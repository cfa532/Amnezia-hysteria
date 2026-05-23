#!/bin/bash
# Client-side Hysteria2 load balancer and failover
#
# Reads the server list from servers.conf. On first run, picks a random server
# so devices are distributed across the farm by default. On failure, round-robins
# to the next live server and restarts Hysteria2.
#
# To add a server: append a line to servers.conf. No other changes needed.
# Deploy as a LaunchAgent (user). See: uk.fireshare.hysteria-failover.plist

YAML="$HOME/Library/Application Support/hysteria/client.yaml"
SERVERS_CONF="$HOME/Library/Application Support/hysteria/servers.conf"
STATE_FILE="/tmp/hysteria-server-index"
LOG="/tmp/hysteria-failover-client.log"

log() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }

# Read server IPs from config, skipping comments and blank lines
mapfile -t SERVERS < <(grep -Ev '^\s*(#|$)' "$SERVERS_CONF" | awk '{print $1}')
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

# Get current server IP from client.yaml
current=$(grep '^server:' "$YAML" | sed 's/server: //;s|:[0-9]*$||;s/ //g')

# Find current server's index in the list
current_idx=-1
for i in "${!SERVERS[@]}"; do
    [[ "${SERVERS[$i]}" == "$current" ]] && current_idx=$i && break
done

# First run or server not in list: pick a random starting server
if [[ $current_idx -eq -1 ]]; then
    stored=$(cat "$STATE_FILE" 2>/dev/null)
    if [[ "$stored" =~ ^[0-9]+$ ]]; then
        current_idx=$((stored % COUNT))
    else
        current_idx=$((RANDOM % COUNT))
    fi
    current="${SERVERS[$current_idx]}"
    port=$(get_port "$current")
    sed -i '' "s|^server:.*|server: ${current}:${port}|" "$YAML"
    echo "$current_idx" > "$STATE_FILE"
    log "Initialised to $current idx=$current_idx"
fi

# Ping goes via en1 (static host route bypasses VPN — no DNS needed)
if /sbin/ping -c 3 -t 5 -q "$current" &>/dev/null; then
    echo "$current_idx" > "$STATE_FILE"
    exit 0
fi

log "$current unreachable — round-robining to next server"

# Try each remaining server in order
for ((i = 1; i < COUNT; i++)); do
    candidate_idx=$(( (current_idx + i) % COUNT ))
    candidate="${SERVERS[$candidate_idx]}"
    if /sbin/ping -c 3 -t 5 -q "$candidate" &>/dev/null; then
        port=$(get_port "$candidate")
        sed -i '' "s|^server:.*|server: ${candidate}:${port}|" "$YAML"
        echo "$candidate_idx" > "$STATE_FILE"
        launchctl kickstart -k "gui/$(id -u)/uk.fireshare.hysteria"
        log "Switched $current → $candidate idx=$candidate_idx"
        exit 0
    fi
done

log "All $COUNT servers unreachable"
exit 1
