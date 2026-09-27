#!/usr/bin/env bash
# =============================================================================
# Orquestrador do laboratorio -- Trabalho I, Redes de Computadores
# UNISINOS 2026/2 -- Matheus Milanezi
#
#   ./scripts/lab.sh preparar-host     ajusta o host Docker para multicast
#   ./scripts/lab.sh up <rip|ospf|bgp> sobe a topologia com UM protocolo ativo
#   ./scripts/lab.sh down              derruba a topologia
#   ./scripts/lab.sh status            estado dos containers e do protocolo
#   ./scripts/lab.sh rotas [rtr]       tabela de roteamento (padrao: todos)
#   ./scripts/lab.sh vizinhos          adjacencias/sessoes do protocolo ativo
#   ./scripts/lab.sh caminho <orig> <dest>   traceroute entre dois hosts
#   ./scripts/lab.sh falha <rtr> <if>  derruba UM lado do enlace (ex: falha r1 to-r5)
#   ./scripts/lab.sh recupera <rtr> <if>  restabelece esse lado
#   ./scripts/lab.sh corta <rA> <rB>   derruba as DUAS pontas (ex: corta r2 r3)
#   ./scripts/lab.sh religa <rA> <rB>  restabelece as duas pontas
#   ./scripts/lab.sh sh <container>    shell dentro de um container
#   ./scripts/lab.sh vtysh <rtr>       console do FRR no roteador
# =============================================================================
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

PROJETO="trabalho1"
REDES=(lan1 lan2 lan3 lan4 lan5 link12 link23 link34 link45 link15)
ROTEADORES=(r1 r2 r3 r4 r5)
ARQ_PROTO="$RAIZ/.proto_ativo"

vermelho() { printf '\033[31m%s\033[0m\n' "$*"; }
verde()    { printf '\033[32m%s\033[0m\n' "$*"; }
azul()     { printf '\033[36m%s\033[0m\n' "$*"; }

proto_ativo() {
    [[ -f "$ARQ_PROTO" ]] && cat "$ARQ_PROTO" || echo ""
}

exigir_proto() {
    local p
    p="$(proto_ativo)"
    if [[ -z "$p" ]]; then
        vermelho "Nenhum protocolo ativo. Rode: ./scripts/lab.sh up <rip|ospf|bgp>"
        exit 1
    fi
    echo "$p"
}

# -----------------------------------------------------------------------------
# Preparacao do host Docker.
#
# Os roteadores trocam RIP (224.0.0.9) e OSPF (224.0.0.5) por multicast.
#
# CAUSA RAIZ (verificada experimentalmente): com bridge-nf-call-iptables=1 o
# trafego IP que atravessa uma bridge Linux e submetido a cadeia FORWARD do
# iptables, onde as regras padrao do Docker o descartam. O resultado e que
# cada roteador ve apenas os proprios hellos: o BGP (TCP unicast) funciona,
# mas RIP e OSPF nunca formam adjacencia.
#
# O IGMP snooping tambem e desativado por precaucao. Foi testado e NAO e a
# causa do problema, adjacencias ja estabelecidas sobrevivem com snooping
# ligado, mas sem um querier na rede os grupos aprendidos podem expirar.
#
# -----------------------------------------------------------------------------
preparar_host() {
    azul ">> preparando o host Docker para trafego multicast"

    local ids=""
    for rede in "${REDES[@]}"; do
        local id
        id="$(docker network inspect "${PROJETO}_${rede}" --format '{{.Id}}' 2>/dev/null | cut -c1-12 || true)"
        [[ -n "$id" ]] && ids="$ids $id"
    done

    docker run --rm --privileged --net=host --pid=host -e IDS="$ids" alpine sh -c '
        echo 0 > /proc/sys/net/bridge/bridge-nf-call-iptables
        echo "  bridge-nf-call-iptables = $(cat /proc/sys/net/bridge/bridge-nf-call-iptables)"
        n=0
        for i in $IDS; do
            f=/sys/class/net/br-$i/bridge/multicast_snooping
            if [ -f "$f" ]; then echo 0 > "$f"; n=$((n+1)); fi
        done
        echo "  IGMP snooping desativado em $n bridge(s) do laboratorio"
    '
    verde "host preparado"
}

