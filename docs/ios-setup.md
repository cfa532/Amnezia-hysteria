# iOS Client Setup

```
AmneziaWG app ──UDP──▶ nebuchadnezzar.fireshare.uk:443
                                    │
                          VPN server (awg0, AmneziaWG)
                                    │
                               internet
```

iOS connects directly to the server on UDP port 443. There is no local Hysteria2 daemon — it is macOS-only.

---

## Prerequisites

- **AmneziaWG** from the App Store — search "AmneziaWG" and install the app by the Amnezia team. **Do not use the plain WireGuard app.** WireGuard silently ignores the obfuscation fields (Jc, Jmin, H1–H4) and the handshake will always time out.
- Your `iosN.conf` file from the admin (unique to your device).

---

## Step 1 — Get the config onto your iPhone

AirDrop `iosN.conf` from the Mac to your iPhone:

1. On Mac: right-click `iosN.conf` → Share → AirDrop → select your iPhone
2. On iPhone: accept the AirDrop → tap **Open with AmneziaWG**

Alternatively share via iCloud Drive, Messages, or email — any method that lets you open the file with AmneziaWG.

---

## Step 2 — Verify the imported config

In the AmneziaWG app, tap the tunnel → check the Peer section:

| Field | Expected value |
|-------|---------------|
| Endpoint | `nebuchadnezzar.fireshare.uk:443` |
| Public Key | `AQgL8TfJomzJTcNxq/2mhKzgZfOp7eLuFEnsH0PDQhc=` |
| AllowedIPs | Long IPv4 CIDR list (reduced split profile) |
| MTU | `1180` |
| PersistentKeepalive | `10` |

If the endpoint shows `127.0.0.1:1443`, delete the tunnel and re-import — that is an old config.

If the tunnel name contains `full-test`, delete it after testing. Those profiles
were used only to isolate server/MTU behavior and are not stable production
iPhone configs.

---

## Step 3 — Connect

Toggle the tunnel on in the AmneziaWG app.

---

## Verification

Open Safari and go to `https://api.ipify.org`. The returned IP should be a VPN server IP (tn1 or minipc), not your ISP's IP.

---

## Speed testing

- **Fast.com** — open in Safari, tap start. Simple download test.
- **Speedtest** (Ookla) — free App Store app. Measures download, upload, and ping. Pick a Singapore or Tokyo server for a realistic reading of what the tunnel delivers.
- **YouTube** — play a 4K video. If it sustains 2160p, you have plenty of bandwidth.

Run one test with the tunnel off and one with it on to measure actual overhead.

---

## Routing modes

| Mode | AllowedIPs | Effect |
|------|-----------|--------|
| Split | Reduced long IPv4 CIDR list | Chinese IPs bypass VPN; everything else goes through |
| Full test only | `0.0.0.0/1, 128.0.0.0/1` plus endpoint exclusion | Diagnostic only; can hang on iOS |

Contact the admin to switch modes — this is set at provisioning time.

### iOS full-tunnel hang discovered during testing

The full-test profile can complete the initial handshake and even pass traffic,
but later the iOS AmneziaWG backend may pause itself. The log pattern is:

```text
Network change detected with unsatisfied route
Connectivity offline, pausing backend.
Path update state: state=temporaryShutdown(...)
```

When this happens, the app can still show `connected`, but traffic is already
dead. The reduced split profile avoids this route-monitor failure in current
testing and should be used for iPhone/iPad.

---

## Troubleshooting

### Handshake always times out

1. **Wrong app** — confirm you are using AmneziaWG, not WireGuard. The WireGuard app won't work.
2. **Old config** — if the endpoint is `127.0.0.1:1443`, delete and re-import the current `iosN.conf`.
3. **Port blocked** — UDP 443 must be open in the cloud security group (Alibaba/Tencent console). If you still time out on Wi-Fi, try cellular to confirm the server is up.

### Connected but no internet

If the tunnel is up but DNS fails, toggle the tunnel off and back on to let the DNS reset. Current iOS configs use IPv4 DNS only.

### Slow on cellular, fast on Wi-Fi

Cellular UDP performance varies by carrier and location. iOS uses raw AWG over UDP 443, so path quality matters.
