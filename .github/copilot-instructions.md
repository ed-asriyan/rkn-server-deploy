# GitHub Copilot & AI Agent Instructions

This repository manages Docker-based deployments of Xray VLESS Reality proxy nodes, supporting both `vless-reality-xhttp` and `vless-reality-tcp` transports, deterministic key derivation, automatic SNI discovery, relay load balancing, Supabase synchronization, and traffic reporting.

All future development, modifications, and AI agent contributions MUST strictly adhere to the architecture and conventions defined below.

---

## 1. Universal Language & Style Rules

1. **English Only**: All code, docstrings, inline comments, variable names, error messages, documentation, and Git commit messages MUST be written in English.
2. **Zero Unapproved Dependencies**: The container runs on `alpine:3.24` with `python3` and `py3-cryptography`. All implementations must rely strictly on the **Python standard library** and **`cryptography`**. Do NOT introduce external packages (`requests`, `scapy`, `dnspython`, `httpx`, etc.).
3. **No Spaghetti Code / Anti-Degradation Rule**: Avoid unstructured procedural scripts. Runtime logic must live in the `rkn_deploy` package, inside the owning domain module. `entrypoint.py` may route commands, validate CLI arity, and print diagnostic results, but MUST NOT contain server business logic. Do NOT scatter `os.environ.get()` calls across helper functions. Main server configuration must be parsed once into strongly-typed immutable dataclasses.

---

## 2. Domain-Driven Architecture (DDD)

The runtime code is split into an explicit root `entrypoint.py` command dispatcher and the `rkn_deploy` package. The package acts as a runtime platform facade through `rkn_deploy/__init__.py` and hides server implementation details behind domain modules. When extending or modifying functionality, maintain these clear domain boundaries:

```
┌────────────────────────────────────────────────────────────────────────┐
│                          CLI & Supervisor Entry                        │
└───────┬──────────────┬──────────────┬──────────────┬───────────────────┘
        │              │              │              │
        ▼              ▼              ▼              ▼
┌──────────────┐┌──────────────┐┌──────────────┐┌────────────────────────┐
│   Domain 1   ││   Domain 2   ││   Domain 3   ││        Domain 4        │
│    Config    ││    Crypto    ││ Subscription ││   Xray Config Domain   │
│    Domain    ││    Domain    ││  & Relays    ││ (Inbound/Outbound/etc) │
└──────────────┘└──────────────┘└──────────────┘└────────────────────────┘
                                      ▲                      ▲
                                      │                      │
                               ┌──────┴───────┐       ┌──────┴───────┐
                               │   Domain 6   │       │   Domain 5   │
                               │  Lifecycle & │       │  SNI Probe & │
                               │  Supervisor  │       │Auto-Discovery│
                               └──────────────┘       └──────────────┘

The Docker image uses one ENTRYPOINT with explicit commands. The Xray service runs `entrypoint.py launch`; the traffic reporter service runs `entrypoint.py traffic-reporter`. Traffic reporting is part of the Lifecycle domain.
```

### Entrypoint Dispatcher (`entrypoint.py`, `rkn_deploy/__init__.py`)
- **Responsibility**: Keep Docker command routing outside the business package while exposing a small runtime facade.
- **Rules**:
  - `entrypoint.py` dispatches explicit commands: `launch`, `traffic-reporter`, `probe`, and `discover-sni`.
  - `entrypoint.py` must delegate runtime work to public functions exported by `rkn_deploy/__init__.py`.
  - `rkn_deploy` must not contain Docker command dispatch logic or parse `sys.argv`.
  - The `launch` command must call `rkn_deploy.launch_server()`, which parses configuration, derives identities, resolves SNI/fallback through the Lifecycle/SNI path, generates Xray config, and starts supervision.
  - The `traffic-reporter` command must call `rkn_deploy.run_traffic_reporter()`; do not restore a separate traffic reporter module or root-level script.

### Domain 1: Configuration & Validation Domain (`rkn_deploy/config.py`)
- **Responsibility**: Environment variable reading, strict validation, safe defaults, and parsing into immutable dataclasses (`ServerConfig`, `RelayConfig`, `SupabaseConfig`, `AppConfig`).
- **Rules**:
  - Validates `MODE` against `XrayInboundMode` enum (`vless-reality-tcp`, `vless-reality-xhttp`).
  - Normalizes paths (ensures leading slash for `XHTTP_PATH`, default `/xhttp`).
  - Sets safe defaults (e.g. `FINGERPRINT="chrome"`, `XHTTP_MODE="stream-one"`).
  - Does not accept user-provided SNI or Reality fallback destinations. These values must be selected by the SNI Discovery Domain at runtime.

### Domain 2: Cryptographic & Deterministic Identification (`rkn_deploy/crypto.py`)
- **Responsibility**: Keypair generation and client UUID derivation.
- **Rules**:
  - `derive_keypair(seed, host)`: Uses X25519. If `seed` is provided, derives private key deterministically via `SHA-256(f"{seed}:{host}")`. Encodes output in unpadded URL-safe base64 (`b64u`).
  - `derive_user_uuids(count, seed, host)`: Generates reproducible `uuid.uuid5` identifiers when `seed` is provided, preserving client subscription links across container restarts. Pure functions with no external state.

