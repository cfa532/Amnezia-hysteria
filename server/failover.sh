#!/bin/bash
# Server-side peer monitor — maintains Cloudflare DNS A records for the server farm
#
# Reads the full server list from SERVERS_CONF. Checks every peer (all servers
# except this one). If a peer is unreachable, its A record is removed from DNS.
# When it recovers, the record is re-added. TTL=60s so clients converge quickly.
#
# To add a server: append a line to servers.conf and deploy to all servers.
# No other changes are needed in this script.
#
# Crontab: */2 * * * * /usr/local/bin/hysteria-failover.sh
# Requires: curl, python3, nc

MY_IP="<THIS_SERVER_IP>"
SERVERS_CONF="/usr/local/etc/hysteria/servers.conf"
CF_TOKEN="<YOUR_CLOUDFLARE_API_TOKEN>"
CF_ZONE="<YOUR_CLOUDFLARE_ZONE_ID>"
RECORD_NAME="<YOUR_HOSTNAME>"
LOG="/var/log/hysteria-failover.log"

log() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }

mapfile -t ALL_SERVERS < <(grep -Ev '^\s*(#|$)' "$SERVERS_CONF" | awk '{print $1}')

is_alive() {
    ping -c 3 -W 2 "$1" &>/dev/null && nc -z -w 5 "$1" 22 &>/dev/null
}

get_record_id() {
    curl -sf "https://api.cloudflare.com/client/v4/zones/$CF_ZONE/dns_records?type=A&name=$RECORD_NAME&content=$1" \
        -H "Authorization: Bearer $CF_TOKEN" \
        | python3 -c "import sys,json; r=json.load(sys.stdin)['result']; print(r[0]['id'] if r else '')" 2>/dev/null
}

add_record() {
    local ip=$1 result
    result=$(curl -sf -X POST "https://api.cloudflare.com/client/v4/zones/$CF_ZONE/dns_records" \
        -H "Authorization: Bearer $CF_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"type\":\"A\",\"name\":\"$RECORD_NAME\",\"content\":\"$ip\",\"ttl\":60,\"proxied\":false}")
    if echo "$result" | python3 -c "import sys,json; sys.exit(0 if json.load(sys.stdin)['success'] else 1)" 2>/dev/null; then
        log "RESTORED $ip to DNS"
    else
        log "ERROR adding $ip: $result"
    fi
}

remove_record() {
    local ip=$1 id
    id=$(get_record_id "$ip")
    [[ -z "$id" ]] && return
    local result
    result=$(curl -sf -X DELETE "https://api.cloudflare.com/client/v4/zones/$CF_ZONE/dns_records/$id" \
        -H "Authorization: Bearer $CF_TOKEN")
    if echo "$result" | python3 -c "import sys,json; sys.exit(0 if json.load(sys.stdin)['success'] else 1)" 2>/dev/null; then
        log "REMOVED $ip from DNS (unreachable)"
    else
        log "ERROR removing $ip: $result"
    fi
}

for PEER_IP in "${ALL_SERVERS[@]}"; do
    [[ "$PEER_IP" == "$MY_IP" ]] && continue

    if is_alive "$PEER_IP"; then
        record_id=$(get_record_id "$PEER_IP")
        if [[ -z "$record_id" ]]; then
            log "$PEER_IP alive but absent from DNS — restoring"
            add_record "$PEER_IP"
        fi
    else
        log "$PEER_IP unreachable — removing from DNS"
        remove_record "$PEER_IP"
    fi
done
