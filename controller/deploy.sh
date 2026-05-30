#!/bin/bash
# Full regional-lb deployment script.
# Run on the controller server (tn2) as root.
# Generates per-server keypairs, updates awg0 on each server,
# registers server pubkeys in controller state, starts provision API.
set -euo pipefail

CFG=/etc/vpn-controller/controller.yaml
STATE=/etc/vpn-controller/clients.json
A1_IP=8.222.164.32
TN2_IP=43.160.238.86

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ── 1. Install Python deps ────────────────────────────────────────────────────
log "Installing deps..."
apt-get install -y python3-yaml python3-pip sshpass -q
pip3 install fastapi uvicorn requests pyyaml --break-system-packages -q

# ── 2. Generate per-server keypairs ──────────────────────────────────────────
log "Generating server keypairs..."
mkdir -p /etc/vpn-controller/keys

A1_PRIV=$(awg genkey)
A1_PUB=$(echo "$A1_PRIV" | awg pubkey)
TN2_PRIV=$(awg genkey)
TN2_PUB=$(echo "$TN2_PRIV" | awg pubkey)

echo "$A1_PRIV" > /etc/vpn-controller/keys/a1.priv
echo "$A1_PUB"  > /etc/vpn-controller/keys/a1.pub
echo "$TN2_PRIV" > /etc/vpn-controller/keys/tn2.priv
echo "$TN2_PUB"  > /etc/vpn-controller/keys/tn2.pub
chmod 600 /etc/vpn-controller/keys/*.priv
log "a1  pubkey: $A1_PUB"
log "tn2 pubkey: $TN2_PUB"

# ── 3. Build awg0.conf for each server ───────────────────────────────────────
build_conf() {
    local PRIV=$1 SUBNET=$2 GATEWAY=$3
    cat << EOF
[Interface]
Address = ${GATEWAY}/24
ListenPort = 443
PrivateKey = ${PRIV}

Jc = 4
Jmin = 40
Jmax = 70
S1 = 30
S2 = 40
S3 = 30
S4 = 40
H1 = 11223
H2 = 44556
H3 = 77889
H4 = 99001

PostUp = iptables -t nat -A POSTROUTING -s ${SUBNET} -o eth0 -j MASQUERADE
PostUp = iptables -I FORWARD 1 -i awg0 -j ACCEPT
PostUp = iptables -I FORWARD 1 -o awg0 -j ACCEPT
PostDown = iptables -t nat -D POSTROUTING -s ${SUBNET} -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i awg0 -j ACCEPT
PostDown = iptables -D FORWARD -o awg0 -j ACCEPT
EOF
}

build_conf "$A1_PRIV"  "10.8.0.0/24" "10.8.0.1" > /tmp/a1-awg0.conf
build_conf "$TN2_PRIV" "10.8.1.0/24" "10.8.1.1" > /tmp/tn2-awg0.conf

# ── 4. Push new configs and restart awg0 ─────────────────────────────────────
log "Deploying a1..."
SSH_A1="ssh -i /etc/vpn-controller/keys/a1-ssh.pem -o StrictHostKeyChecking=no root@${A1_IP}"
scp -i /etc/vpn-controller/keys/a1-ssh.pem -o StrictHostKeyChecking=no \
    /tmp/a1-awg0.conf root@${A1_IP}:/etc/amnezia/amneziawg/awg0.conf
$SSH_A1 "awg-quick down awg0; awg-quick up awg0 && awg show awg0 | grep 'public key'"

log "Deploying tn2..."
cp /tmp/tn2-awg0.conf /etc/amnezia/amneziawg/awg0.conf
awg-quick down awg0; awg-quick up awg0
awg show awg0 | grep 'public key'

# ── 5. Register server pubkeys and subnet info in controller state ────────────
log "Updating controller state..."
python3 - << PYEOF
import json
from pathlib import Path

state_path = Path("$STATE")
state = json.loads(state_path.read_text()) if state_path.exists() else {"clients": {}, "server_keys": {}}

state["server_keys"]["$A1_IP"]  = "$A1_PUB"
state["server_keys"]["$TN2_IP"] = "$TN2_PUB"
state_path.write_text(json.dumps(state, indent=2))
print("State updated.")
PYEOF

# Update controller.yaml with per-server subnet and gateway
python3 - << PYEOF
import yaml
from pathlib import Path

cfg_path = Path("$CFG")
cfg = yaml.safe_load(cfg_path.read_text())
for s in cfg["servers"]:
    if s["ip"] == "$A1_IP":
        s["subnet"] = "10.8.0.0/24"
        s["gateway"] = "10.8.0.1"
        s["ssh_key"] = "/etc/vpn-controller/keys/a1-ssh.pem"
    elif s["ip"] == "$TN2_IP":
        s["subnet"] = "10.8.1.0/24"
        s["gateway"] = "10.8.1.1"
        s["ssh_pass"] = "zaq12WSX"
cfg_path.write_text(yaml.dump(cfg, default_flow_style=False))
print("Config updated.")
PYEOF

# ── 6. Generate API token ─────────────────────────────────────────────────────
if [ ! -f /etc/vpn-controller/api.token ]; then
    python3 -c "import secrets; print(secrets.token_urlsafe(32))" \
        > /etc/vpn-controller/api.token
    chmod 600 /etc/vpn-controller/api.token
fi
API_TOKEN=$(cat /etc/vpn-controller/api.token)
log "API token: $API_TOKEN"

# ── 7. Install and start provisioning API ────────────────────────────────────
cp /opt/vpn-controller/provision.py /opt/vpn-controller/provision.py

cat > /etc/systemd/system/vpn-provision.service << 'SVC'
[Unit]
Description=VPN provisioning API
After=network-online.target vpn-controller.service

[Service]
Type=simple
WorkingDirectory=/opt/vpn-controller
ExecStart=/usr/bin/python3 /opt/vpn-controller/provision.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVC

systemctl daemon-reload
systemctl enable vpn-provision
systemctl restart vpn-provision
sleep 2
systemctl is-active vpn-provision

log "=== Deploy complete ==="
log "Provision API: http://127.0.0.1:9000"
log "Test: curl -s http://127.0.0.1:9000/clients -H 'Authorization: Bearer $API_TOKEN'"
