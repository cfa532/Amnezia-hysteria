#!/bin/bash
# Client-side Hysteria2 server failover
# Checks if the current server is reachable every 2 minutes (via LaunchAgent).
# If not, switches client.yaml to the backup server and restarts Hysteria2.
# Uses IP addresses directly — avoids DNS dependency (AmneziaWG sets system
# DNS to the VPN server, which is unreachable when the tunnel is down).

YAML="$HOME/Library/Application Support/hysteria/client.yaml"
PRIMARY="<PRIMARY_SERVER_IP>"     # e.g. 8.222.164.32
BACKUP="<BACKUP_SERVER_IP>"       # e.g. 43.160.238.86
LOG="/tmp/hysteria-failover-client.log"

current=$(grep '^server:' "$YAML" | sed 's/server: //;s|:443||;s/ //g')
[[ ! "$current" =~ ^[0-9]+\. ]] && current="$PRIMARY"

# Ping goes via en1 (static host route bypasses VPN) — no DNS needed
if /sbin/ping -c 3 -t 5 -q "$current" &>/dev/null; then
    exit 0
fi

[[ "$current" == "$PRIMARY" ]] && candidate="$BACKUP" || candidate="$PRIMARY"

if /sbin/ping -c 3 -t 5 -q "$candidate" &>/dev/null; then
    sed -i '' "s|^server:.*|server: ${candidate}:443|" "$YAML"
    launchctl kickstart -k "gui/$(id -u)/uk.fireshare.hysteria"
    echo "$(date) failed over from $current to $candidate" | tee -a "$LOG"
fi
