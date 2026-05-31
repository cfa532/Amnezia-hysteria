# Dual-NIC macOS Setup

This guide covers any macOS machine with **two physical network interfaces** where:

- **en0** (wired) connects through the gen8 soft router, which runs its own always-on VPN
- **en1** (WiFi) connects directly to the home router — the clean path for this machine's own VPN

Machines with this topology: **mac1 (Sequoia)**, **mac2 (Tahoe)**.

> **Note:** This stack used to tunnel AmneziaWG over a local Hysteria2 proxy. That
> has been **retired** — macOS now connects to AmneziaWG **directly** on UDP 443,
> exactly like iOS. The only macOS-specific piece left is a small route-pinner
> daemon (`awg-en1-route`) that keeps the tunnel on en1.

---

## Architecture

```
AmneziaWG (utun) ──UDP 443──▶ nebuchadnezzar.fireshare.uk
                               (Cloudflare DNS round-robin: tn1 / minipc)
     AllowedIPs = split list (China direct, everything else via VPN)
```

The Mac runs its **own** AmneziaWG tunnel over en1. The endpoint hostname
round-robins across the backend servers; whichever one DNS hands out, the Mac
connects to it directly.

### Why a route-pinner is still needed

Two interface problems have to be solved for the Mac's own tunnel:

1. **The macOS clone-route quirk.** When AmneziaWG activates, macOS clones a `/32`
   host route for the endpoint IP onto the `utun` interface. That captures the
   tunnel's own handshake/keepalive packets and loops them back into the tunnel,
   so the handshake can never complete.
2. **The wrong physical interface.** Left alone, the endpoint route can fall onto
   **en0** (the gen8 wired link). en0 leads to the soft router's own VPN, so the
   Mac's tunnel would be needlessly wrapped a second time.

The **`awg-en1-route`** LaunchDaemon fixes both. It resolves the endpoint
hostname's A records and pins each one to **en1's gateway**, so the tunnel always
egresses via the clean WiFi path — never utun, never en0. Because it reads the
endpoint from DNS, it is **server-agnostic**: add, move, or remove a backend and
the Mac adapts on the next route-table change, with no client edits.

### Failover

DNS-based. The controller removes a dead server's A record from
`nebuchadnezzar.fireshare.uk`; AWG re-resolves on its next handshake and lands on
a surviving server. No local proxy or failover agent.

---

## Setup

### Prerequisites

- macOS 13 Ventura or later
- WiFi (en1) connected to the home router
- Your provisioned `macN.conf` (e.g. in `~/Documents/Gen8/`)
- Repo cloned at `~/Documents/GitHub/Amnezia-hysteria`

### Step 1 — Import the AmneziaWG config

Install **AmneziaWG** from the Mac App Store, then import your `macN.conf`:

1. AmneziaWG → **+** → **Import tunnel(s) from file** → select `macN.conf`
2. Click **Allow** when macOS prompts to add a VPN configuration

Confirm the Peer section shows:

| Field | Expected value |
|-------|---------------|
| Endpoint | `nebuchadnezzar.fireshare.uk:443` |
| AllowedIPs | a long split CIDR list (China excluded) |

> If the endpoint is `127.0.0.1:1443`, that is an old Hysteria-era config —
> re-import the current `macN.conf`.

### Step 2 — Install the route-pinner

One script installs and loads everything (it also removes any leftover
Hysteria-era agents). It needs `sudo` once for the LaunchDaemon:

```bash
cd ~/Documents/GitHub/Amnezia-hysteria/client
./setup-dual-nic.sh
```

The daemon (`uk.fireshare.awg-en1-route`) runs `/usr/local/bin/awg-en1-route.sh`,
logging to `/tmp/awg-en1-route.log`.

### Step 3 — Connect

Toggle the tunnel **on** in the AmneziaWG app.

---

## Verification

```bash
# 1. Route-pinner is running
sudo launchctl list uk.fireshare.awg-en1-route

# 2. Each endpoint IP is pinned to en1 (not utun, not en0)
for ip in $(dig +short nebuchadnezzar.fireshare.uk A); do route get "$ip" | awk '/interface:/{print $2}'; done
# expect: en1   en1

# 3. Traffic exits through the VPN
curl -s https://api.ipify.org   # should return a VPN server IP (tn1 or minipc), not your ISP IP
```

---

## Troubleshooting

### AWG shows no handshake / no internet when the tunnel is on

The endpoint is probably looping through utun or pinned to the wrong interface.

```bash
sudo launchctl list uk.fireshare.awg-en1-route          # must show a PID
for ip in $(dig +short nebuchadnezzar.fireshare.uk A); do route get "$ip" | awk '/interface:/{print $2}'; done
# must be en1 for every IP; if you see utunX or en0, kick the daemon:
sudo launchctl kickstart -k system/uk.fireshare.awg-en1-route
tail -20 /tmp/awg-en1-route.log
```

### Route-pinner log says "no gateway on en1 yet"

en1 (WiFi) is not connected. Join the home WiFi, then:

```bash
sudo launchctl kickstart -k system/uk.fireshare.awg-en1-route
```

### Daemon won't load with "Input/output error" (errno 5)

A stale job under the same label. Bootout, then bootstrap:

```bash
sudo launchctl bootout system/uk.fireshare.awg-en1-route 2>/dev/null
sudo launchctl bootstrap system /Library/LaunchDaemons/uk.fireshare.awg-en1-route.plist
```

### curl returns your ISP IP instead of a VPN IP

The tunnel isn't active, or the AWG handshake is stale (> 3 min). Toggle the
tunnel off and on, then re-check the handshake timestamp in the app.
