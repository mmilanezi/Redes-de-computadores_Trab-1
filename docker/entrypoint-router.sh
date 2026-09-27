#!/bin/bash
# =============================================================================
# Entrypoint do roteador FRR.
#
# O Docker nomeia as interfaces (eth0, eth1, ...) em ordem nao garantida.
# Para que os arquivos de configuracao do FRR possam referenciar interfaces
# por nome estavel, este script renomeia cada interface a partir do endereco
# IPv4 que o Docker atribuiu a ela.
#
# Variaveis de ambiente esperadas:
#   ROUTER   nome do roteador (r1..r5)
#   PROTO    protocolo ativo nesta execucao: rip | ospf | bgp
#   IFMAP    lista "IP=nome" separada por espacos, ex:
#            "10.1.0.1=lan 172.16.12.1=to-r2 172.16.15.1=to-r5"
# =============================================================================
set -eu

log() { echo "[entrypoint:${ROUTER:-?}] $*"; }

# -----------------------------------------------------------------------------
# 1. Renomeia as interfaces conforme o IFMAP
# -----------------------------------------------------------------------------
for pair in ${IFMAP:-}; do
    ip_addr="${pair%%=*}"
    new_name="${pair##*=}"

    dev="$(ip -o -4 addr show | awk -v a="${ip_addr}/" '$4 ~ "^"a {print $2; exit}')"
    if [ -z "$dev" ]; then
        log "AVISO: nenhuma interface com o endereco $ip_addr (esperado: $new_name)"
        continue
    fi
    if [ "$dev" = "$new_name" ]; then
        continue
    fi

    ip link set dev "$dev" down
    ip link set dev "$dev" name "$new_name"
    ip link set dev "$new_name" up
    log "interface $dev ($ip_addr) renomeada para $new_name"
done

# -----------------------------------------------------------------------------
# 2. Remove a rota default criada pelo bridge do Docker.
#    O encaminhamento neste laboratorio deve vir exclusivamente do protocolo
#    de roteamento em estudo.
# -----------------------------------------------------------------------------
while ip route show default | grep -q .; do
    ip route del default || break
done
log "rota default removida; encaminhamento depende apenas do protocolo $PROTO"

# -----------------------------------------------------------------------------
# 3. Seleciona o conjunto de daemons do protocolo desta execucao.
# -----------------------------------------------------------------------------
case "$PROTO" in
    rip)  DAEMON_LINE="ripd=yes";  OTHERS="ospfd=no bgpd=no" ;;
    ospf) DAEMON_LINE="ospfd=yes"; OTHERS="ripd=no bgpd=no"  ;;
    bgp)  DAEMON_LINE="bgpd=yes";  OTHERS="ripd=no ospfd=no" ;;
    *)    log "ERRO: PROTO invalido '$PROTO' (use rip, ospf ou bgp)"; exit 1 ;;
esac

cat > /etc/frr/daemons <<EOF
# Gerado pelo entrypoint -- protocolo ativo: $PROTO
zebra=yes
staticd=yes
$DAEMON_LINE
$(echo "$OTHERS" | tr ' ' '\n')
ldpd=no
nhrpd=no
eigrpd=no
babeld=no
sharpd=no
pbrd=no
bfdd=no
fabricd=no
vrrpd=no
pathd=no
ospf6d=no
ripngd=no
isisd=no
pimd=no
pim6d=no

vtysh_enable=yes
zebra_options="  -A 127.0.0.1 -s 90000000"
staticd_options="-A 127.0.0.1"
ripd_options="  -A 127.0.0.1"
ospfd_options=" -A 127.0.0.1"
bgpd_options="  -A 127.0.0.1"
EOF

# -----------------------------------------------------------------------------
# 4. Instala a configuracao do protocolo ativo
# -----------------------------------------------------------------------------
src="/etc/frr/configs/${ROUTER}.${PROTO}.conf"
if [ ! -f "$src" ]; then
    log "ERRO: configuracao nao encontrada: $src"
    exit 1
fi
cp "$src" /etc/frr/frr.conf

# o vtysh reclama se este arquivo nao existir, poluindo a saida das coletas
echo "service integrated-vtysh-config" > /etc/frr/vtysh.conf

chown frr:frr /etc/frr/frr.conf /etc/frr/daemons /etc/frr/vtysh.conf
chmod 640 /etc/frr/frr.conf /etc/frr/daemons /etc/frr/vtysh.conf
log "configuracao ativa: $src"

# marca o instante de inicializacao, usado para medir convergencia a frio
date +%s.%N > /tmp/boot_ts

exec /usr/lib/frr/docker-start
