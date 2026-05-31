#!/usr/bin/env python3
"""Regenerate the FULL non-China IPv4 split list for macOS provisioning.

Fetches the APNIC delegated-stats table, extracts CN IPv4 allocations, computes
the complement (everything except China + RFC1918/special ranges), and writes it
to /etc/vpn-controller/split-allowed-ips-full.txt — the list provision.py serves
to macOS clients (full coverage; iOS/Android get the smaller reduced list).

Mirrors gen8's `gen8-update-split-cidrs` generator (same APNIC source + algorithm),
minus the wg-tahoe apply step, so the router and the provisioning API stay on the
same list. Run weekly by vpn-update-split-full.timer.
"""
import ipaddress
import subprocess
import sys
import time
from pathlib import Path

APNIC_URLS = [
    "https://ftp.apnic.net/stats/apnic/delegated-apnic-latest",
    "https://ftp.apnic.net/apnic/stats/apnic/delegated-apnic-latest",
    "https://ftp.ripe.net/pub/stats/apnic/delegated-apnic-latest",
]
STATE_DIR  = Path("/var/lib/vpn-controller")
RAW_FILE   = STATE_DIR / "delegated-apnic-latest"
OUT_FILE   = Path("/etc/vpn-controller/split-allowed-ips-full.txt")
STAMP_FILE = STATE_DIR / "split-full-last-update.txt"

EXCLUDE_ALWAYS = [
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
    "224.0.0.0/4", "240.0.0.0/4",
]


def fetch_china_ipv4():
    tmp = RAW_FILE.with_suffix(".tmp")
    last_error = None
    source = None
    for url in APNIC_URLS:
        try:
            subprocess.run(
                ["curl", "-fL", "--retry", "5", "--retry-delay", "10",
                 "--retry-all-errors", "--connect-timeout", "20",
                 "--speed-time", "45", "--speed-limit", "1000",
                 "--max-time", "600", "-o", str(tmp), url],
                text=True, check=True,
            )
            if tmp.stat().st_size < 1_000_000:
                raise RuntimeError(f"download too small from {url}: {tmp.stat().st_size} bytes")
            tmp.replace(RAW_FILE)
            source = url
            break
        except Exception as exc:
            last_error = exc
            tmp.unlink(missing_ok=True)
    if source is None:
        raise RuntimeError(f"could not download delegated APNIC table: {last_error}")

    nets = []
    for line in RAW_FILE.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) < 7:
            continue
        registry, cc, typ, start, value, _date, status = parts[:7]
        if registry != "apnic" or cc != "CN" or typ != "ipv4":
            continue
        if status not in {"allocated", "assigned"}:
            continue
        count = int(value)
        if count <= 0 or count & (count - 1):   # skip non power-of-two blocks
            continue
        prefix = 32 - (count.bit_length() - 1)
        nets.append(ipaddress.ip_network(f"{start}/{prefix}", strict=False))
    return list(ipaddress.collapse_addresses(nets)), source


def build_non_china_routes(china_nets):
    excludes = list(china_nets)
    excludes.extend(ipaddress.ip_network(x) for x in EXCLUDE_ALWAYS)
    excludes = sorted(ipaddress.collapse_addresses(excludes),
                      key=lambda n: int(n.network_address))

    intervals = [(int(n.network_address), int(n.broadcast_address)) for n in excludes]
    merged = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)

    routes = []
    cursor = 0
    last = (1 << 32) - 1
    for start, end in merged:
        if cursor < start:
            routes.extend(ipaddress.summarize_address_range(
                ipaddress.IPv4Address(cursor), ipaddress.IPv4Address(start - 1)))
        cursor = max(cursor, end + 1)
    if cursor <= last:
        routes.extend(ipaddress.summarize_address_range(
            ipaddress.IPv4Address(cursor), ipaddress.IPv4Address(last)))
    return list(routes)


def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    china, source = fetch_china_ipv4()
    vpn = build_non_china_routes(china)
    if len(vpn) < 5000:                          # sanity guard against a bad fetch
        raise RuntimeError(f"refusing to write suspiciously small list: {len(vpn)} routes")

    tmp = OUT_FILE.with_suffix(".tmp")
    tmp.write_text("\n".join(str(n) for n in vpn) + "\n")
    tmp.replace(OUT_FILE)
    STAMP_FILE.write_text(
        f"updated={time.strftime('%Y-%m-%d %H:%M:%S %z')}\n"
        f"china_ipv4={len(china)}\n"
        f"vpn_ipv4={len(vpn)}\n"
        f"source={source}\n"
    )
    print(f"wrote {OUT_FILE}: china_ipv4={len(china)} vpn_ipv4={len(vpn)} (source={source})")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
