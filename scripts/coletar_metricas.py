#!/usr/bin/env python3
"""
Coleta automatizada das metricas de roteamento -- Trabalho I - GA
Redes de Computadores, UNISINOS 2026/2 -- Matheus Milanezi

Executa, para cada protocolo (RIP, OSPF e BGP), o mesmo roteiro de
experimentos sobre a MESMA topologia fisica, e grava os resultados brutos em
resultados/metricas.json e em arquivos CSV.

Experimentos:
  E1  Convergencia a frio  -- tempo entre a subida dos daemons e o instante em
                              que todos os roteadores conhecem todas as LANs.
  E2  Tamanho da tabela    -- numero de rotas instaladas em cada roteador.
  E3  Selecao de rota      -- caminho escolhido de R1 ate a LAN de R3, com o
                              numero de saltos efetivamente percorrido.
  E4  Trafego de controle  -- pacotes e bytes de controle transmitidos pela
                              rede em regime permanente (janela fixa).
  E5  Reconvergencia       -- interrupcao observada pelo trafego de dados apos
                              a falha de um enlace em uso, em dois cenarios:
                              unilateral (so o R1 detecta) e bilateral.
  E6  Latencia fim a fim   -- RTT entre hosts de LANs diferentes.

Uso:
    python scripts/coletar_metricas.py                  # os tres protocolos
    python scripts/coletar_metricas.py --protocolos ospf
    python scripts/coletar_metricas.py --repeticoes 5
    python scripts/coletar_metricas.py --somente-reconvergencia \\
        --protocolos rip --cenarios bilateral           # refaz so o E5
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIR_RESULTADOS = RAIZ / "resultados"
PROJETO = "trabalho1"

ROTEADORES = ["r1", "r2", "r3", "r4", "r5"]
HOSTS = ["h1", "h2", "h3", "h4", "h5"]
LANS = ["10.1.0.0/24", "10.2.0.0/24", "10.3.0.0/24", "10.4.0.0/24", "10.5.0.0/24"]

# Filtros BPF do trafego de controle de cada protocolo.
FILTRO_BPF = {
    "rip": "udp port 520",
    "ospf": "ip proto 89",
    "bgp": "tcp port 179",
}

# E5: o enlace R1--R5 e usado pelos tres protocolos para alcancar a LAN de R5
FALHA_ROTEADOR = "r1"
FALHA_INTERFACE = "to-r5"
FALHA_PAR_ROTEADOR = "r5"     # a outra ponta, derrubada so no cenario bilateral
FALHA_PAR_INTERFACE = "to-r1"
FALHA_DESTINO = "10.5.0.10"   # h5
FALHA_ORIGEM = "h1"

# Janela de enlace caido de cada cenario. Na falha unilateral o R5 so descobre
# a queda quando vence o timeout do RIP ou o hold timer do BGP (ambos 180 s);
# a janela precisa passar disso com folga, ou o que se mede e a propria janela.
JANELA_CAIDO = {"unilateral": 270, "bilateral": 60}

# E3: destino usado para evidenciar a divergencia de selecao de rota.
DESTINO_ANALISE = "10.3.0.0/24"
DESTINO_ANALISE_HOST = "10.3.0.10"


# ===========================================================================
# Utilitarios de execucao
# ===========================================================================

def sh(cmd: list[str], *, check: bool = True, timeout: int = 120) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"falhou: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def dexec(container: str, *args: str, check: bool = True, timeout: int = 120) -> str:
    return sh(["docker", "exec", container, *args], check=check, timeout=timeout)


def vtysh(rtr: str, comando: str, *, check: bool = True) -> str:
    return dexec(rtr, "vtysh", "-c", comando, check=check)


def vtysh_json(rtr: str, comando: str) -> dict:
    saida = vtysh(rtr, comando + " json", check=False)
    inicio = saida.find("{")
    if inicio < 0:
        return {}
    try:
        return json.loads(saida[inicio:])
    except json.JSONDecodeError:
        return {}


def relogio_lab() -> float:
    """
    Relogio do host Docker.
    """
    return float(dexec("r1", "date", "+%s.%N").strip())


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ===========================================================================
# Ciclo de vida da topologia
# ===========================================================================

def subir_topologia(proto: str) -> None:
    log(f"subindo a topologia com {proto.upper()}")
    env_down = {"PROTO": proto}
    subprocess.run(
        ["docker", "compose", "-p", PROJETO, "down", "--remove-orphans"],
        capture_output=True, text=True, cwd=RAIZ, env={**_env(), **env_down},
    )
    r = subprocess.run(
        ["docker", "compose", "-p", PROJETO, "up", "-d"],
        capture_output=True, text=True, cwd=RAIZ, env={**_env(), **env_down},
        timeout=600,
    )
    if r.returncode != 0:
        raise RuntimeError(f"docker compose up falhou:\n{r.stdout}\n{r.stderr}")
    preparar_host()


def derrubar_topologia(proto: str) -> None:
    subprocess.run(
        ["docker", "compose", "-p", PROJETO, "down", "--remove-orphans"],
        capture_output=True, text=True, cwd=RAIZ, env={**_env(), "PROTO": proto},
    )


def _env() -> dict:
    import os
    return dict(os.environ)


def preparar_host() -> None:
    ids = []
    for rede in ["lan1", "lan2", "lan3", "lan4", "lan5",
                 "link12", "link23", "link34", "link45", "link15"]:
        out = sh(["docker", "network", "inspect", f"{PROJETO}_{rede}",
                  "--format", "{{.Id}}"], check=False).strip()
        if out:
            ids.append(out[:12])
    sh([
        "docker", "run", "--rm", "--privileged", "--net=host", "--pid=host",
        "-e", f"IDS={' '.join(ids)}", "alpine", "sh", "-c",
        'echo 0 > /proc/sys/net/bridge/bridge-nf-call-iptables; '
        'for i in $IDS; do f=/sys/class/net/br-$i/bridge/multicast_snooping; '
        '[ -f "$f" ] && echo 0 > "$f"; done; true',
    ], timeout=180)


# ===========================================================================
# E1 -- convergencia a frio
# ===========================================================================

def rotas_lan_conhecidas(rtr: str) -> int:
    """Quantas das 5 redes de acesso este roteador ja tem instaladas."""
    tabela = vtysh_json(rtr, "show ip route")
    if not tabela:
        return 0
    return sum(1 for lan in LANS if lan in tabela)


def medir_convergencia_fria(timeout_s: float) -> dict:
    """
    t0 = instante em que o ultimo roteador terminou de subir (marcado pelo
    entrypoint). Mede-se ate que todos conhecam todas as LANs.
    """
    t0 = 0.0
    for rtr in ROTEADORES:
        for _ in range(60):
            saida = dexec(rtr, "cat", "/tmp/boot_ts", check=False).strip()
            if saida:
                t0 = max(t0, float(saida))
                break
            time.sleep(1.0)

    limite = time.time() + timeout_s
    historico = []
    while time.time() < limite:
        total = sum(rotas_lan_conhecidas(r) for r in ROTEADORES)
        historico.append((relogio_lab() - t0, total))
        if total == len(ROTEADORES) * len(LANS):
            return {
                "convergiu": True,
                "segundos": round(relogio_lab() - t0, 2),
                "curva": [[round(t, 2), n] for t, n in historico],
            }
        time.sleep(1.0)

    return {
        "convergiu": False,
        "segundos": None,
        "rotas_faltando": len(ROTEADORES) * len(LANS) - historico[-1][1],
        "curva": [[round(t, 2), n] for t, n in historico],
    }


# ===========================================================================
# E2 -- tamanho da tabela de roteamento
# ===========================================================================

RE_LINHA_LSA = re.compile(r"^\d+\.\d+\.\d+\.\d+\s+\d+\.\d+\.\d+\.\d+\s+\d+\s+0x")


def tamanho_base_protocolo(rtr: str, proto: str) -> int:
    """
    Quantas entradas o protocolo mantem na propria base, alem da FIB:
      RIP  -- rotas da tabela RIP (uma por prefixo: o RIP so guarda a melhor)
      OSPF -- LSAs na LSDB (a descricao da topologia, nao das rotas)
      BGP  -- caminhos conhecidos (Loc-RIB: varios por prefixo)
    """
    if proto == "rip":
        saida = vtysh(rtr, "show ip rip", check=False)
        return sum(1 for l in saida.splitlines() if re.match(r"^[RC]\(", l))
    if proto == "ospf":
        saida = vtysh(rtr, "show ip ospf database", check=False)
        return sum(1 for l in saida.splitlines() if RE_LINHA_LSA.match(l))
    if proto == "bgp":
        rotas = vtysh_json(rtr, "show ip bgp").get("routes", {})
        return sum(len(caminhos) for caminhos in rotas.values())
    return 0


def medir_tabelas(proto: str) -> dict:
    """
    Tres medidas por roteador:
      fib             -- rotas instaladas, as que efetivamente encaminham
      rib             -- entradas na RIB do zebra (inclui candidatas nao
                         instaladas, ex.: rota OSPF para rede ja conectada)
      base_protocolo  -- ver tamanho_base_protocolo()
    Contar a RIB como "tamanho da tabela" superestima o OSPF: ele registra
    entradas para as redes diretamente conectadas, que perdem para a rota
    conectada e nunca sao instaladas.
    """
    por_roteador = {}
    for rtr in ROTEADORES:
        tabela = vtysh_json(rtr, "show ip route")
        rib = fib = do_protocolo = 0
        for _prefixo, entradas in tabela.items():
            for e in entradas:
                rib += 1
                if e.get("installed"):
                    fib += 1
                if e.get("protocol") == proto:
                    do_protocolo += 1
        por_roteador[rtr] = {
            "fib": fib,
            "rib": rib,
            "aprendidas_pelo_protocolo": do_protocolo,
            "base_protocolo": tamanho_base_protocolo(rtr, proto),
        }

    def media(campo: str) -> float:
        return round(statistics.mean(v[campo] for v in por_roteador.values()), 2)

    return {
        "por_roteador": por_roteador,
        "media_fib": media("fib"),
        "media_rib": media("rib"),
        "media_base_protocolo": media("base_protocolo"),
        # compatibilidade com a primeira coleta, que so media a RIB
        "media_total": media("rib"),
    }


# ===========================================================================
# E3 -- selecao de rota
# ===========================================================================

def medir_selecao_rota() -> dict:
    tabela = vtysh_json("r1", f"show ip route {DESTINO_ANALISE}")
    entrada = (tabela.get(DESTINO_ANALISE) or [{}])[0]
    saltos = entrada.get("nexthops", [{}])
    caminho = traceroute(FALHA_ORIGEM, DESTINO_ANALISE_HOST)
    return {
        "prefixo": DESTINO_ANALISE,
        "protocolo": entrada.get("protocol"),
        "metrica": entrada.get("metric"),
        "distancia_administrativa": entrada.get("distance"),
        "proximo_salto": saltos[0].get("ip") if saltos else None,
        "interface_saida": saltos[0].get("interfaceName") if saltos else None,
        "traceroute": caminho,
        "saltos_roteadores": max(0, len(caminho) - 1),
        "saltos_percorridos": len(caminho),
    }


def traceroute(host: str, destino: str) -> list[str]:
    saida = dexec(host, "traceroute", "-n", "-w", "1", "-q", "1", "-m", "12",
                  destino, check=False, timeout=60)
    saltos = []
    for linha in saida.splitlines()[1:]:
        m = re.match(r"\s*\d+\s+(\d+\.\d+\.\d+\.\d+)", linha)
        if m:
            saltos.append(m.group(1))
    return saltos


# ===========================================================================
# E4 -- trafego de controle em regime permanente
# ===========================================================================

def ler_pcap(caminho: Path) -> tuple[int, int]:
    """
    Le um arquivo pcap classico e devolve (pacotes, bytes_na_rede).

    Formato: cabecalho global de 24 bytes, seguido de registros com cabecalho 
    de 16 bytes (ts_s, ts_us, incl_len, orig_len).
    Soma-se orig_len, o tamanho real do quadro na rede.
    """
    dados = caminho.read_bytes()
    if len(dados) < 24:
        return 0, 0

    magia = dados[:4]
    if magia in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        ordem = "<"
    elif magia in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        ordem = ">"
    else:
        return 0, 0

    pos, pacotes, total_bytes = 24, 0, 0
    while pos + 16 <= len(dados):
        _ts_s, _ts_f, incl_len, orig_len = struct.unpack(
            ordem + "IIII", dados[pos:pos + 16])
        pos += 16 + incl_len
        if pos > len(dados):
            break
        pacotes += 1
        total_bytes += orig_len
    return pacotes, total_bytes


def medir_trafego_controle(proto: str, janela_s: int, dir_saida: Path) -> dict:
    """
    Captura apenas os pacotes transmitidos (-Q out) por cada roteador.

    Capturar tambem a recepcao contaria cada pacote duas vezes (no emissor e
    no receptor). Restringindo a saida, a soma sobre os cinco roteadores e
    exatamente o volume de controle injetado na rede.
    """
    filtro = FILTRO_BPF[proto]
    log(f"capturando {janela_s}s de trafego de controle ({filtro})")

    for rtr in ROTEADORES:
        subprocess.Popen(
            ["docker", "exec", rtr, "sh", "-c",
             f"timeout {janela_s} tcpdump -i any -Q out -U -s 128 "
             f"-w /tmp/ctrl.pcap {filtro} 2>/dev/null"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    time.sleep(janela_s + 4)

    por_roteador = {}
    dir_pcap = dir_saida / "pcap"
    dir_pcap.mkdir(parents=True, exist_ok=True)
    for rtr in ROTEADORES:
        destino = dir_pcap / f"{proto}_{rtr}.pcap"
        sh(["docker", "cp", f"{rtr}:/tmp/ctrl.pcap", str(destino)], check=False)
        pacotes, bytes_ = ler_pcap(destino) if destino.exists() else (0, 0)
        por_roteador[rtr] = {
            "pacotes": pacotes,
            "bytes": bytes_,
            "pacotes_por_s": round(pacotes / janela_s, 3),
            "bits_por_s": round(bytes_ * 8 / janela_s, 1),
        }

    total_pkt = sum(v["pacotes"] for v in por_roteador.values())
    total_bytes = sum(v["bytes"] for v in por_roteador.values())
    return {
        "filtro_bpf": filtro,
        "janela_s": janela_s,
        "por_roteador": por_roteador,
        "total_pacotes": total_pkt,
        "total_bytes": total_bytes,
        "pacotes_por_s": round(total_pkt / janela_s, 3),
        "bits_por_s": round(total_bytes * 8 / janela_s, 1),
    }


# ===========================================================================
# E5 -- reconvergencia apos falha de enlace
# ===========================================================================

def _parse_ping_D(saida: str) -> list[tuple[float, int]]:
    """Extrai (timestamp, seq) de cada resposta de 'ping -D'."""
    respostas = []
    for linha in saida.splitlines():
        m = re.match(r"\[(\d+\.\d+)\].*icmp_seq=(\d+)", linha)
        if m:
            respostas.append((float(m.group(1)), int(m.group(2))))
    return respostas


def _maior_lacuna(respostas: list[tuple[float, int]],
                  inicio: float, fim: float) -> tuple[float, int]:
    """
    Maior intervalo sem resposta dentro da janela, e quantos pacotes se
    perderam nele (pela numeracao ICMP, nao por estimativa temporal).

    O comando que derruba o enlace leva algumas centenas de milissegundos 
    para chegar ao container, de modo que o trafego ainda flui normalmente 
    logo apos a marcacao de tempo. Medir "a primeira resposta depois 
    do instante T" devolveria sempre um intervalo de ping, e nenhuma 
    interrupcao seria detectada.
    """
    maior, perdidos = 0.0, 0
    for (t1, s1), (t2, s2) in zip(respostas, respostas[1:]):
        if t1 < inicio or t1 > fim:
            continue
        lacuna = t2 - t1
        if lacuna > maior:
            maior, perdidos = lacuna, max(0, s2 - s1 - 1)
    return maior, perdidos


def _mudar_enlace(estado: str, bilateral: bool) -> None:
    dexec(FALHA_ROTEADOR, "ip", "link", "set", "dev", FALHA_INTERFACE, estado)
    if bilateral:
        dexec(FALHA_PAR_ROTEADOR, "ip", "link", "set", "dev",
              FALHA_PAR_INTERFACE, estado)


def medir_reconvergencia(repeticao: int, intervalo: float, janela_caido_s: int,
                         bilateral: bool,
                         guardar_linha_do_tempo: bool = False) -> dict:
    """
    Dois cenarios de falha:
      unilateral -> so a interface do R1 cai. O R5 continua com a interface
                    ativa e nao percebe nada: so descobre a falha pelo
                    protocolo (timeout do RIP, hold timer do BGP, LSA do OSPF).
      bilateral  -> as duas pontas caem, como um cabo cortado: os dois
                    roteadores detectam a perda de enlace imediatamente.

    """
    espera_falha, espera_final = 10, 20
    duracao_s = espera_falha + janela_caido_s + espera_final
    total_pings = int(duracao_s / intervalo)
    espera_volta = janela_caido_s

    proc = subprocess.Popen(
        ["docker", "exec", FALHA_ORIGEM, "ping", "-D", "-n",
         "-i", str(intervalo), "-W", "1", "-c", str(total_pings), FALHA_DESTINO],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )

    time.sleep(espera_falha)
    ts_falha = relogio_lab()
    _mudar_enlace("down", bilateral)
    log(f"  rep {repeticao}: enlace R1-R5 derrubado "
        f"({'bilateral' if bilateral else 'unilateral'})")

    time.sleep(espera_volta)
    ts_volta = relogio_lab()
    _mudar_enlace("up", bilateral)
    log(f"  rep {repeticao}: enlace restabelecido")

    try:
        saida, _ = proc.communicate(timeout=duracao_s + 90)
    except subprocess.TimeoutExpired:
        proc.kill()
        saida, _ = proc.communicate()

    respostas = _parse_ping_D(saida)
    if len(respostas) < 2:
        return {"repeticao": repeticao, "erro": "ping sem respostas suficientes"}

    # margem de 2 s para absorver a latencia do 'docker exec' e a resolucao
    # de 1 s do relogio do host
    queda_s, queda_perdidos = _maior_lacuna(respostas, ts_falha - 2, ts_volta)
    volta_s, volta_perdidos = _maior_lacuna(respostas, ts_volta - 2,
                                            respostas[-1][0])

    # a interrupcao util desconta um intervalo de ping: mesmo sem falha
    # alguma, duas respostas consecutivas distam um intervalo.
    liquida = max(0.0, queda_s - intervalo)

    resultado = {
        "repeticao": repeticao,
        "interrupcao_apos_falha_s": round(liquida, 3),
        "maior_lacuna_bruta_s": round(queda_s, 3),
        "pacotes_perdidos": queda_perdidos,
        "perturbacao_apos_retorno_s": round(max(0.0, volta_s - intervalo), 3),
        "pacotes_perdidos_no_retorno": volta_perdidos,
        "respostas_recebidas": len(respostas),
        "pings_enviados": total_pings,
        "perda_total_pct": round(100 * (1 - len(respostas) / total_pings), 2),
        "janela_enlace_caido_s": espera_volta,
        "convergiu_dentro_da_janela": liquida < espera_volta - 2,
    }
    if guardar_linha_do_tempo:
        t0 = ts_falha
        resultado["linha_do_tempo"] = [[round(t - t0, 3), s]
                                       for t, s in respostas]
    return resultado


# ===========================================================================
# E6 -- latencia fim a fim
# ===========================================================================

def medir_rtt(origem: str, destino: str, n: int = 30) -> dict:
    saida = dexec(origem, "ping", "-n", "-c", str(n), "-i", "0.2", "-W", "2",
                  destino, check=False, timeout=120)
    m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", saida)
    perda = re.search(r"(\d+)% packet loss", saida)
    if not m:
        return {"erro": "ping sem estatisticas", "saida": saida[-300:]}
    return {
        "origem": origem, "destino": destino, "amostras": n,
        "rtt_min_ms": float(m.group(1)),
        "rtt_med_ms": float(m.group(2)),
        "rtt_max_ms": float(m.group(3)),
        "rtt_desvio_ms": float(m.group(4)),
        "perda_pct": float(perda.group(1)) if perda else None,
    }


# ===========================================================================
# Roteiro por protocolo
# ===========================================================================

def executar(proto: str, args) -> dict:
    log(f"===== {proto.upper()} =====")
    subir_topologia(proto)

    r: dict = {"protocolo": proto,
               "inicio": datetime.now(timezone.utc).isoformat()}

    log("E1 convergencia a frio")
    r["e1_convergencia_fria"] = medir_convergencia_fria(args.timeout_convergencia)
    if not r["e1_convergencia_fria"]["convergiu"]:
        log(f"  AVISO: {proto.upper()} nao convergiu em "
            f"{args.timeout_convergencia}s")
    else:
        log(f"  convergiu em {r['e1_convergencia_fria']['segundos']}s")

    log("E2 tamanho das tabelas de roteamento")
    r["e2_tabelas"] = medir_tabelas(proto)
    log(f"  FIB {r['e2_tabelas']['media_fib']} | RIB {r['e2_tabelas']['media_rib']}"
        f" | base {r['e2_tabelas']['media_base_protocolo']}")

    log("E3 selecao de rota")
    r["e3_selecao_rota"] = medir_selecao_rota()
    log(f"  R1 -> {DESTINO_ANALISE} via "
        f"{r['e3_selecao_rota']['interface_saida']} "
        f"({r['e3_selecao_rota']['saltos_percorridos']} saltos)")

    log("E6 latencia fim a fim")
    r["e6_rtt"] = {
        "h1_h3": medir_rtt("h1", DESTINO_ANALISE_HOST),
        "h1_h5": medir_rtt("h1", FALHA_DESTINO),
    }

    log("E4 trafego de controle")
    r["e4_trafego_controle"] = medir_trafego_controle(
        proto, args.janela_captura, DIR_RESULTADOS)
    log(f"  {r['e4_trafego_controle']['pacotes_por_s']} pacotes/s, "
        f"{r['e4_trafego_controle']['bits_por_s']} bit/s")

    for cenario in args.cenarios:
        r[CHAVE_E5[cenario]] = rodar_reconvergencia(cenario, args)

    r["fim"] = datetime.now(timezone.utc).isoformat()
    return r


CHAVE_E5 = {"unilateral": "e5_reconvergencia",
            "bilateral": "e5b_reconvergencia_bilateral"}


def rodar_reconvergencia(cenario: str, args) -> dict:
    bilateral = cenario == "bilateral"
    janela = JANELA_CAIDO[cenario]
    log(f"E5 reconvergencia -- falha {cenario}, enlace caido por {janela}s")
    rodadas = []
    for i in range(1, args.repeticoes + 1):
        # 50 pacotes/s: resolucao de 20 ms na medida da interrupcao.
        rodadas.append(medir_reconvergencia(i, 0.02, janela, bilateral,
                                            guardar_linha_do_tempo=(i == 1)))
        # espera o protocolo voltar ao caminho primario antes da proxima rodada
        time.sleep(args.intervalo_entre_repeticoes)
    validos = [x["interrupcao_apos_falha_s"] for x in rodadas
               if x.get("interrupcao_apos_falha_s") is not None]
    resumo = {
        "cenario": cenario,
        "enlace_derrubado": "r1/to-r5 e r5/to-r1" if bilateral
                            else f"{FALHA_ROTEADOR}/{FALHA_INTERFACE}",
        "janela_enlace_caido_s": janela,
        "fluxo": f"{FALHA_ORIGEM} -> {FALHA_DESTINO}",
        "rodadas": rodadas,
        "rodadas_que_convergiram": sum(1 for x in rodadas
                                       if x.get("convergiu_dentro_da_janela")),
        "media_s": round(statistics.mean(validos), 3) if validos else None,
        "desvio_s": round(statistics.stdev(validos), 3) if len(validos) > 1 else 0.0,
        "min_s": round(min(validos), 3) if validos else None,
        "max_s": round(max(validos), 3) if validos else None,
    }
    log(f"  interrupcao media: {resumo['media_s']}s "
        f"({resumo['rodadas_que_convergiram']}/{len(rodadas)} convergiram "
        f"dentro da janela)")
    return resumo


def somente_tabelas(proto: str, args, resultados: dict) -> None:
    """Refaz apenas o E2 de um protocolo, preservando os demais experimentos."""
    log(f"===== {proto.upper()} (apenas tabelas) =====")
    subir_topologia(proto)
    conv = medir_convergencia_fria(args.timeout_convergencia)
    log(f"  topologia convergida em {conv['segundos']}s")
    # alguns segundos a mais: o E1 so exige as LANs, e os demais prefixos
    # (enlaces, loopbacks) podem chegar no update seguinte
    time.sleep(35)
    r = resultados.setdefault(proto, {"protocolo": proto})
    r["e2_tabelas"] = medir_tabelas(proto)
    log(f"  FIB {r['e2_tabelas']['media_fib']} | RIB {r['e2_tabelas']['media_rib']}"
        f" | base {r['e2_tabelas']['media_base_protocolo']}")


def somente_reconvergencia(proto: str, args, resultados: dict) -> None:
    """Refaz apenas o E5 de um protocolo, preservando os demais experimentos."""
    log(f"===== {proto.upper()} (apenas reconvergencia) =====")
    subir_topologia(proto)
    conv = medir_convergencia_fria(args.timeout_convergencia)
    log(f"  topologia convergida em {conv['segundos']}s")
    r = resultados.setdefault(proto, {"protocolo": proto})
    for cenario in args.cenarios:
        r[CHAVE_E5[cenario]] = rodar_reconvergencia(cenario, args)


# ===========================================================================
# Saida
# ===========================================================================

def gravar_csvs(resultados: dict) -> None:
    import csv

    def e5(r: dict, chave: str, campo: str):
        return (r.get(chave) or {}).get(campo)

    with open(DIR_RESULTADOS / "resumo.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "protocolo", "convergencia_fria_s", "rotas_media",
            "controle_pacotes_por_s", "controle_bits_por_s",
            "reconv_unilateral_media_s", "reconv_unilateral_desvio_s",
            "reconv_bilateral_media_s", "reconv_bilateral_desvio_s",
            "rtt_h1_h3_ms", "roteadores_h1_h3",
        ])
        for proto, r in resultados.items():
            w.writerow([
                proto,
                r["e1_convergencia_fria"]["segundos"],
                r["e2_tabelas"].get("media_fib", r["e2_tabelas"]["media_total"]),
                r["e4_trafego_controle"]["pacotes_por_s"],
                r["e4_trafego_controle"]["bits_por_s"],
                e5(r, "e5_reconvergencia", "media_s"),
                e5(r, "e5_reconvergencia", "desvio_s"),
                e5(r, "e5b_reconvergencia_bilateral", "media_s"),
                e5(r, "e5b_reconvergencia_bilateral", "desvio_s"),
                r["e6_rtt"]["h1_h3"].get("rtt_med_ms"),
                r["e3_selecao_rota"].get("saltos_roteadores"),
            ])

    with open(DIR_RESULTADOS / "tabelas_por_roteador.csv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["protocolo", "roteador", "fib", "rib",
                    "rotas_do_protocolo", "base_protocolo"])
        for proto, r in resultados.items():
            for rtr, v in r["e2_tabelas"]["por_roteador"].items():
                w.writerow([proto, rtr, v.get("fib"), v.get("rib", v.get("total")),
                            v["aprendidas_pelo_protocolo"], v.get("base_protocolo")])

    with open(DIR_RESULTADOS / "controle_por_roteador.csv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["protocolo", "roteador", "pacotes", "bytes",
                    "pacotes_por_s", "bits_por_s"])
        for proto, r in resultados.items():
            for rtr, v in r["e4_trafego_controle"]["por_roteador"].items():
                w.writerow([proto, rtr, v["pacotes"], v["bytes"],
                            v["pacotes_por_s"], v["bits_por_s"]])

    with open(DIR_RESULTADOS / "reconvergencia.csv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["protocolo", "cenario", "janela_caido_s", "repeticao",
                    "interrupcao_s", "convergiu_na_janela", "pacotes_perdidos",
                    "perturbacao_retorno_s"])
        for proto, r in resultados.items():
            for chave in CHAVE_E5.values():
                bloco = r.get(chave)
                if not bloco:
                    continue
                cenario = bloco.get("cenario", "unilateral")
                for rodada in bloco["rodadas"]:
                    w.writerow([proto, cenario,
                                rodada.get("janela_enlace_caido_s"),
                                rodada.get("repeticao"),
                                rodada.get("interrupcao_apos_falha_s"),
                                rodada.get("convergiu_dentro_da_janela"),
                                rodada.get("pacotes_perdidos"),
                                rodada.get("perturbacao_apos_retorno_s")])


def main() -> None:
    p = argparse.ArgumentParser(description="Coleta de metricas de roteamento")
    p.add_argument("--protocolos", nargs="+", default=["rip", "ospf", "bgp"],
                   choices=["rip", "ospf", "bgp"])
    p.add_argument("--repeticoes", type=int, default=3,
                   help="rodadas do teste de reconvergencia (padrao: 3)")
    p.add_argument("--janela-captura", type=int, default=90,
                   help="segundos de captura do trafego de controle")
    p.add_argument("--cenarios", nargs="+", default=["unilateral", "bilateral"],
                   choices=["unilateral", "bilateral"],
                   help="cenarios de falha do E5 (padrao: os dois)")
    p.add_argument("--somente-reconvergencia", action="store_true",
                   help="refaz apenas o E5, mantendo os demais resultados "
                        "ja gravados em resultados/metricas.json")
    p.add_argument("--somente-tabelas", action="store_true",
                   help="refaz apenas o E2, mantendo os demais resultados")
    p.add_argument("--timeout-convergencia", type=int, default=300)
    p.add_argument("--intervalo-entre-repeticoes", type=int, default=30)
    p.add_argument("--manter-topologia", action="store_true",
                   help="nao derrubar a topologia ao final")
    args = p.parse_args()

    DIR_RESULTADOS.mkdir(exist_ok=True)
    destino = DIR_RESULTADOS / "metricas.json"
    resultados = {}
    parcial = args.somente_reconvergencia or args.somente_tabelas
    if parcial and destino.exists():
        resultados = json.loads(destino.read_text(encoding="utf-8"))
    try:
        for proto in args.protocolos:
            if args.somente_tabelas:
                somente_tabelas(proto, args, resultados)
            elif args.somente_reconvergencia:
                somente_reconvergencia(proto, args, resultados)
            else:
                resultados[proto] = executar(proto, args)
            destino.write_text(
                json.dumps(resultados, indent=2, ensure_ascii=False),
                encoding="utf-8")
    finally:
        if resultados:
            gravar_csvs(resultados)
            log(f"resultados gravados em {DIR_RESULTADOS}")
        if not args.manter_topologia and resultados:
            derrubar_topologia(list(resultados)[-1])

    print()
    print(f"{'protocolo':<10} {'reconv. unilateral':>20} {'reconv. bilateral':>19}")
    for proto, r in resultados.items():
        uni = (r.get("e5_reconvergencia") or {}).get("media_s")
        bil = (r.get("e5b_reconvergencia_bilateral") or {}).get("media_s")
        print(f"{proto:<10} {str(uni):>20} {str(bil):>19}")


if __name__ == "__main__":
    sys.exit(main())
