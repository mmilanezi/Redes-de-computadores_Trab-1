#!/usr/bin/env python3
"""
Gerador da topologia do Trabalho I - Redes de Computadores (UNISINOS 2026/2).

Fonte unica de verdade da topologia. A partir da tabela TOPOLOGIA abaixo este
script emite:

  * docker-compose.yml          -- topologia fisica (containers, enlaces, IPs)
  * configs/<rX>.<proto>.conf   -- topologia logica (RIP, OSPF e BGP)

Uso:  python scripts/gerar_topologia.py
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIR_CONFIGS = RAIZ / "configs"

# ---------------------------------------------------------------------------
# Topologia: anel de 5 roteadores distribuidos em 3 Sistemas Autonomos.
#
#                       AS 65001              AS 65002
#                    +----------+          +----------+
#            .-------|    R1    |----------|    R2    |------.
#            |       +----------+  .12.0/29 +----------+      |
#            |                                                | .23.0/29
#   .15.0/29 |                                          +----------+
#            |                                          |    R3    |
#            |       +----------+          +----------+ +----------+
#            '-------|    R5    |----------|    R4    |------'
#                    +----------+  .45.0/29 +----------+  .34.0/29
#                     AS 65003              AS 65002
#
# O anel garante dois caminhos disjuntos entre quaisquer dois pontos, o que
# permite observar reconvergencia e diferencas de selecao de rota entre os
# protocolos.
# ---------------------------------------------------------------------------

# (nome, ASN, id do roteador == loopback)
ROTEADORES = {
    "r1": {"asn": 65001, "lo": "10.255.0.1"},
    "r2": {"asn": 65001, "lo": "10.255.0.2"},
    "r3": {"asn": 65002, "lo": "10.255.0.3"},
    "r4": {"asn": 65002, "lo": "10.255.0.4"},
    "r5": {"asn": 65003, "lo": "10.255.0.5"},
}

# Enlaces ponto-a-ponto. Cada enlace vira uma rede bridge dedicada no Docker.
# O prefixo /29 e usado em vez de /30 porque o Docker reserva um endereco do
# bridge como gateway (alocado no ultimo endereco utilizavel, .6).
ENLACES = [
    # (nome, prefixo, roteador_a, ip_a, roteador_b, ip_b)
    ("link12", "172.16.12.0/29", "r1", "172.16.12.1", "r2", "172.16.12.2"),
    ("link23", "172.16.23.0/29", "r2", "172.16.23.2", "r3", "172.16.23.3"),
    ("link34", "172.16.34.0/29", "r3", "172.16.34.3", "r4", "172.16.34.4"),
    ("link45", "172.16.45.0/29", "r4", "172.16.45.4", "r5", "172.16.45.5"),
    ("link15", "172.16.15.0/29", "r5", "172.16.15.5", "r1", "172.16.15.1"),
]

# Rede de acesso associada a cada roteador.
LANS = {
    "r1": {"nome": "lan1", "prefixo": "10.1.0.0/24", "rtr": "10.1.0.1", "host": "10.1.0.10"},
    "r2": {"nome": "lan2", "prefixo": "10.2.0.0/24", "rtr": "10.2.0.1", "host": "10.2.0.10"},
    "r3": {"nome": "lan3", "prefixo": "10.3.0.0/24", "rtr": "10.3.0.1", "host": "10.3.0.10"},
    "r4": {"nome": "lan4", "prefixo": "10.4.0.0/24", "rtr": "10.4.0.1", "host": "10.4.0.10"},
    "r5": {"nome": "lan5", "prefixo": "10.5.0.0/24", "rtr": "10.5.0.1", "host": "10.5.0.10"},
}

# Custo OSPF por interface. O enlace R2--R3 recebe custo alto de proposito:
# e o caminho mais curto em numero de saltos, mas o mais caro em custo OSPF.
# Isso faz RIP (saltos) e OSPF (custo) escolherem caminhos DIFERENTES para o
# mesmo destino, que e o fenomeno central da analise comparativa.
CUSTO_OSPF_PADRAO = 10
CUSTO_OSPF = {
    ("r2", "to-r3"): 200,
    ("r3", "to-r2"): 200,
}

# Timers deixados nos valores padrao de cada protocolo, para que a medicao de
# trafego de controle reflita o comportamento nativo e nao um ajuste artificial.
RIP_TIMERS = "30 180 120"          # update / timeout / garbage-collect
BGP_TIMERS = "60 180"              # keepalive / hold


# ---------------------------------------------------------------------------
# Derivacao
# ---------------------------------------------------------------------------

def vizinhos_de(rtr: str):
    """Lista (nome_if, ip_local, prefixo, rtr_peer, ip_peer) do roteador."""
    saida = []
    for _nome, prefixo, ra, ipa, rb, ipb in ENLACES:
        if ra == rtr:
            saida.append((f"to-{rb}", ipa, prefixo, rb, ipb))
        elif rb == rtr:
            saida.append((f"to-{ra}", ipb, prefixo, ra, ipa))
    return sorted(saida)


def redes_de(rtr: str):
    """Todos os prefixos conectados ao roteador (LAN, enlaces e loopback)."""
    redes = [LANS[rtr]["prefixo"]]
    redes += [p for _if, _ip, p, _rp, _ripp in vizinhos_de(rtr)]
    redes.append(f"{ROTEADORES[rtr]['lo']}/32")
    return redes


def gateway_docker(prefixo: str) -> str:
    """Docker exige um gateway na bridge; usamos o ultimo endereco utilizavel."""
    rede = ipaddress.ip_network(prefixo)
    return str(rede.broadcast_address - 1)


def ifmap_de(rtr: str) -> str:
    """Mapa IP=nome_interface consumido pelo entrypoint do container."""
    itens = [f"{LANS[rtr]['rtr']}=lan"]
    itens += [f"{ip}={nome}" for nome, ip, _p, _rp, _ripp in vizinhos_de(rtr)]
    return " ".join(itens)


# ---------------------------------------------------------------------------
# Cabecalho comum das configuracoes FRR
# ---------------------------------------------------------------------------

def cabecalho(rtr: str, proto: str) -> list[str]:
    return [
        "! ===========================================================================",
        f"! {rtr.upper()} - protocolo ativo: {proto.upper()}",
        f"! Sistema Autonomo: AS {ROTEADORES[rtr]['asn']}",
        "! Gerado por scripts/gerar_topologia.py  .",
        "! ===========================================================================",
        "frr version 8.4",
        "frr defaults traditional",
        f"hostname {rtr}",
        "log stdout",
        "no ipv6 forwarding",
        "service integrated-vtysh-config",
        "!",
        "interface lo",
        f" ip address {ROTEADORES[rtr]['lo']}/32",
        "!",
    ]


# ---------------------------------------------------------------------------
# RIP v2 -- vetor de distancia, metrica = numero de saltos
# ---------------------------------------------------------------------------

def config_rip(rtr: str) -> str:
    linhas = cabecalho(rtr, "rip")
    linhas += [
        "router rip",
        " version 2",
    ]
    for rede in redes_de(rtr):
        linhas.append(f" network {rede}")
    linhas += [
        " ! a LAN e o loopback nao possuem vizinhos RIP: nao ha por que",
        " ! inundar as redes de acesso com updates de roteamento.",
        " passive-interface lan",
        " passive-interface lo",
        f" timers basic {RIP_TIMERS}",
        "!",
        "line vty",
        "!",
    ]
    return "\n".join(linhas) + "\n"


# ---------------------------------------------------------------------------
# OSPFv2 -- estado de enlace, metrica = custo acumulado, area unica
# ---------------------------------------------------------------------------

def config_ospf(rtr: str) -> str:
    linhas = cabecalho(rtr, "ospf")
    for nome_if, _ip, _p, _rp, _ripp in vizinhos_de(rtr):
        custo = CUSTO_OSPF.get((rtr, nome_if), CUSTO_OSPF_PADRAO)
        linhas += [
            f"interface {nome_if}",
            f" ip ospf cost {custo}",
            "!",
        ]
    linhas += [
        "router ospf",
        f" ospf router-id {ROTEADORES[rtr]['lo']}",
    ]
    for rede in redes_de(rtr):
        linhas.append(f" network {rede} area 0")
    linhas += [
        " passive-interface lan",
        " passive-interface lo",
        "!",
        "line vty",
        "!",
    ]
    return "\n".join(linhas) + "\n"


# ---------------------------------------------------------------------------
# BGP-4 -- vetor de caminho; eBGP entre ASes, iBGP dentro de cada AS
# ---------------------------------------------------------------------------

def config_bgp(rtr: str) -> str:
    asn = ROTEADORES[rtr]["asn"]
    linhas = cabecalho(rtr, "bgp")
    linhas += [
        f"router bgp {asn}",
        f" bgp router-id {ROTEADORES[rtr]['lo']}",
        " ! RFC 8212 exige politica explicita em sessoes eBGP; em laboratorio",
        " ! desativamos a exigencia para observar o protocolo sem filtros.",
        " no bgp ebgp-requires-policy",
        " no bgp default ipv4-unicast",
        f" timers bgp {BGP_TIMERS}",
    ]

    sessoes = []
    for nome_if, _ip, _p, rtr_peer, ip_peer in vizinhos_de(rtr):
        asn_peer = ROTEADORES[rtr_peer]["asn"]
        tipo = "iBGP" if asn_peer == asn else "eBGP"
        linhas += [
            f" ! {tipo} com {rtr_peer.upper()} (AS {asn_peer}) via {nome_if}",
            f" neighbor {ip_peer} remote-as {asn_peer}",
            f" neighbor {ip_peer} description {tipo}-{rtr_peer.upper()}-AS{asn_peer}",
        ]
        sessoes.append((ip_peer, tipo))

    linhas += [
        " !",
        " address-family ipv4 unicast",
        f"  ! anuncia a rede de acesso e o loopback deste roteador",
        f"  network {LANS[rtr]['prefixo']}",
        f"  network {ROTEADORES[rtr]['lo']}/32",
    ]
    for ip_peer, tipo in sessoes:
        linhas.append(f"  neighbor {ip_peer} activate")
        if tipo == "iBGP":
            # Sem IGP neste cenario, o next-hop aprendido via eBGP nao seria
            # alcancavel pelo par iBGP; next-hop-self resolve a recursao.
            linhas.append(f"  neighbor {ip_peer} next-hop-self")
    linhas += [
        " exit-address-family",
        "!",
        "line vty",
        "!",
    ]
    return "\n".join(linhas) + "\n"


GERADORES = {"rip": config_rip, "ospf": config_ospf, "bgp": config_bgp}


# ---------------------------------------------------------------------------
# docker-compose.yml -- topologia fisica
# ---------------------------------------------------------------------------

def gerar_compose() -> str:
    L: list[str] = []
    add = L.append

    add("# ===========================================================================")
    add("# Topologia fisica do Trabalho I - Redes de Computadores (UNISINOS 2026/2)")
    add("#")
    add("# 5 roteadores FRRouting em anel, 3 Sistemas Autonomos, 5 redes de acesso.")
    add("# Cada enlace ponto-a-ponto e uma rede bridge dedicada, de modo que o")
    add("# encaminhamento entre LANs dependa exclusivamente do protocolo em estudo.")
    add("#")
    add("# ARQUIVO GERADO por scripts/gerar_topologia.py  .")
    add("#")
    add("# O protocolo ativo vem da variavel de ambiente PROTO (rip|ospf|bgp).")
    add("# Use os scripts de orquestracao em vez de chamar o compose diretamente.")
    add("# ===========================================================================")
    add("")
    add("services:")

    for rtr in ROTEADORES:
        lan = LANS[rtr]
        add(f"  {rtr}:")
        add("    build:")
        add("      context: ./docker")
        add("      dockerfile: Dockerfile.router")
        add("    image: trabalho1-frr:8.4.1")
        add(f"    container_name: {rtr}")
        add(f"    hostname: {rtr}")
        add("    environment:")
        add(f"      ROUTER: {rtr}")
        add("      PROTO: ${PROTO:?defina PROTO=rip|ospf|bgp}")
        add(f'      IFMAP: "{ifmap_de(rtr)}"')
        add(f"      ASN: \"{ROTEADORES[rtr]['asn']}\"")
        add("    volumes:")
        add("      - ./configs:/etc/frr/configs:ro")
        add("    cap_add:")
        add("      - NET_ADMIN")
        add("      - SYS_ADMIN")
        add("      - NET_RAW")
        add("      - NET_BIND_SERVICE")
        add("    sysctls:")
        add("      net.ipv4.ip_forward: 1")
        add("      # caminhos assimetricos sao esperados em um anel: rp_filter")
        add("      # descartaria pacotes legitimos durante a reconvergencia.")
        add("      net.ipv4.conf.all.rp_filter: 0")
        add("      net.ipv4.conf.default.rp_filter: 0")
        add("    networks:")
        add(f"      {lan['nome']}:")
        add(f"        ipv4_address: {lan['rtr']}")
        for nome_enlace, _p, ra, ipa, rb, ipb in ENLACES:
            if ra == rtr:
                add(f"      {nome_enlace}:")
                add(f"        ipv4_address: {ipa}")
            elif rb == rtr:
                add(f"      {nome_enlace}:")
                add(f"        ipv4_address: {ipb}")
        add("    restart: \"no\"")
        add("")

    for rtr, lan in LANS.items():
        nome_host = "h" + rtr[1:]
        add(f"  {nome_host}:")
        add("    image: nicolaka/netshoot:latest")
        add(f"    container_name: {nome_host}")
        add(f"    hostname: {nome_host}")
        add("    depends_on:")
        add(f"      - {rtr}")
        add("    cap_add:")
        add("      - NET_ADMIN")
        add("      - NET_RAW")
        add("    # substitui a rota default do bridge Docker pelo roteador da LAN")
        add('    command: >')
        add('      sh -c "')
        add('        while ip route show default | grep -q .; do ip route del default; done;')
        add(f'        ip route add default via {lan["rtr"]};')
        add('        sleep infinity"')
        add("    networks:")
        add(f"      {lan['nome']}:")
        add(f"        ipv4_address: {lan['host']}")
        add("    restart: \"no\"")
        add("")

    add("networks:")
    redes = [(lan["nome"], lan["prefixo"]) for lan in LANS.values()]
    redes += [(nome, prefixo) for nome, prefixo, *_ in ENLACES]
    for nome, prefixo in redes:
        add(f"  {nome}:")
        add("    driver: bridge")
        add("    # internal: sem NAT nem saida para a Internet -- o laboratorio")
        add("    # e fechado e nenhuma rota externa pode influenciar a medicao.")
        add("    internal: true")
        add("    ipam:")
        add("      config:")
        add(f"        - subnet: {prefixo}")
        add(f"          gateway: {gateway_docker(prefixo)}")
        add("")

    return "\n".join(L)


# ---------------------------------------------------------------------------

def main() -> None:
    DIR_CONFIGS.mkdir(exist_ok=True)

    gerados = 0
    for rtr in ROTEADORES:
        for proto, gerador in GERADORES.items():
            destino = DIR_CONFIGS / f"{rtr}.{proto}.conf"
            destino.write_text(gerador(rtr), encoding="utf-8", newline="\n")
            gerados += 1
    print(f"configs/: {gerados} arquivos de configuracao FRR gerados")

    compose = RAIZ / "docker-compose.yml"
    compose.write_text(gerar_compose(), encoding="utf-8", newline="\n")
    print(f"{compose.name}: topologia fisica gerada "
          f"({len(ROTEADORES)} roteadores, {len(LANS)} hosts, "
          f"{len(ENLACES) + len(LANS)} redes)")


if __name__ == "__main__":
    main()
