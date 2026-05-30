#!/bin/bash
# Provision or reprovision a device against the VPN controller.
#
# Usage: reprovision.sh <device_name> [os_type] [routing] [output_dir]
#   device_name: mac1, mac2, ios1, etc.
#   os_type:     macos | ios | android  (default: macos)
#   routing:     full | split            (default: split)
#   output_dir:  where to write output files  (default: ~/Documents/Gen8)
#
# macOS output: <device_name>.conf  (AWG config)
#               servers.conf        (Hysteria2 server list — macOS only)
# iOS/Android:  <device_name>.conf  (AWG config)
#
# NOTE: Run on the controller server (tn1) where awg tools are installed.
#
# Reads PROVISION_URL and PROVISION_TOKEN from environment or ~/.vpn-provision.env
# Default when run on the controller: PROVISION_URL=http://127.0.0.1:9000

set -euo pipefail

DEVICE_NAME=${1:?Usage: reprovision.sh <device_name> [os_type] [routing] [output_dir]}
OS_TYPE=${2:-macos}
ROUTING=${3:-split}
OUTPUT_DIR=${4:-~/Documents/Gen8}
REGION=${REGION:-asia}

ENV_FILE=~/.vpn-provision.env
[ -f "$ENV_FILE" ] && source "$ENV_FILE"

PROVISION_URL=${PROVISION_URL:-http://127.0.0.1:9000}
PROVISION_TOKEN=${PROVISION_TOKEN:?Set PROVISION_TOKEN in $ENV_FILE or environment}

# Generate a fresh keypair for this device
PRIV=$(awg genkey)
PUB=$(echo "$PRIV" | awg pubkey)

echo "Device:  $DEVICE_NAME"
echo "OS type: $OS_TYPE"
echo "Routing: $ROUTING"
echo "Region:  $REGION"
echo "Pubkey:  $PUB"

RESPONSE=$(curl -sf -X POST "$PROVISION_URL/provision" \
    -H "Authorization: Bearer $PROVISION_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"device_name\":\"$DEVICE_NAME\",\"device_pubkey\":\"$PUB\",\"device_privkey\":\"$PRIV\",\"os_type\":\"$OS_TYPE\",\"routing\":\"$ROUTING\",\"region\":\"$REGION\"}")

echo "$RESPONSE" | python3 -m json.tool

mkdir -p "$OUTPUT_DIR"

echo "$RESPONSE" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data['wg_config'])
" > "${OUTPUT_DIR}/${DEVICE_NAME}.conf"

echo "Config written to ${OUTPUT_DIR}/${DEVICE_NAME}.conf"

if [[ "$OS_TYPE" == "macos" ]]; then
    echo "$RESPONSE" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data['servers_conf'])
" > "${OUTPUT_DIR}/servers.conf"
    echo "servers.conf written to ${OUTPUT_DIR}/servers.conf"
fi

echo "Assigned to: $(echo "$RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['server_name'], d['client_ip'])")"
