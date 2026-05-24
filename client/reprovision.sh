#!/bin/bash
# Reprovision a device against the VPN controller.
# Usage: reprovision.sh <device_name> <os_type> <output_dir>
#   device_name: mac1, mac2, ios1, etc.
#   os_type:     macos | ios | android
#   output_dir:  where to write <device_name>.conf
#
# Reads PROVISION_URL and PROVISION_TOKEN from environment or ~/.vpn-provision.env

set -euo pipefail

DEVICE_NAME=${1:?Usage: reprovision.sh <device_name> <os_type> [output_dir]}
OS_TYPE=${2:-macos}
OUTPUT_DIR=${3:-~/Documents/Gen8}

ENV_FILE=~/.vpn-provision.env
[ -f "$ENV_FILE" ] && source "$ENV_FILE"

PROVISION_URL=${PROVISION_URL:?Set PROVISION_URL in $ENV_FILE}
PROVISION_TOKEN=${PROVISION_TOKEN:?Set PROVISION_TOKEN in $ENV_FILE}

# Generate a fresh keypair for this device
PRIV=$(awg genkey)
PUB=$(echo "$PRIV" | awg pubkey)

echo "Device:  $DEVICE_NAME"
echo "OS type: $OS_TYPE"
echo "Pubkey:  $PUB"

RESPONSE=$(curl -sf -X POST "$PROVISION_URL/provision" \
    -H "Authorization: Bearer $PROVISION_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"device_name\":\"$DEVICE_NAME\",\"device_pubkey\":\"$PUB\",\"device_privkey\":\"$PRIV\",\"os_type\":\"$OS_TYPE\"}")

echo "$RESPONSE" | python3 -m json.tool

# Write the wg_config to file
echo "$RESPONSE" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data['wg_config'])
" > "${OUTPUT_DIR}/${DEVICE_NAME}.conf"

echo "Config written to ${OUTPUT_DIR}/${DEVICE_NAME}.conf"
echo "Assigned to: $(echo "$RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['server_name'], d['client_ip'])")"
