from __future__ import annotations

import concurrent.futures
import dataclasses
import ipaddress
import re
import socket
import ssl
import time

try:
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID, NameOID
    HAVE_X509 = True
except ImportError:
    HAVE_X509 = False

# Domain 5: SNI Probe & Auto-Discovery Domain (SNI Discovery Domain)
# ==============================================================================

# Common CDN indicators to exclude
KNOWN_CDN_KEYWORDS = [
    "cloudflare",
    "cloudfront",
    "fastly",
    "akamai",
    "akamaiedge",
    "edgekey",
    "edgesuite",
    "incapsula",
    "imperva",
    "sucuri",
    "ddos-guard",
    "qrator",
    "stormwall",
]

# Verified high-availability non-CDN candidates as reliable fallbacks if subnet neighbor scan fails
FALLBACK_SNI_CANDIDATES = [
    "dl.google.com",
    "gateway.icloud.com",
    "www.microsoft.com",
    "s3.amazonaws.com",
    "www.apple.com",
    "kernel.org",
    "www.debian.org",
]


@dataclasses.dataclass
class SniProbeResult:
    domain: str
    target_ip: str | None
    is_valid: bool
    tls_13_supported: bool
    h2_supported: bool
    alpn_protocols: list[str]
    is_cdn: bool
    cdn_provider: str | None
    ipv4_address: str | None
    ipv6_address: str | None
    ipv6_broken: bool
    rtt_ms: float
    cert_cn: str | None
    cert_sans: list[str]
    cert_issuer: str | None
    score: int
    recommended_fallback_target: str
    status_summary: str


@dataclasses.dataclass(frozen=True)
class SniSelection:
    server_name: str
    fallback_target: str
    source: str
    probe: SniProbeResult | None = None


def _detect_cdn_text(*values: str | None) -> str | None:
    text = " ".join(v.lower() for v in values if v)
    for cdn in KNOWN_CDN_KEYWORDS:
        if cdn in text:
            if cdn in ("akamaiedge", "edgekey", "edgesuite"):
                return "akamai"
            return cdn
    return None


def _extract_cert_info_from_der(der_bytes: bytes) -> tuple[str | None, list[str], str | None]:
    """Extract CN, SANs, and Issuer Organization from raw DER bytes."""
    cn: str | None = None
    sans: list[str] = []
    issuer: str | None = None

    if HAVE_X509:
        try:
            cert = x509.load_der_x509_certificate(der_bytes)
            # CN
            cns = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            if cns:
                cn = str(cns[0].value)
            # Issuer
            issuers = cert.issuer.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
            if issuers:
                issuer = str(issuers[0].value)
            elif cert.issuer:
                issuer = cert.issuer.rfc4514_string()
            # SANs
            try:
                san_ext = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
                sans = [str(n) for n in san_ext.value.get_values_for_type(x509.DNSName)]
            except Exception:
                pass
            return cn, sans, issuer
        except Exception:
            pass

    # Fallback DER regex extraction for SANs (DNS names tagged with 0x82)
    try:
        # Match tag 0x82 followed by 1-byte length and ASCII domain
        raw_matches = re.findall(rb"\x82([\x01-\x7f])([a-zA-Z0-9.\-_]{2,})", der_bytes)
        for length_byte, domain_bytes in raw_matches:
            expected_len = length_byte[0] if isinstance(length_byte, bytes) else length_byte
            domain_str = domain_bytes.decode("ascii", errors="ignore").lower()
            if len(domain_str) >= expected_len:
                clean_domain = domain_str[:expected_len]
                if "." in clean_domain and not clean_domain.startswith("."):
                    sans.append(clean_domain)
    except Exception:
        pass

    return cn, list(dict.fromkeys(sans)), issuer


