# gen8 ↔ minipc — FEC to cut cross-strait packet loss

The gen8 (China) → minipc (Taiwan) AmneziaWG path runs ~15–40% packet loss at
peak. Plain AWG passes the loss through (TCP survives but retransmits over a
~130 ms RTT → sluggish); Hysteria2 made it *worse* (QUIC datagrams aren't
retransmitted). The fix is **Forward Error Correction**: send redundant packets
so the receiver reconstructs losses without retransmitting.

Tool: **UDPspeeder** (`speederv2`, wangyu-/UDPspeeder), inserted between gen8's
AWG and minipc's AWG.

```
gen8 AWG (wg-tahoe, Endpoint 127.0.0.1:4000)
     │
     ▼  speederv2 -c  (FEC encode, 10:10)
     │  UDP 80  ──────────── cross-strait ───────────▶  minipc
     ▼                                                    speederv2 -s (FEC decode)
                                                          │
                                                          ▼ 127.0.0.1:443 (minipc AWG)
```

## Why UDP 80

The path **blocks most UDP ports** (tested: 4096, 53, 500, 4500, 8443, 1194, 2083
all dropped) and only passes **80** and **443**. AWG owns 443 for direct clients
(nebuchadnezzar round-robin), so the FEC tunnel runs over **UDP 80**. FEC repairs
whatever loss that port carries.

## Results (measured 2026-05-31, peak)

| | Direct AWG | AWG + FEC (10:10) |
|--|--|--|
| 5 MB download | 1.23 MB/s | 2.6–2.8 MB/s |
| 20 MB download | — | **4.16 MB/s** |

~2–3.4× throughput. FEC 10:10 = 50% redundancy (≈2× raw traffic), affordable
because the bottleneck is loss, not bandwidth (minipc's own link is 8.5 MB/s).
Sparse single packets (e.g. ICMP ping) still show loss — FEC needs a packet
stream to batch redundancy — but real bulk/streaming traffic gets the full gain.

## Components

- **minipc** (server): `/usr/local/bin/speederv2`, unit `udpspeeder-gen8.service`
  (`-s -l0.0.0.0:80 -r127.0.0.1:443 -f10:10 -k … --mode 0`). UDP 80 opened in
  iptables.
- **gen8** (client): `/usr/local/bin/speederv2`, unit `udpspeeder.service`
  (`-c -l127.0.0.1:4000 -r125.229.161.122:80 -f10:10 -k … --mode 0`).
- **gen8** `wg-tahoe.setconf` `Endpoint = 127.0.0.1:4000`.
- Both `speederv2` units are enabled (survive reboot). Binaries: x86_64
  (`speederv2_amd64` from the UDPspeeder release).

## Rollback to direct AWG

`/usr/local/sbin/gen8-fec-rollback` on gen8: restores `Endpoint =
125.229.161.122:443`, stops `udpspeeder.service`, restarts the AWG + split-VPN +
DoH chain.

## Tuning

- **Redundancy:** `-f<data>:<fec>`. Raise the FEC share at worse loss
  (e.g. `-f10:20`), lower it to save bandwidth when the path is clean. Must match
  on both ends.
- **If UDP 80 starts getting blocked too**, re-probe ports and move both `-l`/`-r`
  to whatever still passes (443 is unavailable — AWG owns it).
