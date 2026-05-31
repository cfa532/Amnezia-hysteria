#!/bin/bash
# Set up Hysteria2 + UDP proxy on a dual-NIC macOS machine.
# Same script, same result on mac1 (Sequoia) and mac2 (Tahoe).
#
# Usage: ./setup-dual-nic.sh <device_name>
#   device_name: mac1, mac2, etc. — must match a provisioned macN.conf
#                in ~/Documents/Gen8/
#
# Prerequisites:
#   - AmneziaWG installed from App Store and macN.conf already imported
#   - ~/bin/ exists or will be created
#   - Run from: ~/Documents/GitHub/Amnezia-hysteria/client/
#   - Requires sudo for the route-fix LaunchDaemon

set -euo pipefail

DEVICE=${1:?Usage: setup-dual-nic.sh <device_name>}
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CLIENT="$REPO/client"
CONF_DIR=~/Documents/Gen8
APPSUPP=~/Library/Application\ Support/hysteria
AGENTS=~/Library/LaunchAgents
BIN=~/bin

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ── 1. Hysteria2 binary ───────────────────────────────────────────────────────
log "Installing hysteria binary..."
mkdir -p "$BIN"
if [[ ! -x "$BIN/hysteria" ]]; then
    ARCH=$(uname -m | sed 's/x86_64/amd64/;s/arm64/arm64/')
    curl -fsSL "https://github.com/apernet/hysteria/releases/latest/download/hysteria-darwin-${ARCH}" \
         -o "$BIN/hysteria"
    chmod +x "$BIN/hysteria"
fi
"$BIN/hysteria" version

# ── 2. Config directory and servers.conf ─────────────────────────────────────
log "Setting up hysteria config dir..."
mkdir -p "$APPSUPP"

SERVERS_SRC="$CONF_DIR/${DEVICE}-servers.conf"
if [[ -f "$SERVERS_SRC" ]]; then
    cp "$SERVERS_SRC" "$APPSUPP/servers.conf"
    log "servers.conf: loaded from $SERVERS_SRC"
elif [[ ! -f "$APPSUPP/servers.conf" ]]; then
    cp "$REPO/config/servers.conf" "$APPSUPP/servers.conf"
    log "servers.conf: copied from repo config"
else
    log "servers.conf: already present, not overwriting"
fi

# ── 3. client.yaml (proxy mode) ──────────────────────────────────────────────
log "Writing client.yaml..."
cat > "$APPSUPP/client.yaml" << 'EOF'
server: 127.0.0.1:9443

auth: morphous-hy2-2026

tls:
  sni: nebuchadnezzar.fireshare.uk
  insecure: false

transport:
  udp:
    hopInterval: 0s

udpForwarding:
  - listen: 127.0.0.1:1443
    remote: 127.0.0.1:51820
    timeout: 0s
EOF

# ── 4. UDP proxy ──────────────────────────────────────────────────────────────
log "Installing hysteria-udp-proxy..."
cp "$CLIENT/hysteria-udp-proxy.py" "$BIN/hysteria-udp-proxy.py"
chmod +x "$BIN/hysteria-udp-proxy.py"

sed "s|REPLACE_USER|$(whoami)|g" \
    "$CLIENT/uk.fireshare.hysteria-proxy.plist" \
    > "$AGENTS/uk.fireshare.hysteria-proxy.plist"

# ── 5. Failover script ────────────────────────────────────────────────────────
log "Installing hysteria-failover-client..."
cp "$CLIENT/hysteria-failover-client.sh" "$BIN/hysteria-failover-client.sh"
chmod +x "$BIN/hysteria-failover-client.sh"

sed "s|REPLACE_USER|$(whoami)|g" \
    "$CLIENT/uk.fireshare.hysteria-failover.plist" \
    > "$AGENTS/uk.fireshare.hysteria-failover.plist"

# ── 6. Hysteria2 LaunchAgent ──────────────────────────────────────────────────
log "Installing hysteria LaunchAgent..."
sed "s|<USERNAME>|$(whoami)|g" \
    "$CLIENT/uk.fireshare.hysteria.plist" \
    > "$AGENTS/uk.fireshare.hysteria.plist"

# ── 7. AWG en1 route-pinner LaunchDaemon (requires sudo) ──────────────────────
log "Installing AWG en1 route-pinner daemon (requires sudo)..."
sudo cp "$CLIENT/awg-en1-route.sh" /usr/local/bin/awg-en1-route.sh
sudo chmod +x /usr/local/bin/awg-en1-route.sh
sudo cp "$CLIENT/uk.fireshare.awg-en1-route.plist" /Library/LaunchDaemons/

if sudo launchctl list uk.fireshare.awg-en1-route &>/dev/null; then
    sudo launchctl bootout system /Library/LaunchDaemons/uk.fireshare.awg-en1-route.plist 2>/dev/null || true
fi
sudo launchctl bootstrap system /Library/LaunchDaemons/uk.fireshare.awg-en1-route.plist

# ── 8. Load / reload LaunchAgents ────────────────────────────────────────────
log "Loading LaunchAgents..."
GUI="gui/$(id -u)"

for label in uk.fireshare.hysteria-proxy uk.fireshare.hysteria uk.fireshare.hysteria-failover; do
    plist="$AGENTS/${label}.plist"
    if launchctl list "$label" &>/dev/null; then
        launchctl bootout "$GUI/$label" 2>/dev/null || true
    fi
    launchctl load "$plist"
done

# ── 9. Verify ─────────────────────────────────────────────────────────────────
log "Waiting for proxy to bind en1..."
sleep 4

log "=== proxy log ==="
tail -3 /tmp/hysteria-proxy.log

log "=== hysteria log ==="
tail -3 /tmp/hysteria-mac.log

log "=== routes ==="
while IFS= read -r line; do
    [[ "$line" =~ ^#.*$ || -z "$line" ]] && continue
    server_ip=$(awk '{print $1}' <<< "$line")
    /sbin/route get "$server_ip" | grep -E 'interface|gateway' | sed "s/^/  $server_ip: /"
done < "$APPSUPP/servers.conf"

log "Done. Toggle the AWG tunnel in AmneziaWG and verify a handshake."
