#!/bin/bash
TARGETS=(
    8.222.164.32
    43.160.238.86
)
IFACE=en1

fix_route() {
    local target="$1"
    local current_iface current_gateway expected_gateway

    current_iface=$(/sbin/route get "$target" 2>/dev/null | awk '/interface/{print $2}')
    current_gateway=$(/sbin/route get "$target" 2>/dev/null | awk '/gateway/{print $2}')

    # DHCP gives the router via ipconfig; manual/static IPs need the routing table fallback
    expected_gateway=$(ipconfig getoption "$IFACE" router 2>/dev/null)
    if [[ -z "$expected_gateway" ]]; then
        expected_gateway=$(netstat -rn 2>/dev/null | awk '/^default/ && $NF=="'"$IFACE"'" {print $2; exit}')
    fi

    if [[ -z "$expected_gateway" ]]; then
        echo "$(date) no gateway on $IFACE yet, skipping"
        return
    fi

    if [[ "$current_iface" != "$IFACE" || "$current_gateway" != "$expected_gateway" ]]; then
        /sbin/route delete -host "$target" 2>/dev/null
        /sbin/route delete -host "$target" 2>/dev/null
        /sbin/route add -host "$target" "$expected_gateway"
        echo "$(date) fixed $target → $expected_gateway ($IFACE)"
    fi
}

for _t in "${TARGETS[@]}"; do fix_route "$_t"; done

/sbin/route monitor | while IFS= read -r _line; do
    for _t in "${TARGETS[@]}"; do fix_route "$_t"; done
done
