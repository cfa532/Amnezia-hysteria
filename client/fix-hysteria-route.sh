#!/bin/bash
# Dynamic route-fix for macOS AmneziaWG clients.
#
# macOS clones a host route for the AWG endpoint IP onto the utun interface when
# the tunnel activates, which captures the endpoint's own packets and loops them
# back into the tunnel. This daemon re-pins the endpoint IP(s) to the physical
# gateway so they always exit via the clean physical path instead of utun.
#
# Targets are resolved dynamically from the AWG endpoint hostname's A records, so
# the client is server-agnostic: adding/moving/removing a backend is a DNS-only
# change and every client adapts on the next route-monitor tick. No servers.conf,
# no per-server config baked into the client.
ENDPOINT_HOST="${ENDPOINT_HOST:-nebuchadnezzar.fireshare.uk}"
IFACE="${ROUTE_FIX_IFACE:-en1}"

# Resolve the endpoint hostname to its current set of IPv4 A records.
resolve_targets() {
    dig +short "$ENDPOINT_HOST" A 2>/dev/null \
        | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'
}

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
        echo "$(date) no gateway on $IFACE yet, skipping $target"
        return
    fi

    if [[ "$current_iface" != "$IFACE" || "$current_gateway" != "$expected_gateway" ]]; then
        /sbin/route delete -host "$target" 2>/dev/null
        /sbin/route delete -host "$target" 2>/dev/null
        /sbin/route add -host "$target" "$expected_gateway"
        echo "$(date) fixed $target → $expected_gateway ($IFACE)"
    fi
}

fix_all() {
    local targets t
    targets=$(resolve_targets)
    if [[ -z "$targets" ]]; then
        echo "$(date) WARN: $ENDPOINT_HOST resolved to no A records — leaving existing routes" >&2
        return
    fi
    for t in $targets; do fix_route "$t"; done
}

fix_all

/sbin/route monitor | while IFS= read -r _line; do
    fix_all
done
