#!/usr/bin/env python3
"""
Geracao dos graficos comparativos -- Trabalho I
Redes de Computadores, UNISINOS 2026/2 -- Matheus Milanezi

Le resultados/metricas.json (produzido por coletar_metricas.py) e grava um PNG
por metrica em resultados/graficos/.

Uso:  python scripts/gerar_graficos.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

RAIZ = Path(__file__).resolve().parent.parent
DIR_RESULTADOS = RAIZ / "resultados"
DIR_GRAFICOS = DIR_RESULTADOS / "graficos"
COR = {"rip": "#2a78d6", "ospf": "#eb6834", "bgp": "#1baf7a"}
ROTULO = {"rip": "RIPv2", "ospf": "OSPFv2", "bgp": "BGP-4"}

SUPERFICIE = "#fcfcfb"
TINTA = "#0b0b0b"
TINTA_2 = "#52514e"
TINTA_MUDA = "#898781"
GRADE = "#e1e0d9"
EIXO = "#c3c2b7"

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
    "font.size": 11,
    "figure.facecolor": SUPERFICIE,
    "axes.facecolor": SUPERFICIE,
    "savefig.facecolor": SUPERFICIE,
    "text.color": TINTA,
    "axes.labelcolor": TINTA_2,
    "xtick.color": TINTA_MUDA,
    "ytick.color": TINTA_MUDA,
})


def preparar_eixos(ax, titulo: str, subtitulo: str = "", ylabel: str = "") -> None:
    """Cromo recessivo: a grade e os eixos nao competem com os dados."""
    ax.set_title(titulo, fontsize=14, fontweight="600", color=TINTA,
                 loc="left", pad=18 if subtitulo else 10)
    if subtitulo:
        ax.text(0, 1.02, subtitulo, transform=ax.transAxes,
                fontsize=10, color=TINTA_MUDA, va="bottom")
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=10, color=TINTA_2)

    ax.set_axisbelow(True)
    ax.yaxis.grid(True, color=GRADE, linewidth=0.8)
    ax.xaxis.grid(False)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        ax.spines[lado].set_color(EIXO)
        ax.spines[lado].set_linewidth(1.0)
    ax.tick_params(length=0)


def rotular_barras(ax, barras, fmt="{:.1f}", dy=0.02) -> None:

    limite = ax.get_ylim()[1]
    for b in barras:
        altura = b.get_height()
        if altura is None:
            continue
        ax.text(b.get_x() + b.get_width() / 2, altura + limite * dy,
                fmt.format(altura).replace(".", ","), ha="center", va="bottom",
                fontsize=10, color=TINTA, fontweight="600")


def salvar(fig, nome: str) -> None:
    DIR_GRAFICOS.mkdir(parents=True, exist_ok=True)
    destino = DIR_GRAFICOS / nome
    fig.savefig(destino, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  {destino.relative_to(RAIZ)}")


# ---------------------------------------------------------------------------
# G1 -- trafego de controle: pacotes/s e bits/s em graficos separados.
# ---------------------------------------------------------------------------

def g1_pacotes_controle(dados: dict) -> None:
    protos = list(dados)
    valores = [dados[p]["e4_trafego_controle"]["pacotes_por_s"] for p in protos]

    fig, ax = plt.subplots(figsize=(7, 4.4))
    preparar_eixos(
        ax, "Pacotes de controle injetados na rede",
        "soma dos cinco roteadores, apenas transmissão, em regime permanente",
        "pacotes por segundo")
    barras = ax.bar([ROTULO[p] for p in protos], valores, width=0.55,
                    color=[COR[p] for p in protos], zorder=3)
    ax.set_ylim(0, max(valores) * 1.22 if max(valores) else 1)
    rotular_barras(ax, barras, "{:.2f}")
    salvar(fig, "g1_pacotes_controle.png")


def g2_taxa_controle(dados: dict) -> None:
    protos = list(dados)
    valores = [dados[p]["e4_trafego_controle"]["bits_por_s"] for p in protos]

    fig, ax = plt.subplots(figsize=(7, 4.4))
    preparar_eixos(
        ax, "Taxa de transmissão ocupada pelo plano de controle",
        "soma dos cinco roteadores, apenas transmissão, em regime permanente",
        "bits por segundo")
    barras = ax.bar([ROTULO[p] for p in protos], valores, width=0.55,
                    color=[COR[p] for p in protos], zorder=3)
    ax.set_ylim(0, max(valores) * 1.22 if max(valores) else 1)
    rotular_barras(ax, barras, "{:.0f}")
    salvar(fig, "g2_taxa_controle.png")


# ---------------------------------------------------------------------------
# G3 -- tamanho da tabela de roteamento, por roteador
# ---------------------------------------------------------------------------

def g3_tabelas(dados: dict) -> None:
    _g_tabelas(dados, "fib", "Tamanho da tabela de roteamento (FIB)",
               "rotas instaladas em cada roteador, as que efetivamente encaminham",
               "número de rotas", "g3_tabelas_roteamento.png")
    _g_tabelas(dados, "base_protocolo", "Tamanho da base de cada protocolo",
               "RIP: rotas da tabela RIP · OSPF: LSAs na LSDB · BGP: caminhos conhecidos",
               "número de entradas", "g3b_base_protocolo.png")


def _g_tabelas(dados: dict, campo: str, titulo: str, subtitulo: str,
               ylabel: str, arquivo: str) -> None:
    protos = [p for p in dados
              if all(campo in v for v in dados[p]["e2_tabelas"]["por_roteador"].values())]
    if not protos:
        print(f"  AVISO: {arquivo} ignorado, sem o campo '{campo}' "
              f"(rode coletar_metricas.py --somente-tabelas)")
        return
    roteadores = list(dados[protos[0]]["e2_tabelas"]["por_roteador"])
    largura = 0.26

    fig, ax = plt.subplots(figsize=(9, 4.6))
    preparar_eixos(ax, titulo, subtitulo, ylabel)

    posicoes = range(len(roteadores))
    for i, p in enumerate(protos):
        deslocamento = (i - (len(protos) - 1) / 2) * (largura + 0.015)
        valores = [dados[p]["e2_tabelas"]["por_roteador"][r][campo]
                   for r in roteadores]
        barras = ax.bar([x + deslocamento for x in posicoes], valores,
                        width=largura, color=COR[p], label=ROTULO[p], zorder=3)
        for b, v in zip(barras, valores):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.3, str(v),
                    ha="center", va="bottom", fontsize=9, color=TINTA_2)

    ax.set_xticks(list(posicoes))
    ax.set_xticklabels([r.upper() for r in roteadores])
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_ylim(0, max(
        dados[p]["e2_tabelas"]["por_roteador"][r][campo]
        for p in protos for r in roteadores) * 1.18)
    ax.legend(frameon=False, ncol=3, loc="upper center",
              bbox_to_anchor=(0.5, -0.08), labelcolor=TINTA_2)
    salvar(fig, arquivo)


# ---------------------------------------------------------------------------
# G4 -- convergencia a frio
# ---------------------------------------------------------------------------

def g4_convergencia_fria(dados: dict) -> None:
    protos = list(dados)
    valores = [dados[p]["e1_convergencia_fria"]["segundos"] or 0 for p in protos]

    fig, ax = plt.subplots(figsize=(7, 4.4))
    preparar_eixos(
        ax, "Convergência a frio",
        "da subida dos daemons até todos os roteadores conhecerem todas as LANs",
        "segundos")
    barras = ax.bar([ROTULO[p] for p in protos], valores, width=0.55,
                    color=[COR[p] for p in protos], zorder=3)
    ax.set_ylim(0, max(valores) * 1.22 if max(valores) else 1)
    rotular_barras(ax, barras, "{:.0f} s")
    salvar(fig, "g4_convergencia_fria.png")


def g5_curva_convergencia(dados: dict) -> None:

    fig, ax = plt.subplots(figsize=(9, 4.8))
    preparar_eixos(
        ax, "Dinâmica da convergência a frio",
        "rotas de acesso conhecidas pelo conjunto dos cinco roteadores (máximo 25)",
        "rotas conhecidas")

    for p in dados:
        curva = dados[p]["e1_convergencia_fria"].get("curva") or []
        if not curva:
            continue
        xs = [t for t, _n in curva]
        ys = [n for _t, n in curva]
        ax.plot(xs, ys, color=COR[p], linewidth=2, label=ROTULO[p], zorder=3)
        # rotulo direto no fim da serie, alem da legenda
        ax.scatter([xs[-1]], [ys[-1]], s=36, color=COR[p],
                   edgecolor=SUPERFICIE, linewidth=2, zorder=4)

    ax.axhline(25, color=EIXO, linewidth=1, linestyle="--", zorder=2)
    ax.text(ax.get_xlim()[1], 25.4, "convergência completa", ha="right",
            va="bottom", fontsize=9, color=TINTA_MUDA)
    ax.set_xlabel("segundos desde a subida dos daemons", fontsize=10, color=TINTA_2)
    ax.set_ylim(0, 28)
    ax.legend(frameon=False, ncol=3, loc="lower right", labelcolor=TINTA_2)
    salvar(fig, "g5_curva_convergencia.png")


# ---------------------------------------------------------------------------
# G6 -- reconvergencia apos falha de enlace
# ---------------------------------------------------------------------------

def _fmt_segundos(v: float) -> str:
    if v < 1:
        return f"{v * 1000:.0f} ms"
    return f"{v:.1f} s".replace(".", ",")


def _g_reconvergencia(dados: dict, chave: str, titulo: str, subtitulo: str,
                      arquivo: str) -> None:
    protos = [p for p in dados if dados[p].get(chave)]
    if not protos:
        return
    medias = [dados[p][chave]["media_s"] or 0 for p in protos]
    desvios = [dados[p][chave]["desvio_s"] or 0 for p in protos]

    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    preparar_eixos(ax, titulo, subtitulo, "segundos sem conectividade")
    barras = ax.bar([ROTULO[p] for p in protos], medias, width=0.55,
                    color=[COR[p] for p in protos], zorder=3,
                    yerr=desvios, capsize=6,
                    error_kw={"ecolor": TINTA_MUDA, "elinewidth": 1.2})
    topo = max(m + d for m, d in zip(medias, desvios)) or 1
    ax.set_ylim(0, topo * 1.28)
    # o rotulo fica acima da barra de erro, nunca sobre ela
    for b, m, d in zip(barras, medias, desvios):
        ax.text(b.get_x() + b.get_width() / 2, m + d + topo * 0.04,
                _fmt_segundos(m), ha="center", va="bottom",
                fontsize=10, color=TINTA, fontweight="600")
    salvar(fig, arquivo)


def g6_reconvergencia(dados: dict) -> None:
    _g_reconvergencia(
        dados, "e5_reconvergencia",
        "Reconvergência com falha unilateral",
        "só o R1 detecta a queda do enlace R1–R5; média de 3 rodadas",
        "g6_reconvergencia_unilateral.png")
    _g_reconvergencia(
        dados, "e5b_reconvergencia_bilateral",
        "Reconvergência com falha bilateral",
        "R1 e R5 detectam a queda (cabo cortado); média de 3 rodadas",
        "g6b_reconvergencia_bilateral.png")


def g8_linha_do_tempo(dados: dict) -> None:
    """
    Faixa de conectividade h1 -> h5 durante a falha unilateral.
    Cada trecho pintado e um periodo com respostas de ping; os vazios sao a
    interrupcao. Mostra onde o trafego parou e quando voltou.
    """
    protos = [p for p in dados
              if (dados[p].get("e5_reconvergencia") or {}).get("rodadas")
              and dados[p]["e5_reconvergencia"]["rodadas"][0].get("linha_do_tempo")]
    if not protos:
        return

    fig, ax = plt.subplots(figsize=(10, 3.6))
    preparar_eixos(
        ax, "Conectividade h1 → h5 durante a falha unilateral",
        "trechos pintados = respostas de ping; t = 0 na queda do enlace R1–R5; "
        "o teste do OSPF usou janela menor (58 s)",
        "")
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, color=GRADE, linewidth=0.8)

    for i, p in enumerate(reversed(protos)):
        rodada = dados[p]["e5_reconvergencia"]["rodadas"][0]
        tempos = [t for t, _s in rodada["linha_do_tempo"]]
        trechos, ini = [], tempos[0]
        for a, b in zip(tempos, tempos[1:]):
            if b - a > 0.2:            # lacuna: fecha o trecho atual
                trechos.append((ini, a - ini))
                ini = b
        trechos.append((ini, tempos[-1] - ini))
        ax.broken_barh(trechos, (i - 0.3, 0.6), color=COR[p], zorder=3)
        # marca onde o enlace voltou: as janelas nao sao iguais entre as coletas
        volta = rodada.get("janela_enlace_caido_s")
        if volta:
            ax.plot([volta, volta], [i - 0.42, i + 0.42], color=TINTA,
                    linewidth=2, zorder=4)
            ax.text(volta + 3, i + 0.36, "enlace volta", fontsize=9,
                    color=TINTA_2, va="bottom")

    ax.set_yticks(range(len(protos)))
    ax.set_yticklabels([ROTULO[p] for p in reversed(protos)], color=TINTA_2)
    ax.axvline(0, color=TINTA_2, linewidth=1.2, linestyle="--", zorder=4)
    ax.text(0, len(protos) - 0.45, " enlace cai", fontsize=9, color=TINTA_2,
            va="bottom")
    ax.set_xlabel("segundos em relação à queda do enlace", fontsize=10,
                  color=TINTA_2)
    ax.set_ylim(-0.6, len(protos) - 0.1)
    salvar(fig, "g8_linha_do_tempo_falha.png")


# ---------------------------------------------------------------------------
# G7 -- latencia e caminho escolhido
# ---------------------------------------------------------------------------

def g7_rtt_caminho(dados: dict) -> None:

    protos = list(dados)
    valores = [dados[p]["e6_rtt"]["h1_h3"].get("rtt_med_ms") or 0 for p in protos]
    desvios = [dados[p]["e6_rtt"]["h1_h3"].get("rtt_desvio_ms") or 0 for p in protos]
    saltos = [dados[p]["e3_selecao_rota"].get("saltos_roteadores") or 0
              for p in protos]

    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    preparar_eixos(
        ax, "Latência fim a fim h1 → h3",
        "RTT médio ± desvio (30 amostras); enlaces virtuais sem atraso, "
        "diferenças dentro do ruído",
        "milissegundos")
    barras = ax.bar([ROTULO[p] for p in protos], valores, width=0.55,
                    color=[COR[p] for p in protos], zorder=3,
                    yerr=desvios, capsize=6,
                    error_kw={"ecolor": TINTA_MUDA, "elinewidth": 1.2})
    topo = max(v + d for v, d in zip(valores, desvios)) or 1
    ax.set_ylim(0, topo * 1.30)
    for b, v in zip(barras, valores):
        ax.text(b.get_x() + b.get_width() / 2, topo * 1.08,
                f"{v:.3f} ms".replace(".", ","), ha="center", va="bottom",
                fontsize=10, color=TINTA, fontweight="600")
    for b, s in zip(barras, saltos):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() / 2,
                f"{s} roteadores", ha="center", va="center",
                fontsize=10, color="#ffffff", fontweight="600")
    salvar(fig, "g7_rtt_caminho.png")


def main() -> int:
    origem = DIR_RESULTADOS / "metricas.json"
    if not origem.exists():
        print(f"erro: {origem} nao encontrado. "
              f"Rode antes: python scripts/coletar_metricas.py")
        return 1

    dados = json.loads(origem.read_text(encoding="utf-8"))
    ordem = [p for p in ("rip", "ospf", "bgp") if p in dados]
    dados = {p: dados[p] for p in ordem}

    print("gerando graficos:")
    for fn in (g1_pacotes_controle, g2_taxa_controle, g3_tabelas,
               g4_convergencia_fria, g5_curva_convergencia,
               g6_reconvergencia, g7_rtt_caminho, g8_linha_do_tempo):
        try:
            fn(dados)
        except Exception as e:  # um grafico com dado faltante nao derruba o resto
            print(f"  AVISO: {fn.__name__} falhou: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
