#!/bin/bash
# Provision or reprovision a device against the VPN controller.
#
# Usage: reprovision.sh <device_name> [os_type] [routing|output_dir] [output_dir]
#   device_name: mac1, mac2, ios1, etc.
#   os_type:     macos | ios | android  (default: macos)
#   routing:     full | split            (default: full)
#   output_dir:  where to write <device_name>.conf  (default: ~/Documents/Gen8)
#
# NOTE: This script must run on the controller server (tn2) where the awg
#       tools are installed. It will not work on macOS unless you have
#       AmneziaWG CLI tools available.
#
# Reads PROVISION_URL and PROVISION_TOKEN from environment or ~/.vpn-provision.env
# Default when run on the controller: PROVISION_URL=http://127.0.0.1:9000

set -euo pipefail

DEVICE_NAME=${1:?Usage: reprovision.sh <device_name> [os_type] [routing|output_dir] [output_dir]}
OS_TYPE=${2:-macos}

if [[ "${3:-}" == "full" || "${3:-}" == "split" ]]; then
    ROUTING=${3}
    OUTPUT_DIR=${4:-~/Documents/Gen8}
else
    ROUTING=${ROUTING:-full}
    OUTPUT_DIR=${3:-~/Documents/Gen8}
fi

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
echo "Pubkey:  $PUB"

RESPONSE=$(curl -sf -X POST "$PROVISION_URL/provision" \
    -H "Authorization: Bearer $PROVISION_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"device_name\":\"$DEVICE_NAME\",\"device_pubkey\":\"$PUB\",\"device_privkey\":\"$PRIV\",\"os_type\":\"$OS_TYPE\",\"routing\":\"$ROUTING\"}")

echo "$RESPONSE" | python3 -m json.tool

mkdir -p "$OUTPUT_DIR"
echo "$RESPONSE" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data['wg_config'])
" > "${OUTPUT_DIR}/${DEVICE_NAME}.conf"

echo "Config written to ${OUTPUT_DIR}/${DEVICE_NAME}.conf"
echo "Assigned to: $(echo "$RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['server_name'], d['client_ip'])")"
