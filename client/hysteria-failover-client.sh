#!/bin/bash
SERVERS_CONF="$HOME/Library/Application Support/hysteria/servers.conf"
STATE_FILE="/tmp/hysteria-server-index"
LOG="/tmp/hysteria-failover-client.log"

log() { echo "$(date +%F %T) $*" | tee -a "$LOG"; }

SERVERS=()
while IFS= read -r _s; do SERVERS+=("$_s"); done \
    < <(grep -Ev '^\s*(#|$)' "$SERVERS_CONF" | awk '{print $1}')
COUNT=${#SERVERS[@]}

if [[ $COUNT -eq 0 ]]; then
    log "ERROR: no servers in $SERVERS_CONF"
    exit 1
fi

# Read current server from state file (not client.yaml, which points to proxy)
stored=$(cat "$STATE_FILE" 2>/dev/null)
if [[ -n "$stored" && "$stored" =~ ^[0-9]+$ ]]; then
    current_idx=$((stored % COUNT))
else
    current_idx=0
    echo $current_idx > "$STATE_FILE"
fi
current="${SERVERS[$current_idx]}"

if /sbin/ping -c 3 -t 5 -q "$current" &>/dev/null; then
    echo $current_idx > "$STATE_FILE"
    exit 0
fi

log "$current unreachable — round-robining"

for ((i = 1; i < COUNT; i++)); do
    candidate_idx=$(( (current_idx + i) % COUNT ))
    candidate="${SERVERS[$candidate_idx]}"
    if /sbin/ping -c 3 -t 5 -q "$candidate" &>/dev/null; then
        port=$(grep -Ev '^\s*(#|$)' "$SERVERS_CONF" | awk -v ip="$candidate" '$1==ip{print $3; exit}')
        [[ -z "$port" ]] && port=443
        echo $candidate_idx > "$STATE_FILE"
        # Restart proxy first (clears old sessions), then hysteria
        launchctl kickstart -k "gui/$(id -u)/uk.fireshare.hysteria-proxy"
        sleep 1
        launchctl kickstart -k "gui/$(id -u)/uk.fireshare.hysteria"
        log "Switched $current → $candidate idx=$candidate_idx"
        exit 0
    fi
done

log "All $COUNT servers unreachable"
exit 1
