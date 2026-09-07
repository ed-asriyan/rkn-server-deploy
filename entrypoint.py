#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

from rkn_deploy import discover_best_sni, launch_server, probe_domain, run_traffic_reporter


def print_probe_result(domain: str):
    print(f"Probing SNI candidate: {domain} ...\n")
    res = probe_domain(domain)
    print("==================================================")
    print(f"Target Domain:             {res.domain}")
    print(f"IPv4 Address:              {res.ipv4_address or 'None'}")
    print(f"IPv6 Address:              {res.ipv6_address or 'None'}")
    print(f"IPv6 Reachability:         {'BROKEN/TIMEOUT' if res.ipv6_broken else ('OK' if res.ipv6_address else 'N/A')}")
    print(f"TLS 1.3 Supported:         {res.tls_13_supported}")
    print(f"HTTP/2 (h2) Supported:     {res.h2_supported}")
    print(f"Negotiated ALPN:           {res.alpn_protocols}")
    print(f"CDN Detected:              {res.is_cdn} ({res.cdn_provider or 'None'})")
    print(f"Round-Trip Time (RTT):     {res.rtt_ms} ms")
    print(f"Certificate CN:            {res.cert_cn}")
    print(f"Certificate SANs:          {res.cert_sans[:5]}{'...' if len(res.cert_sans) > 5 else ''}")
    print(f"Certificate Issuer:        {res.cert_issuer}")
    print(f"Quality Score (0-100):     {res.score}")
    print(f"Recommended Fallback:      {res.recommended_fallback_target}")
    print(f"Verdict:                   {res.status_summary}")
    print("==================================================")


def print_discovery_result(host_ip: str):
    selection = discover_best_sni(host_ip)
    print("\n[Auto-Discovery Result]")
    print(f"Chosen SNI:        {selection.server_name}")
    print(f"Fallback Target:   {selection.fallback_target}")
    print(f"Source:            {selection.source}")


def print_usage():
    print("Usage:")
    print("  entrypoint.py launch              # Run server daemon")
    print("  entrypoint.py traffic-reporter    # Run traffic reporter daemon")
    print("  entrypoint.py probe <domain>      # Run TLS & CDN diagnostic probe on domain")
    print("  entrypoint.py discover-sni [host] # Scan subnet for optimal SNI candidates")


def main():
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "launch"

    if cmd == "launch":
        launch_server()
        return

    if cmd in ("traffic-reporter", "traffic_reporter"):
        run_traffic_reporter()
        return

    if cmd == "probe":
        if len(sys.argv) < 3:
            print("Usage: entrypoint.py probe <domain>", file=sys.stderr)
            sys.exit(1)
        print_probe_result(sys.argv[2])
        return

    if cmd in ("discover-sni", "discover_sni", "scan"):
        host = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("HOST", "")
        if not host:
            print("Usage: entrypoint.py discover-sni <host_ip>", file=sys.stderr)
            sys.exit(1)
        print_discovery_result(host)
        return

    if cmd in ("--help", "-h", "help"):
        print_usage()
        return

    print(f"ERROR: Unknown command '{cmd}'.", file=sys.stderr)
    print_usage()
    sys.exit(1)


if __name__ == "__main__":
    main()