def probe_tls_endpoint(
    ip_or_host: str,
    port: int = 443,
    sni: str | None = None,
    timeout: float = 2.0,
) -> dict:
    """Connect to port 443 with TLS and extract ALPN, cipher, version, and certificate info."""
    result = {
        "success": False,
        "tls_version": None,
        "cipher": None,
        "alpn": None,
        "rtt_ms": 9999.0,
        "cert_cn": None,
        "cert_sans": [],
        "cert_issuer": None,
        "server_header": None,
        "is_cdn": False,
        "cdn_provider": None,
        "error": None,
    }

    t_start = time.perf_counter()
    sock = None
    try:
        sock = socket.create_connection((ip_or_host, port), timeout=timeout)

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # Request HTTP/2 and HTTP/1.1 via ALPN
        try:
            ctx.set_alpn_protocols(["h2", "http/1.1"])
        except Exception:
            pass

        # Use TLS 1.2 minimum, preferably TLS 1.3
        if hasattr(ssl, "TLSVersion"):
            try:
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            except Exception:
                pass

        server_name = sni if sni else (ip_or_host if not re.match(r"^\d+\.\d+\.\d+\.\d+$", ip_or_host) else None)
        with ctx.wrap_socket(sock, server_hostname=server_name) as ss:
            tls_handshake_time = (time.perf_counter() - t_start) * 1000.0
            result["rtt_ms"] = round(tls_handshake_time, 2)
            result["tls_version"] = ss.version()
            result["cipher"] = ss.cipher()[0] if ss.cipher() else None
            result["alpn"] = ss.selected_alpn_protocol()
            result["success"] = True

            # Extract certificate info
            der_cert = ss.getpeercert(binary_form=True)
            if der_cert:
                cn, sans, issuer = _extract_cert_info_from_der(der_cert)
                result["cert_cn"] = cn
                result["cert_sans"] = sans
                result["cert_issuer"] = issuer

                cdn_provider = _detect_cdn_text(issuer, cn, " ".join(sans[:25]))
                if cdn_provider:
                    result["is_cdn"] = True
                    result["cdn_provider"] = cdn_provider

            # Send minimal HTTP HEAD request to check server headers
            if sni and not result["is_cdn"] and result["alpn"] in (None, "http/1.1"):
                try:
                    head_req = (
                        f"HEAD / HTTP/1.1\r\n"
                        f"Host: {sni}\r\n"
                        f"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n"
                        f"Connection: close\r\n\r\n"
                    ).encode("ascii")
                    ss.settimeout(1.5)
                    ss.sendall(head_req)
                    resp = ss.recv(2048).decode("latin-1", errors="ignore").lower()
                    for line in resp.splitlines():
                        if line.startswith("server:"):
                            result["server_header"] = line.split(":", 1)[1].strip()
                            cdn_provider = _detect_cdn_text(result["server_header"])
                            if cdn_provider:
                                result["is_cdn"] = True
                                result["cdn_provider"] = cdn_provider
                        if "cf-ray" in line or "x-amz-cf-id" in line:
                            result["is_cdn"] = True
                            result["cdn_provider"] = "cloudflare" if "cf-ray" in line else "cloudfront"
                except Exception:
                    pass

    except Exception as e:
        result["error"] = str(e)
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass

    return result