### Domain 3: Subscription & Relay Node Domain (`rkn_deploy/subscription.py`)
- **Responsibility**: Ingestion, decoding, normalization, and validation of downstream VLESS relay nodes (`VlessNode`).
- **Rules**:
  - Supports base64 subscriptions, sing-box JSON configuration arrays, and raw `vless://` URI streams.
  - **Self-Loop Prevention**: Any downstream node matching the server's own `HOST` and `PORT` must be skipped.
  - Generates Xray outbound blocks (`vless_node_to_outbound`) with appropriate stream settings for next-hop relays.

### Domain 4: Xray Configuration Builders (`rkn_deploy/xray_config.py`)
- **Responsibility**: Programmatic assembly of Xray's `config.json`.
- **Builders**:
  - `InboundBuilder`: Assembles `inbound-vless`.
    - For `vless-reality-tcp`: Injects `flow: "xtls-rprx-vision"`, `network: "tcp"`.
    - For `vless-reality-xhttp`: Configures `network: "xhttp"` with `xhttpSettings` (`path`, `mode`), omitting TCP-only flow settings.
  - `ClientUriBuilder`: Builds client VLESS URIs with properly encoded query parameters.
  - `OutboundBuilder`: Builds freedom outbounds (`direct-ipv4`, `direct-ipv6`) or next-hop relay pool with `blackhole` blocked fallbacks.
  - `RoutingBuilder`: Enforces `geoip:private`, `geosite:category-ru`, `geoip:ru` blacklisting, optional domain whitelist bypassing, and `leastPing` balancers.
  - `ObservatoryBuilder`: Configures active latency probing (`probeURL`, `probeInterval`).

### Domain 5: SNI Probe & Auto-Discovery Domain (`rkn_deploy/sni.py`)
- **Responsibility**: Quality assessment, active scanning, and selection of Reality camouflage targets.
- **Rules & Quality Criteria**:
  1. **Subnet Proximity Scanning**:
     - Scans neighbor IPs in the server's `/24` subnet on port 443.
     - Grabs SSL certificates and extracts Subject Alternative Names (SANs) and Common Names (CN).
    - Keeps each selected Reality `serverName` paired with the exact tested `dest` endpoint. For subnet candidates, `dest` MUST be the tested neighbor IP (`ip:443`), not the candidate domain's unrelated DNS result.
     - Prioritizes targets hosted in the same datacenter/ASN to ensure minimal latency (<1ms) and eliminate DPI ISP/ASN mismatch anomalies.
  2. **Strict Verification**:
     - **TLS 1.3**: Target must support TLS 1.3.
     - **ALPN HTTP/2 (`h2`)**: Target must negotiate `h2` via ALPN. Crucial for XHTTP transport.
     - **CDN Elimination**: Cloudflare, Akamai, CloudFront, Fastly, etc., MUST be detected and rejected. CDNs frequently trigger 403 Forbidden errors, active probe blocks, and are heavily censored in restricted regions.
  3. **Dual-Stack & Broken IPv6 Protection**:
     - Resolves both A and AAAA records for candidate domains.
     - Actively tests IPv6 connectivity on port 443. If AAAA exists in DNS but IPv6 is unreachable (timeout/unrouted), the fallback destination MUST be bound to the working IPv4 address (`f"{ipv4}:443"`), while keeping the original domain in `serverNames`. This prevents Xray from hanging on broken IPv6 routes.
  4. **Fallback Pool**:
     - If local subnet scanning yields no viable targets (e.g. strict provider firewall), automatically probes a curated pool of reliable high-availability non-CDN domains (`dl.google.com`, `gateway.icloud.com`, etc.).

### Domain 6: Lifecycle & Supervisor Domain (`rkn_deploy/lifecycle.py`)
- **Responsibility**: Process execution, graceful shutdown, health monitoring, dynamic reload, hourly traffic accounting, and Supabase publishing.
- **Rules**:
  - `SupabasePublisher`: Pushes generated client VLESS URIs to Supabase Edge Functions.
  - `XraySupervisor`:
    - Handles `SIGTERM` and `SIGINT` signals for graceful process termination.
    - Supervises the Xray child process in an event loop.
    - Executes background subscription polling (`NEXT_HOP_UPDATE_INTERVAL`), rebuilding configurations and reloading Xray when the downstream node set changes.
  - Traffic reporter functions:
    - Are invoked through `run_traffic_reporter()`.
    - Read only traffic-reporter-specific environment variables at daemon startup (`SUPABASE_*`, `TRAFFIC_STATE_FILE`).
    - Persist unsent records atomically and keep retry behavior local to the Lifecycle domain.
    - Must not depend on Xray config builders, SNI discovery, or subscription parsing.

---

## 3. Reality & XHTTP Transport Best Practices

When configuring or debugging nodes:
- **Never use `fp=random` for XHTTP**: uTLS random fingerprints can negotiate HTTP/1.1 or omit `h2`, breaking XHTTP multiplexing. Always specify `FINGERPRINT=chrome`.
- **Avoid root path (`/`) for XHTTP**: Using `/` can collide with the fallback website's root index. Default to `/xhttp`.
- **Avoid Cloudflare for Reality**: Cloudflare inspects HTTP/2 `:authority` and SNI headers, dropping non-matching traffic with 403 or connection resets.

---

## 4. CLI Diagnostic Commands

`entrypoint.py` provides standalone CLI diagnostic capabilities:
```bash
# Probe any domain for Reality & XHTTP suitability:
python3 entrypoint.py probe <domain>

# Run subnet discovery scanner to identify top SNI candidates:
python3 entrypoint.py discover-sni [host_ip]

# Run the hourly traffic reporter daemon:
python3 entrypoint.py traffic-reporter

# Run the Xray server daemon:
python3 entrypoint.py launch
```
