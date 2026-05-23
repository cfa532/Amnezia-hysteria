#!/bin/bash
# Server-side Hysteria2 DNS failover
# Deploy on each server. Set MY_IP and PEER_IP for each instance.
# Requires: Cloudflare API token (Zone:DNS:Edit), python3, curl
# Crontab: */2 * * * * /usr/local/bin/hysteria-failover.sh

CF_TOKEN="<YOUR_CLOUDFLARE_API_TOKEN>"
CF_ZONE="<YOUR_CLOUDFLARE_ZONE_ID>"
RECORD_NAME="<YOUR_HOSTNAME>"   # e.g. nebuchadnezzar.fireshare.uk
LOG="/var/log/hysteria-failover.log"

MY_IP="<THIS_SERVER_IP>"
PEER_IP="<OTHER_SERVER_IP>"

log() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }

is_alive() {
    ping -c 3 -W 2 "$1" &>/dev/null || nc -z -w 5 "$1" 22 &>/dev/null
}

get_record_id() {
    curl -sf "https://api.cloudflare.com/client/v4/zones/$CF_ZONE/dns_records?type=A&name=$RECORD_NAME&content=$1" \
        -H "Authorization: Bearer $CF_TOKEN" \
        | python3 -c "import sys,json; r=json.load(sys.stdin)['result']; print(r[0]['id'] if r else '')" 2>/dev/null
}

add_record() {
    local ip=$1
    local result
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
    if [[ -z "$id" ]]; then return; fi
    local result
    result=$(curl -sf -X DELETE "https://api.cloudflare.com/client/v4/zones/$CF_ZONE/dns_records/$id" \
        -H "Authorization: Bearer $CF_TOKEN")
    if echo "$result" | python3 -c "import sys,json; sys.exit(0 if json.load(sys.stdin)['success'] else 1)" 2>/dev/null; then
        log "REMOVED $ip from DNS (server unreachable)"
    else
        log "ERROR removing $ip: $result"
    fi
}

if is_alive "$PEER_IP"; then
    peer_id=$(get_record_id "$PEER_IP")
    if [[ -z "$peer_id" ]]; then
        log "$PEER_IP is alive but missing from DNS — restoring"
        add_record "$PEER_IP"
    fi
else
    log "$PEER_IP unreachable — removing from DNS"
    remove_record "$PEER_IP"
fi