subir() {
    local proto="${1:-}"
    case "$proto" in
        rip|ospf|bgp) ;;
        *) vermelho "Protocolo invalido: '${proto}'. Use rip, ospf ou bgp."; exit 1 ;;
    esac

    azul ">> subindo a topologia com o protocolo ${proto^^}"
    # Derruba qualquer execucao anterior: o enunciado exige que os protocolos
    # nao sejam configurados simultaneamente.
    PROTO="$proto" docker compose -p "$PROJETO" down --remove-orphans >/dev/null 2>&1 || true

    PROTO="$proto" docker compose -p "$PROJETO" up -d --build
    echo "$proto" > "$ARQ_PROTO"

    # as bridges so existem depois do 'up', por isso o ajuste vem agora
    preparar_host

    verde "topologia no ar com ${proto^^}. Aguarde a convergencia e rode: ./scripts/lab.sh vizinhos"
}

derrubar() {
    azul ">> derrubando a topologia"
    local p
    p="$(proto_ativo)"
    PROTO="${p:-ospf}" docker compose -p "$PROJETO" down --remove-orphans
    rm -f "$ARQ_PROTO"
    verde "topologia removida"
}

status() {
    local p
    p="$(proto_ativo)"
    azul ">> protocolo ativo: ${p:-<nenhum>}"
    PROTO="${p:-ospf}" docker compose -p "$PROJETO" ps \
        --format 'table {{.Name}}\t{{.State}}\t{{.Status}}'
}

rotas() {
    local alvo="${1:-todos}"
    local lista=("${ROTEADORES[@]}")
    [[ "$alvo" != "todos" ]] && lista=("$alvo")

    for r in "${lista[@]}"; do
        azul "===== $r ====="
        docker exec "$r" vtysh -c "show ip route" 2>/dev/null \
            | grep -vE "vtysh.conf" || true
    done
}

vizinhos() {
    local p
    p="$(exigir_proto)"
    for r in "${ROTEADORES[@]}"; do
        azul "===== $r ====="
        case "$p" in
            rip)  docker exec "$r" vtysh -c "show ip rip status" 2>/dev/null | grep -v vtysh.conf ;;
            ospf) docker exec "$r" vtysh -c "show ip ospf neighbor" 2>/dev/null | grep -v vtysh.conf ;;
            bgp)  docker exec "$r" vtysh -c "show ip bgp summary" 2>/dev/null | grep -v vtysh.conf ;;
        esac
    done
}

caminho() {
    local origem="${1:?informe o host de origem, ex: h1}"
    local destino="${2:?informe o IP de destino, ex: 10.3.0.10}"
    azul ">> traceroute $origem -> $destino"
    docker exec "$origem" traceroute -n -w 1 -q 1 "$destino"
}

falha() {
    local rtr="${1:?informe o roteador, ex: r1}"
    local iface="${2:?informe a interface, ex: to-r5}"
    docker exec "$rtr" ip link set dev "$iface" down
    vermelho ">> enlace $rtr/$iface DERRUBADO"
}

recupera() {
    local rtr="${1:?informe o roteador, ex: r1}"
    local iface="${2:?informe a interface, ex: to-r5}"
    docker exec "$rtr" ip link set dev "$iface" up
    verde ">> enlace $rtr/$iface RESTABELECIDO"
}

# Falha bilateral, como um cabo cortado: os dois roteadores perdem o enlace.
# Na falha unilateral ('falha') a outra ponta continua com a interface ativa
# e so descobre a queda pelo protocolo. No RIP e no BGP isso leva ate 180 s.
corta() {
    local a="${1:?informe o primeiro roteador, ex: r2}"
    local b="${2:?informe o segundo roteador, ex: r3}"
    docker exec "$a" ip link set dev "to-$b" down
    docker exec "$b" ip link set dev "to-$a" down
    vermelho ">> enlace ${a^^}-${b^^} CORTADO (as duas pontas)"
}

religa() {
    local a="${1:?informe o primeiro roteador, ex: r2}"
    local b="${2:?informe o segundo roteador, ex: r3}"
    docker exec "$a" ip link set dev "to-$b" up
    docker exec "$b" ip link set dev "to-$a" up
    verde ">> enlace ${a^^}-${b^^} RELIGADO"
}

case "${1:-}" in
    preparar-host) preparar_host ;;
    up)            subir "${2:-}" ;;
    down)          derrubar ;;
    status)        status ;;
    rotas)         rotas "${2:-todos}" ;;
    vizinhos)      vizinhos ;;
    caminho)       caminho "${2:-}" "${3:-}" ;;
    falha)         falha "${2:-}" "${3:-}" ;;
    recupera)      recupera "${2:-}" "${3:-}" ;;
    corta)         corta "${2:-}" "${3:-}" ;;
    religa)        religa "${2:-}" "${3:-}" ;;
    sh)            docker exec -it "${2:?informe o container}" bash ;;
    vtysh)         docker exec -it "${2:?informe o roteador}" vtysh ;;
    *)             sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
esac
