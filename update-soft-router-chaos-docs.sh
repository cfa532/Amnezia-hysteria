#!/usr/bin/env bash
set -euo pipefail

REPO=/home/pi/gen8-doc/github-repo
SCRIPT_SRC=/tmp/gen8-host-vpn-up.repo-fixed
SCRIPT_DST="$REPO/scripts/gen8-host-vpn-up"
DOC="$REPO/docs/Gen8_VPN_Rebuild_Memo.md"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo bash /tmp/update-soft-router-chaos-docs.sh" >&2
  exit 1
fi

if [ ! -f "$SCRIPT_SRC" ]; then
  echo "Missing staged script: $SCRIPT_SRC" >&2
  exit 1
fi

install -m 0755 -o vpncli -g vpncli "$SCRIPT_SRC" "$SCRIPT_DST"

python3 - "$DOC" <<'PY'
from pathlib import Path
import sys

doc = Path(sys.argv[1])
text = doc.read_text()

block = """<!-- BEGIN chaos-host-vpn-self-heal -->
### Chaos Host-VPN Self-Healing

`chaos` depends on two route tables:

```text
table 111  source split-VPN routes built by gen8-ap-split-vpn.service
table 112  host-command routes copied from table 111 for uid vpncli
```

The host wrapper is:

```text
/usr/local/sbin/gen8-host-vpn-up
```

It is called automatically by:

```text
sudo chaos <command>
```

If table `112`, the uid rule, or the nft DNS/NAT table is missing, the wrapper
rebuilds them. If table `111` is missing or too small, the wrapper restarts
`gen8-awg-tahoe.service` and `gen8-ap-split-vpn.service` once, waits briefly,
then retries. If table `111` is still unusable, it falls back to the last
known-good route snapshot:

```text
/var/lib/gen8-router/host-vpn/table-111.routes
```

The snapshot is refreshed automatically whenever `chaos` sees a healthy live
table `111`. This gives `sudo chaos codex` a recovery path even when the live
route table has disappeared and AI access is needed to repair Gen8.

If recovery still fails, `chaos` refuses to run and prints diagnostics instead
of leaking traffic through the direct `eno1` path.

Expected success check:

```sh
sudo chaos bash -lc 'whoami; id -u; curl -4 --connect-timeout 8 --max-time 20 https://ifconfig.me'
```

Expected output shape:

```text
vpncli
996
125.229.161.122
```

Useful state checks:

```sh
ip rule show | grep uidrange
sudo ip route show table 111 | wc -l
sudo ip route show table 112 | wc -l
sudo test -f /var/lib/gen8-router/host-vpn/table-111.routes && wc -l /var/lib/gen8-router/host-vpn/table-111.routes
sudo nft list table ip gen8_host_vpn
```

Failure message to expect if the source table cannot be restored:

```text
chaos cannot enter VPN mode safely.
```

Follow the printed commands for `systemctl status`, `journalctl`, route-table
counts, and `awg show wg-tahoe`.
<!-- END chaos-host-vpn-self-heal -->"""

start = "<!-- BEGIN chaos-host-vpn-self-heal -->"
end = "<!-- END chaos-host-vpn-self-heal -->"
if start in text and end in text:
    before = text[:text.index(start)]
    after = text[text.index(end) + len(end):]
    text = before + block + after
else:
    anchor = "If login prints a browser URL, open it on a Mac/browser, finish the login, then paste the returned code/token back into the gen8 terminal."
    if anchor not in text:
        raise SystemExit("Could not find chaos section anchor in document")
    text = text.replace(anchor, anchor + "\n\n" + block, 1)

doc.write_text(text)
PY

chown vpncli:vpncli "$DOC"

sudo -u vpncli git -C "$REPO" diff -- scripts/gen8-host-vpn-up docs/Gen8_VPN_Rebuild_Memo.md
sudo -u vpncli git -C "$REPO" add scripts/gen8-host-vpn-up docs/Gen8_VPN_Rebuild_Memo.md
sudo -u vpncli git -C "$REPO" commit -m "Harden chaos host VPN recovery"
sudo -u vpncli git -C "$REPO" push origin main
