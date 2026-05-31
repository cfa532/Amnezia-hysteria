#!/usr/bin/env python3
import argparse
import ipaddress
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

APNIC_URLS = [
    "https://ftp.apnic.net/stats/apnic/delegated-apnic-latest",
    "https://ftp.apnic.net/apnic/stats/apnic/delegated-apnic-latest",
    "https://ftp.ripe.net/pub/stats/apnic/delegated-apnic-latest",
]
STATE_DIR = Path("/var/lib/gen8-router/split-cidrs")
RAW_FILE = STATE_DIR / "delegated-apnic-latest"
CHINA_FILE = STATE_DIR / "china-ipv4.txt"
VPN_FILE = STATE_DIR / "non-china-vpn-ipv4.txt"
STAMP_FILE = STATE_DIR / "last-update.txt"
WG_CONF = Path("/etc/amnezia/amneziawg/wg-tahoe.conf")
WG_SETCONF = Path("/etc/amnezia/amneziawg/wg-tahoe.setconf")
HEALTH = Path("/usr/local/sbin/gen8-check-split-vpn")

EXCLUDE_ALWAYS = [
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
]


def run(cmd, check=True):
    return subprocess.run(cmd, text=True, check=check)


def fetch_china_ipv4():
    tmp = RAW_FILE.with_suffix(".tmp")
    last_error = None
    source = None
    for url in APNIC_URLS:
        try:
            subprocess.run(
                [
                    "curl",
                    "-fL",
                    "--retry",
                    "5",
                    "--retry-delay",
                    "10",
                    "--retry-all-errors",
                    "--connect-timeout",
                    "20",
                    "--speed-time",
                    "45",
                    "--speed-limit",
                    "1000",
                    "--max-time",
                    "600",
                    "-o",
                    str(tmp),
                    url,
                ],
                text=True,
                check=True,
            )
            if tmp.stat().st_size < 1000000:
                raise RuntimeError(f"download too small from {url}: {tmp.stat().st_size} bytes")
            tmp.replace(RAW_FILE)
            source = url
            break
        except Exception as exc:
            last_error = exc
            tmp.unlink(missing_ok=True)
    if source is None:
        raise RuntimeError(f"could not download delegated APNIC table: {last_error}")

    data = RAW_FILE.read_text()

    nets = []
    for line in data.splitlines():
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
        if count <= 0 or count & (count - 1):
            continue
        prefix = 32 - (count.bit_length() - 1)
        nets.append(ipaddress.ip_network(f"{start}/{prefix}", strict=False))
    return list(ipaddress.collapse_addresses(nets)), source


def build_non_china_routes(china_nets):
    excludes = list(china_nets)
    excludes.extend(ipaddress.ip_network(x) for x in EXCLUDE_ALWAYS)
    excludes = sorted(ipaddress.collapse_addresses(excludes), key=lambda n: int(n.network_address))

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
                ipaddress.IPv4Address(cursor),
                ipaddress.IPv4Address(start - 1),
            ))
        cursor = max(cursor, end + 1)
    if cursor <= last:
        routes.extend(ipaddress.summarize_address_range(
            ipaddress.IPv4Address(cursor),
            ipaddress.IPv4Address(last),
        ))
    return list(routes)


def replace_allowed_ips(path, allowed_ips_line):
    text = path.read_text()
    new_text, count = re.subn(r"^AllowedIPs\s*=.*$", allowed_ips_line, text, count=1, flags=re.M)
    if count != 1:
        raise RuntimeError(f"AllowedIPs line not found exactly once in {path}")
    path.write_text(new_text)


def backup(path):
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = path.with_suffix(path.suffix + f".bak-split-cidrs-{ts}")
    shutil.copy2(path, out)
    return out


def apply_routes(vpn_routes):
    allowed_ips_line = "AllowedIPs = " + ", ".join(str(n) for n in vpn_routes)
    conf_bak = backup(WG_CONF)
    setconf_bak = backup(WG_SETCONF)
    try:
        replace_allowed_ips(WG_CONF, allowed_ips_line)
        replace_allowed_ips(WG_SETCONF, allowed_ips_line)
        run(["systemctl", "restart", "gen8-awg-tahoe.service"])
        run(["systemctl", "restart", "gen8-ap-split-vpn.service"])
        run([str(HEALTH)])
    except Exception:
        shutil.copy2(conf_bak, WG_CONF)
        shutil.copy2(setconf_bak, WG_SETCONF)
        run(["systemctl", "restart", "gen8-awg-tahoe.service"], check=False)
        run(["systemctl", "restart", "gen8-ap-split-vpn.service"], check=False)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="replace AllowedIPs and restart services after generating routes")
    args = parser.parse_args()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    china, source = fetch_china_ipv4()
    vpn = build_non_china_routes(china)
    CHINA_FILE.write_text("\n".join(str(n) for n in china) + "\n")
    VPN_FILE.write_text("\n".join(str(n) for n in vpn) + "\n")
    STAMP_FILE.write_text(
        f"updated={time.strftime('%Y-%m-%d %H:%M:%S %z')}\n"
        f"china_ipv4={len(china)}\n"
        f"vpn_ipv4={len(vpn)}\n"
        f"source={source}\n"
    )
    print(f"generated china_ipv4={len(china)} vpn_ipv4={len(vpn)}")
    print(f"wrote {CHINA_FILE}")
    print(f"wrote {VPN_FILE}")
    if args.apply:
        apply_routes(vpn)
        print("applied split CIDR update and health check passed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
