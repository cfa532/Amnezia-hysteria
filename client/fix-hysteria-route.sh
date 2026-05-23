#!/bin/bash
# macOS route fix daemon — keeps the Hysteria2 server IP routed via WiFi (en1),
# not via the AmneziaWG utun interface (which would create a routing loop).
#
# macOS spuriously clones a host route for the VPN server IP onto utun when
# AmneziaWG activates, even though the IP is excluded from AllowedIPs.
# This script reacts instantly to route table changes via `route monitor`
# and overrides the cloned route with a static host route via the WiFi gateway.
#
# Deploy as a LaunchDaemon (root) so it can run `route` commands without sudo.
# See: uk.fireshare.hysteria-route.plist

TARGET=<HYSTERIA2_SERVER_IP>   # e.g. <SERVER_1_IP>
IFACE=en1                       # WiFi interface (direct home router, no soft router)

fix_route() {
    local current_iface current_gateway expected_gateway
    current_iface=$(/sbin/route get "$TARGET" 2>/dev/null | awk '/interface/{print $2}')
    current_gateway=$(/sbin/route get "$TARGET" 2>/dev/null | awk '/gateway/{print $2}')
    expected_gateway=$(ipconfig getoption "$IFACE" router 2>/dev/null)

    if [[ -z "$expected_gateway" ]]; then
        echo "$(date) no gateway on $IFACE yet, skipping"
        return
    fi

    if [[ "$current_iface" != "$IFACE" || "$current_gateway" != "$expected_gateway" ]]; then
        /sbin/route delete -host "$TARGET" 2>/dev/null
        /sbin/route add -host "$TARGET" "$expected_gateway"
        echo "$(date) fixed route via $expected_gateway ($IFACE)"
    fi
}

fix_route

/sbin/route monitor | while IFS= read -r _line; do
    fix_route
done