def detect_http_cdn_headers(
    ip_or_host: str,
    sni: str,
    timeout: float = 2.0,
) -> tuple[bool, str | None, str | None]:
    server_header: str | None = None
    sock = None
    try:
        sock = socket.create_connection((ip_or_host, 443), timeout=timeout)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.set_alpn_protocols(["http/1.1"])
        except Exception:
            pass
        with ctx.wrap_socket(sock, server_hostname=sni) as ss:
            request = (
                "HEAD / HTTP/1.1\r\n"
                f"Host: {sni}\r\n"
                "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            ss.settimeout(timeout)
            ss.sendall(request)
            response = ss.recv(4096).decode("latin-1", errors="ignore").lower()
            for line in response.splitlines():
                if line.startswith("server:"):
                    server_header = line.split(":", 1)[1].strip()
                    cdn_provider = _detect_cdn_text(server_header)
                    if cdn_provider:
                        return True, cdn_provider, server_header
                if "cf-ray" in line:
                    return True, "cloudflare", server_header
                if "x-amz-cf-id" in line:
                    return True, "cloudfront", server_header
                cdn_provider = _detect_cdn_text(line)
                if cdn_provider:
                    return True, cdn_provider, server_header
    except Exception:
        pass
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
    return False, None, server_header


def check_dual_stack_health(domain: str, timeout: float = 2.0) -> tuple[str | None, str | None, bool]:
    """
    Resolves domain DNS and checks IPv6 reachability.
    Returns: (ipv4_address, ipv6_address, is_ipv6_broken)
    """
    ipv4_addr: str | None = None
    ipv6_addr: str | None = None
    ipv6_broken = False

    # IPv4 resolution
    try:
        res_v4 = socket.getaddrinfo(domain, 443, socket.AF_INET, socket.SOCK_STREAM)
        if res_v4:
            ipv4_addr = res_v4[0][4][0]
    except Exception:
        pass

    # IPv6 resolution & reachability check
    try:
        res_v6 = socket.getaddrinfo(domain, 443, socket.AF_INET6, socket.SOCK_STREAM)
        if res_v6:
            ipv6_addr = res_v6[0][4][0]
            # Test connecting to port 443 over IPv6
            s6 = None
            try:
                s6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                s6.settimeout(timeout)
                s6.connect((ipv6_addr, 443))
            except Exception:
                # AAAA record exists in DNS, but IPv6 on port 443 times out / unreachable!
                ipv6_broken = True
            finally:
                if s6:
                    try:
                        s6.close()
                    except Exception:
                        pass
    except Exception:
        pass

    return ipv4_addr, ipv6_addr, ipv6_broken


def probe_domain(domain: str, target_ip: str | None = None, host_ip: str | None = None) -> SniProbeResult:
    """Full diagnostic probe of an SNI candidate."""
    domain = domain.strip().lower().rstrip(".")
    ipv4_addr, ipv6_addr, ipv6_broken = check_dual_stack_health(domain)

    connect_target = target_ip if target_ip else (ipv4_addr if ipv4_addr else domain)
    tls_info = probe_tls_endpoint(connect_target, port=443, sni=domain, timeout=2.5)

    tls_13 = (tls_info["tls_version"] == "TLSv1.3")
    h2_supported = (tls_info["alpn"] == "h2")
    is_cdn = tls_info["is_cdn"]
    cdn_provider = tls_info["cdn_provider"]

    detected_domain_cdn = _detect_cdn_text(domain)
    if detected_domain_cdn:
        is_cdn = True
        cdn_provider = detected_domain_cdn

    if not is_cdn:
        headers_are_cdn, header_cdn_provider, server_header = detect_http_cdn_headers(connect_target, domain)
        if server_header:
            tls_info["server_header"] = server_header
        if headers_are_cdn:
            is_cdn = True
            cdn_provider = header_cdn_provider

    # Determine recommended fallback target
    if target_ip:
        fallback_target = f"{target_ip}:443"
    elif ipv6_broken and ipv4_addr:
        # Prevent Xray hangs when DNS resolves unreachable IPv6
        fallback_target = f"{ipv4_addr}:443"
    else:
        fallback_target = f"{domain}:443"

    # Calculate quality score (0 - 100)
    score = 0
    if tls_info["success"]:
        score += 20
        if tls_13:
            score += 30
        if h2_supported:
            score += 25
        if not is_cdn:
            score += 20
        else:
            score -= 50

        # RTT scoring
        rtt = tls_info["rtt_ms"]
        if rtt < 30:
            score += 15
        elif rtt < 100:
            score += 10
        elif rtt < 250:
            score += 5

        # Subnet proximity bonus
        if host_ip and ipv4_addr:
            try:
                h_octets = host_ip.split(".")[:3]
                t_octets = ipv4_addr.split(".")[:3]
                if h_octets == t_octets:
                    score += 20  # Same /24 subnet (same DC)
                elif h_octets[:2] == t_octets[:2]:
                    score += 10  # Same /16 subnet
            except Exception:
                pass

    score = max(0, min(100, score))
    is_valid = tls_info["success"] and tls_13 and h2_supported and not is_cdn

    if is_valid:
        status = "EXCELLENT (TLS 1.3, h2, Non-CDN)"
    elif is_cdn:
        status = f"POOR (CDN Detected: {cdn_provider})"
    elif not tls_13:
        status = f"UNSUITABLE (Requires TLS 1.3, got {tls_info['tls_version']})"
    elif not h2_supported:
        status = f"SUBOPTIMAL (ALPN h2 missing, got {tls_info['alpn']})"
    else:
        status = f"FAILED ({tls_info['error']})"

    return SniProbeResult(
        domain=domain,
        target_ip=connect_target,
        is_valid=is_valid,
        tls_13_supported=tls_13,
        h2_supported=h2_supported,
        alpn_protocols=[tls_info["alpn"]] if tls_info["alpn"] else [],
        is_cdn=is_cdn,
        cdn_provider=cdn_provider,
        ipv4_address=ipv4_addr,
        ipv6_address=ipv6_addr,
        ipv6_broken=ipv6_broken,
        rtt_ms=tls_info["rtt_ms"],
        cert_cn=tls_info["cert_cn"],
        cert_sans=tls_info["cert_sans"],
        cert_issuer=tls_info["cert_issuer"],
        score=score,
        recommended_fallback_target=fallback_target,
        status_summary=status,
    )


def scan_subnet_neighbors(host_ip: str, scan_window: int = 35) -> list[tuple[str, str]]:
    """
    Scans neighbor IPs in the /24 subnet for open 443 ports and extracts SSL certificate SANs.
    Returns a list of (ip, candidate_domain).
    """
    try:
        base_ip = ipaddress.IPv4Address(host_ip)
    except Exception:
        return []

    network = ipaddress.IPv4Network(f"{base_ip}/24", strict=False)
    # Generate neighbor offsets: +1, -1, +2, -2, ...
    candidate_ips: list[str] = []
    for delta in range(1, scan_window + 1):
        for sign in (1, -1):
            ip_val = int(base_ip) + (delta * sign)
            try:
                cand = ipaddress.IPv4Address(ip_val)
                if cand in network and cand != base_ip:
                    # Skip .0 network and .255 broadcast
                    octets = str(cand).split(".")
                    if octets[-1] not in ("0", "255"):
                        candidate_ips.append(str(cand))
            except Exception:
                pass

    discovered_candidates: list[tuple[str, str]] = []

    def check_neighbor_tls(ip: str) -> list[tuple[str, str]]:
        try:
            sock = socket.create_connection((ip, 443), timeout=0.8)
            sock.close()
        except Exception:
            return []

        # Port 443 is open. Grab the default certificate, then validate each discovered name against this IP.
        tls_info = probe_tls_endpoint(ip, port=443, timeout=1.5)
        found: list[tuple[str, str]] = []
        all_names = list(tls_info.get("cert_sans", []))
        if tls_info.get("cert_cn"):
            all_names.append(tls_info["cert_cn"])

        for name in all_names:
            name_clean = name.strip().lower().lstrip("*.")
            # Basic validation
            if (
                "." in name_clean
                and not name_clean.endswith(".arpa")
                and not name_clean.endswith(".local")
                and not name_clean.endswith(".internal")
                and not re.match(r"^\d+\.\d+\.\d+\.\d+$", name_clean)
            ):
                found.append((ip, name_clean))
        return found

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(check_neighbor_tls, ip): ip for ip in candidate_ips}
        for fut in concurrent.futures.as_completed(futures):
            try:
                res = fut.result()
                if res:
                    discovered_candidates.extend(res)
            except Exception:
                pass

    # Deduplicate by domain
    unique: dict[str, str] = {}
    for ip, dom in discovered_candidates:
        if dom not in unique:
            unique[dom] = ip
    return [(ip, dom) for dom, ip in unique.items()]


