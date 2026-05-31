# Client Setup (macOS)

Sets up the VPN on a regular macOS machine — a single active physical interface
(one WiFi or Ethernet link, no built-in soft-router VPN).

> Setting up **Tahoe** or **Sequoia** (dual-NIC)? Use [dual-nic-setup.md](dual-nic-setup.md).
> Setting up an **iPhone/iPad**? Use [ios-setup.md](ios-setup.md).

macOS connects to AmneziaWG **directly** on UDP 443 (the former Hysteria2 proxy
layer has been retired). A small route-pinner daemon defeats a macOS routing
quirk; that's the only extra piece.

```
AmneziaWG (utun) ──UDP 443──▶ nebuchadnezzar.fireshare.uk
                               (Cloudflare DNS round-robin: tn1 / minipc)
     AllowedIPs = split list (China direct, everything else via VPN)
```

## Prerequisites

- macOS 13 Ventura or later
- Your provisioned `macN.conf` from the admin
- Repo cloned at `~/Documents/GitHub/Amnezia-hysteria`

---

## Step 1 — Import the AmneziaWG config

Install **AmneziaWG** from the Mac App Store, then import your `macN.conf`:

1. AmneziaWG → **+** → **Import tunnel(s) from file** → select `macN.conf`
2. Click **Allow** when prompted to add a VPN configuration

Confirm the Peer section shows `Endpoint = nebuchadnezzar.fireshare.uk:443` and a
long split CIDR `AllowedIPs`. (If it shows `127.0.0.1:1443`, that's an old
Hysteria-era config — re-import the current `macN.conf`.)

---

## Step 2 — Install the route-pinner

When AmneziaWG activates, macOS clones a `/32` route for the endpoint IP onto the
`utun` interface, which loops the tunnel's own packets and breaks the handshake.
The `awg-en1-route` daemon resolves the endpoint hostname and re-pins each
endpoint IP to the **physical** interface's gateway, so it bypasses utun.

```bash
# Install the daemon and script
sudo cp ~/Documents/GitHub/Amnezia-hysteria/client/awg-en1-route.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/awg-en1-route.sh
sudo cp ~/Documents/GitHub/Amnezia-hysteria/client/uk.fireshare.awg-en1-route.plist /Library/LaunchDaemons/
sudo launchctl bootstrap system /Library/LaunchDaemons/uk.fireshare.awg-en1-route.plist
```

> **Single-NIC interface name.** The daemon defaults to `en1`. If this machine's
> active interface is `en0` (common on WiFi-only Macs), set it before bootstrap:
> edit `/Library/LaunchDaemons/uk.fireshare.awg-en1-route.plist` and add an
> `EnvironmentVariables` dict with `AWG_ROUTE_IFACE` = `en0`. Check your active
> interface with `route get default | awk '/interface:/{print $2}'`.

---

## Step 3 — Connect

Toggle the tunnel **on** in the AmneziaWG app.

---

## Verification

```bash
# Route-pinner running
sudo launchctl list uk.fireshare.awg-en1-route

# Endpoint IPs pinned to the physical interface (not utunX)
for ip in $(dig +short nebuchadnezzar.fireshare.uk A); do route get "$ip" | awk '/interface:/{print $2}'; done

# Traffic exits through the VPN
curl -s https://api.ipify.org   # should return a VPN server IP, not your ISP IP
```

---

## Split routing

Your `macN.conf` `AllowedIPs` is the **honest full** non-China CIDR list (~11975
routes): Chinese IP ranges route direct via your ISP; everything else goes through
the VPN. Server IPs do **not** need to be excluded — the route-pinner keeps the
endpoint off the tunnel.

> macOS uses the **full** list (no config-size limit). This is **not**
> `/etc/vpn-controller/split-allowed-ips.txt` — that file is the *reduced
> "Taobao-direct"* list for iOS/Android (kept < 128 KB and curated so major Chinese
> apps route direct). Don't put the reduced list on a Mac; it shrinks coverage. See
> [regional-lb-design.md](regional-lb-design.md#split-allowedips).

If provisioned with `routing=full`, all traffic goes through the VPN — contact
the admin to reprovision with `routing=split`.

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| No handshake / no internet with tunnel on | `route get <server-ip>` shows `utunX` → daemon not pinning. `sudo launchctl kickstart -k system/uk.fireshare.awg-en1-route`, then `tail /tmp/awg-en1-route.log` |
| Log says "no gateway on en1 yet" | Wrong interface — set `AWG_ROUTE_IFACE` (see Step 2 note) |
| Daemon won't load, "Input/output error" (errno 5) | Stale job: `sudo launchctl bootout system/uk.fireshare.awg-en1-route` then bootstrap again |
| curl returns ISP IP | Tunnel inactive or handshake stale — toggle off/on |
