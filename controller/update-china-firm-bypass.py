#!/usr/bin/env python3
"""Refresh the full and priority-QR IPv4 bypass sets used by AWG clients.

Static firm/cloud CIDRs are combined with /24 networks observed by resolving a
curated domain list through mainland resolvers. The provisioning API then
computes the inverse: these ranges go direct and all other public IPv4 uses AWG.
"""

import argparse
import ipaddress
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_DOMAINS = Path("/etc/vpn-controller/china-firm-bypass-domains.txt")
DEFAULT_STATIC = Path("/etc/vpn-controller/china-firm-bypass-static-cidrs.txt")
DEFAULT_OUTPUT = Path("/etc/vpn-controller/china-firm-bypass-cidrs.txt")
DEFAULT_QR_DOMAINS = Path("/etc/vpn-controller/china-qr-bypass-domains.txt")
DEFAULT_QR_STATIC = Path("/etc/vpn-controller/china-qr-bypass-static-cidrs.txt")
DEFAULT_QR_OUTPUT = Path("/etc/vpn-controller/china-qr-bypass-cidrs.txt")
DEFAULT_STAMP = Path("/var/lib/vpn-controller/china-firm-bypass-last-update.txt")
CHINA_DNS_SERVERS = ("223.5.5.5", "119.29.29.29")


def _content_lines(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    return lines


def load_static(path: Path) -> list[ipaddress.IPv4Network]:
    networks = []
    for token in _content_lines(path):
        network = ipaddress.ip_network(token, strict=True)
        if network.version != 4:
            raise ValueError(f"IPv6 is not supported in firm bypass data: {token}")
        networks.append(network)
    return networks


def resolve_ipv4(domain: str) -> set[ipaddress.IPv4Address]:
    addresses = set()
    for server in CHINA_DNS_SERVERS:
        result = subprocess.run(
            ["dig", "+time=4", "+tries=2", "+short", f"@{server}", "A", domain],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        for token in result.stdout.split():
            try:
                address = ipaddress.ip_address(token.rstrip("."))
            except ValueError:
                continue
            if address.version == 4:
                addresses.add(address)
    return addresses


def build_bypass(domains_path: Path, static_path: Path):
    domains = _content_lines(domains_path)
    networks = load_static(static_path)
    resolved_domains = 0
    resolved_addresses = 0

    for domain in domains:
        addresses = resolve_ipv4(domain)
        if addresses:
            resolved_domains += 1
        resolved_addresses += len(addresses)
        for address in addresses:
            networks.append(ipaddress.ip_network(f"{address}/24", strict=False))

    minimum = max(3, len(domains) // 3)
    if resolved_domains < minimum:
        raise RuntimeError(
            f"only {resolved_domains}/{len(domains)} firm domains resolved; "
            "refusing to replace the last known-good bypass set"
        )

    collapsed = list(ipaddress.collapse_addresses(networks))
    return collapsed, len(domains), resolved_domains, resolved_addresses


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domains", type=Path, default=DEFAULT_DOMAINS)
    parser.add_argument("--static", type=Path, default=DEFAULT_STATIC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--qr-domains", type=Path, default=DEFAULT_QR_DOMAINS)
    parser.add_argument("--qr-static", type=Path, default=DEFAULT_QR_STATIC)
    parser.add_argument("--qr-output", type=Path, default=DEFAULT_QR_OUTPUT)
    parser.add_argument("--stamp", type=Path, default=DEFAULT_STAMP)
    args = parser.parse_args()

    bypass, domain_count, resolved_count, address_count = build_bypass(
        args.domains, args.static
    )
    qr_bypass, qr_domain_count, qr_resolved_count, qr_address_count = build_bypass(
        args.qr_domains, args.qr_static
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.qr_output.parent.mkdir(parents=True, exist_ok=True)
    args.stamp.parent.mkdir(parents=True, exist_ok=True)

    output_tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    output_tmp.write_text("\n".join(str(network) for network in bypass) + "\n")
    output_tmp.chmod(0o644)
    output_tmp.replace(args.output)

    qr_output_tmp = args.qr_output.with_suffix(args.qr_output.suffix + ".tmp")
    qr_output_tmp.write_text(
        "\n".join(str(network) for network in qr_bypass) + "\n"
    )
    qr_output_tmp.chmod(0o644)
    qr_output_tmp.replace(args.qr_output)

    args.stamp.write_text(
        f"updated={time.strftime('%Y-%m-%d %H:%M:%S %z')}\n"
        f"domains={domain_count}\n"
        f"resolved_domains={resolved_count}\n"
        f"resolved_addresses={address_count}\n"
        f"bypass_ipv4={len(bypass)}\n"
        f"qr_domains={qr_domain_count}\n"
        f"qr_resolved_domains={qr_resolved_count}\n"
        f"qr_resolved_addresses={qr_address_count}\n"
        f"qr_bypass_ipv4={len(qr_bypass)}\n"
        f"dns_servers={','.join(CHINA_DNS_SERVERS)}\n"
    )
    print(
        f"wrote {args.output}: bypass_ipv4={len(bypass)} "
        f"resolved_domains={resolved_count}/{domain_count}; "
        f"{args.qr_output}: bypass_ipv4={len(qr_bypass)} "
        f"resolved_domains={qr_resolved_count}/{qr_domain_count}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