def discover_best_sni(host_ip: str) -> SniSelection:
    """
    Subnet neighbor scanner with automatic quality ranking and fallback pool.
    Returns a linked Reality serverName and dest pair.
    """
    print(f"[*] Starting SNI Auto-Discovery for host {host_ip}...")
    neighbor_domains = scan_subnet_neighbors(host_ip, scan_window=35)
    print(f"[*] Subnet scan located {len(neighbor_domains)} candidate domains on neighbor IPs.")

    probed_results: list[SniProbeResult] = []

    # Probe neighbor candidates
    for ip, dom in neighbor_domains[:15]:
        res = probe_domain(dom, target_ip=ip, host_ip=host_ip)
        if res.is_valid:
            probed_results.append(res)
            print(f"  -> Discovered Neighbor: {dom} | Score: {res.score} | RTT: {res.rtt_ms}ms | {res.status_summary}")
        else:
            print(f"  x Rejected Candidate: {dom} ({res.status_summary})")

    # If subnet scan yielded suitable candidates, pick the top one
    if probed_results:
        probed_results.sort(key=lambda x: (x.score, -x.rtt_ms), reverse=True)
        winner = probed_results[0]
        print(f"[OK] Selected Subnet Neighbor SNI: '{winner.domain}' (Target: {winner.recommended_fallback_target}, RTT: {winner.rtt_ms}ms)")
        return SniSelection(
            server_name=winner.domain,
            fallback_target=winner.recommended_fallback_target,
            source="subnet",
            probe=winner,
        )

    # Fallback to vetted high-availability non-CDN candidates
    print("[!] No suitable neighbor SNI found in subnet. Probing vetted fallback pool...")
    fallback_results: list[SniProbeResult] = []
    for dom in FALLBACK_SNI_CANDIDATES:
        res = probe_domain(dom, host_ip=host_ip)
        if res.is_valid:
            fallback_results.append(res)
            print(f"  -> Vetted Candidate: {dom} | Score: {res.score} | RTT: {res.rtt_ms}ms | {res.status_summary}")

    if fallback_results:
        fallback_results.sort(key=lambda x: (x.score, -x.rtt_ms), reverse=True)
        winner = fallback_results[0]
        print(f"[OK] Selected Vetted Fallback SNI: '{winner.domain}' (Target: {winner.recommended_fallback_target}, RTT: {winner.rtt_ms}ms)")
        return SniSelection(
            server_name=winner.domain,
            fallback_target=winner.recommended_fallback_target,
            source="fallback-pool",
            probe=winner,
        )

    # Absolute fallback safe default
    default_sni = "dl.google.com"
    default_target = "dl.google.com:443"
    print(f"[!] Warning: All candidate probes failed. Falling back to default '{default_sni}'.")
    return SniSelection(
        server_name=default_sni,
        fallback_target=default_target,
        source="default",
    )


# ==============================================================================
