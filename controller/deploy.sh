#!/bin/bash
# Deploy or update the VPN controller on tn1 (controller host).
# Run on tn1 as root. Safe to re-run — idempotent.
#
# Assumes:
#   - /etc/vpn-controller/controller.yaml already configured
#   - /etc/vpn-controller/api.token exists (generate once: python3 -c "import secrets; print(secrets.token_urlsafe(32))")
#   - /etc/vpn-controller/minipc-key SSH key in place
#   - Shared AWG keypair already deployed to all servers
#   - Source files at /opt/vpn-controller/ (copy from repo before running)
set -euo pipefail

CTRL_DIR=/etc/vpn-controller
INSTALL_DIR=/opt/vpn-controller
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ── 1. Install deps ───────────────────────────────────────────────────────────
log "Installing system deps..."
apt-get install -y python3-pip sshpass dnsutils -q
python3 -m pip install fastapi uvicorn pyyaml --break-system-packages -q

# ── 2. Install source files ───────────────────────────────────────────────────
log "Installing controller files..."
mkdir -p "$INSTALL_DIR"
cp "$INSTALL_DIR/provision.py" "$INSTALL_DIR/provision.py.bak" 2>/dev/null || true
cp "$SCRIPT_DIR/provision.py" "$SCRIPT_DIR/health.py" \
    "$SCRIPT_DIR/update-china-firm-bypass.py" "$INSTALL_DIR/"
install -m 0755 "$SCRIPT_DIR/../client/reprovision.sh" \
    "$INSTALL_DIR/reprovision.sh"
install -m 0644 "$SCRIPT_DIR/china-firm-bypass-domains.txt" \
    "$CTRL_DIR/china-firm-bypass-domains.txt"
install -m 0644 "$SCRIPT_DIR/china-firm-bypass-static-cidrs.txt" \
    "$CTRL_DIR/china-firm-bypass-static-cidrs.txt"
install -m 0644 "$SCRIPT_DIR/china-qr-bypass-domains.txt" \
    "$CTRL_DIR/china-qr-bypass-domains.txt"
install -m 0644 "$SCRIPT_DIR/china-qr-bypass-static-cidrs.txt" \
    "$CTRL_DIR/china-qr-bypass-static-cidrs.txt"

# ── 3. Generate API token if missing ─────────────────────────────────────────
if [ ! -f "$CTRL_DIR/api.token" ]; then
    python3 -c "import secrets; print(secrets.token_urlsafe(32))" \
        > "$CTRL_DIR/api.token"
    chmod 600 "$CTRL_DIR/api.token"
    log "Generated new API token"
fi
log "API token: $(cat "$CTRL_DIR/api.token")"

# ── 4. Install systemd units ──────────────────────────────────────────────────
log "Installing systemd units..."

cat > /etc/systemd/system/vpn-controller.service << 'SVC'
[Unit]
Description=VPN health controller
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/vpn-controller
ExecStart=/usr/bin/python3 /opt/vpn-controller/health.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SVC

cat > /etc/systemd/system/vpn-provision.service << 'SVC'
[Unit]
Description=VPN provisioning API
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/vpn-controller
ExecStart=/usr/bin/python3 /opt/vpn-controller/provision.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVC

install -m 0644 "$SCRIPT_DIR/vpn-update-china-firm-bypass.service" \
    /etc/systemd/system/vpn-update-china-firm-bypass.service
install -m 0644 "$SCRIPT_DIR/vpn-update-china-firm-bypass.timer" \
    /etc/systemd/system/vpn-update-china-firm-bypass.timer

# ── 5. Start / restart services ───────────────────────────────────────────────
python3 "$INSTALL_DIR/update-china-firm-bypass.py"
systemctl disable --now vpn-update-split-full.timer 2>/dev/null || true
systemctl daemon-reload
systemctl enable vpn-controller vpn-provision vpn-update-china-firm-bypass.timer
systemctl restart vpn-controller vpn-provision
systemctl restart vpn-update-china-firm-bypass.timer
sleep 2

log "=== Status ==="
systemctl is-active vpn-controller && log "vpn-controller: active" || log "vpn-controller: FAILED"
systemctl is-active vpn-provision  && log "vpn-provision:  active" || log "vpn-provision:  FAILED"

API_TOKEN=$(cat "$CTRL_DIR/api.token")
log "=== Deploy complete ==="
log "Provision API: http://127.0.0.1:9000"
log "Test: curl -s http://127.0.0.1:9000/clients -H 'Authorization: Bearer $API_TOKEN'"
