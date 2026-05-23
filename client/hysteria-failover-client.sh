#!/bin/bash
# Client-side Hysteria2 round-robin failover
#
# Works for both direct (device1) and proxy (device2/macOS 26 dual-NIC) configurations.
# Source of truth is STATE_FILE; client.yaml is only updated in direct mode.
#
# Proxy mode is detected automatically: if client.yaml server == 127.0.0.1, proxy is in use.
# To add a server: append a line to servers.conf — no other changes needed.
# Deploy as a LaunchAgent (user). See: uk.fireshare.hysteria-failover.plist

YAML="$HOME/Library/Application Support/hysteria/client.yaml"
SERVERS_CONF="$HOME/Library/Application Support/hysteria/servers.conf"
STATE_FILE="/tmp/hysteria-server-index"
LOG="/tmp/hysteria-failover-client.log"

log() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }

# Detect proxy mode: client.yaml points to loopback → UDP proxy in use
yaml_server=$(grep '^server:' "$YAML" 2>/dev/null | awk '{print $2}' | sed 's|:.*||')
using_proxy=false
[[ "$yaml_server" == "127.0.0.1" || "$yaml_server" == "localhost" ]] && using_proxy=true

# Read server list — use while-read for Bash 3.2 (macOS ships Bash 3.2, no mapfile)
SERVERS=()
while IFS= read -r _s; do SERVERS+=("$_s"); done \
    < <(grep -Ev '^\s*(#|$)' "$SERVERS_CONF" | awk '{print $1}')
COUNT=${#SERVERS[@]}

if [[ $COUNT -eq 0 ]]; then
    log "ERROR: no servers in $SERVERS_CONF"
    exit 1
fi

get_port() {
    local ip=$1
    local port
    port=$(grep -Ev '^\s*(#|$)' "$SERVERS_CONF" | awk -v ip="$ip" '$1==ip{print $3; exit}')
    echo "${port:-443}"
}

# Source of truth: STATE_FILE (not client.yaml, which may show proxy address)
stored=$(cat "$STATE_FILE" 2>/dev/null)
if [[ -n "$stored" && "$stored" =~ ^[0-9]+$ ]]; then
    current_idx=$((stored % COUNT))
else
    current_idx=$((RANDOM % COUNT))
    echo "$current_idx" > "$STATE_FILE"
    log "Initialised at random idx=$current_idx"
fi
current="${SERVERS[$current_idx]}"

# In direct mode, keep client.yaml in sync with the current server
if ! $using_proxy; then
    port=$(get_port "$current")
    sed -i '' "s|^server:.*|server: ${current}:${port}|" "$YAML"
fi

# Ping goes via en1 UGHS host route — bypasses VPN tunnel, no DNS needed
if /sbin/ping -c 3 -t 5 -q "$current" &>/dev/null; then
    exit 0
fi

log "$current unreachable — round-robining"

for ((i = 1; i < COUNT; i++)); do
    candidate_idx=$(( (current_idx + i) % COUNT ))
    candidate="${SERVERS[$candidate_idx]}"
    if /sbin/ping -c 3 -t 5 -q "$candidate" &>/dev/null; then
        port=$(get_port "$candidate")
        echo "$candidate_idx" > "$STATE_FILE"
        if $using_proxy; then
            # Restart proxy first to clear stale sessions, then hysteria
            launchctl kickstart -k "gui/$(id -u)/uk.fireshare.hysteria-proxy"
            sleep 1
            launchctl kickstart -k "gui/$(id -u)/uk.fireshare.hysteria"
        else
            sed -i '' "s|^server:.*|server: ${candidate}:${port}|" "$YAML"
            launchctl kickstart -k "gui/$(id -u)/uk.fireshare.hysteria"
        fi
        log "Switched $current → $candidate idx=$candidate_idx"
        exit 0
    fi
done

log "All $COUNT servers unreachable"
exit 1
