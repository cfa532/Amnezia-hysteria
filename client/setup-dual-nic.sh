#!/bin/bash
# Set up a dual-NIC macOS client for direct AmneziaWG over en1.
#
# Architecture (Hysteria retired — macOS now connects to AWG directly):
#
#   AmneziaWG (utun) ──UDP 443──▶ nebuchadnezzar.fireshare.uk
#                                  (Cloudflare DNS round-robin: tn1 / minipc)
#        AllowedIPs = split list (China direct, everything else via VPN)
#
#   awg-en1-route daemon resolves the endpoint hostname and pins each A record
#   to the en1 gateway, so the tunnel egresses via the clean WiFi path (en1) —
#   never via utun (the macOS clone-route loop) or en0 (the gen8 soft-router,
#   which runs its own always-on VPN).
#
#   Failover is handled by DNS: the controller pulls a dead server's A record,
#   AWG re-resolves on the next handshake. No local proxy, client, or failover
#   agent is needed.
#
# Usage: ./setup-dual-nic.sh
#   Run from ~/Documents/GitHub/Amnezia-hysteria/client/. Requires sudo for the
#   LaunchDaemon. The macN.conf must already be imported into the AmneziaWG app
#   with Endpoint = nebuchadnezzar.fireshare.uk:443.

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CLIENT="$REPO/client"
ENDPOINT_HOST="nebuchadnezzar.fireshare.uk"
log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ── 1. Clean up any leftover Hysteria-era client components ───────────────────
log "Removing any leftover Hysteria client agents..."
GUI="gui/$(id -u)"
for label in uk.fireshare.hysteria uk.fireshare.hysteria-proxy uk.fireshare.hysteria-failover; do
    launchctl bootout "$GUI/$label" 2>/dev/null || true
done
mkdir -p ~/Library/LaunchAgents/.hysteria-removed
for p in uk.fireshare.hysteria.plist uk.fireshare.hysteria-proxy.plist uk.fireshare.hysteria-failover.plist; do
    [[ -f ~/Library/LaunchAgents/$p ]] && \
        mv ~/Library/LaunchAgents/"$p" ~/Library/LaunchAgents/.hysteria-removed/ || true
done
# Retire the old route-pinner name if present (replaced by awg-en1-route).
if sudo launchctl list uk.fireshare.hysteria-route &>/dev/null; then
    sudo launchctl bootout system/uk.fireshare.hysteria-route 2>/dev/null || true
fi
sudo rm -f /Library/LaunchDaemons/uk.fireshare.hysteria-route.plist \
           /usr/local/bin/fix-hysteria-route.sh 2>/dev/null || true

# ── 2. Install the AWG en1 route-pinner LaunchDaemon (requires sudo) ──────────
log "Installing AWG en1 route-pinner daemon (requires sudo)..."
sudo cp "$CLIENT/awg-en1-route.sh" /usr/local/bin/awg-en1-route.sh
sudo chmod +x /usr/local/bin/awg-en1-route.sh
sudo cp "$CLIENT/uk.fireshare.awg-en1-route.plist" /Library/LaunchDaemons/

if sudo launchctl list uk.fireshare.awg-en1-route &>/dev/null; then
    sudo launchctl bootout system/uk.fireshare.awg-en1-route 2>/dev/null || true
fi
sudo launchctl bootstrap system /Library/LaunchDaemons/uk.fireshare.awg-en1-route.plist

# ── 3. Verify ─────────────────────────────────────────────────────────────────
log "Waiting for the daemon to pin routes..."
sleep 4

log "=== route-pinner log ==="
tail -5 /tmp/awg-en1-route.log 2>/dev/null || true

log "=== endpoint routes (each should be via en1) ==="
for ip in $(dig +short "$ENDPOINT_HOST" A | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'); do
    /sbin/route get "$ip" 2>/dev/null \
        | awk '/interface:|gateway:/{printf "  '"$ip"': %s\n", $2}'
done

log "Done. Toggle the AWG tunnel in AmneziaWG, then verify:"
log "  curl https://api.ipify.org   # should print a VPN server IP (tn1 or minipc)"
