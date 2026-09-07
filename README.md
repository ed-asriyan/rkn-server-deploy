# RKN Server Deploy

Docker-based deployment for Xray VLESS Reality proxy servers with selectable transport modes (`vless-reality-xhttp` and `vless-reality-tcp`), automatic SNI discovery and dual-stack fallback validation, automatic client URI generation, deterministic key generation, dynamic subscription-based next-hop relay routing with `leastPing` load balancing & health checks, hourly traffic reporting, and Supabase synchronization.

## Features

- **Selectable Transport Modes**:
  - `vless-reality-xhttp` (default): Modern XHTTP transport (HTTP/2 & HTTP/3 encapsulation) with Reality TLS camouflage.
  - `vless-reality-tcp`: Classic VLESS over TCP with XTLS Vision flow (`xtls-rprx-vision`) and Reality.
- **Automatic SNI Discovery & Quality Validation**:
  - The server automatically scans neighbor IP addresses in its `/24` subnet and derives both Reality `serverNames` and `dest` at startup.
  - Inspects remote SSL certificates for Subject Alternative Names (SANs) and evaluates candidates.
  - Quality gating: enforces TLS 1.3, ALPN HTTP/2 (`h2`), and eliminates CDN domains (Cloudflare, Akamai, CloudFront, etc.) to prevent DPI detection and latency spikes.
  - **Dual-Stack & Broken IPv6 Protection**: Validates domain AAAA reachability. If an SNI candidate has broken/unresponsive IPv6, the fallback proxy destination is automatically bound to the working IPv4 address (`ip:443`) to prevent Xray connection hangs.
  - Built-in diagnostic CLI tools: test any domain with `python3 entrypoint.py probe <domain>` or run scanner with `python3 entrypoint.py discover-sni`.
- **Deterministic Keys & UUIDs**: When `SEED` is provided, recreating or updating containers preserves client configuration URIs.
- **Unified Subscription-Based Next-Hop Relay**:
  - Pass a subscription URL (`NEXT_HOP=https://...`) or raw VLESS URIs (separated by newlines, commas, or spaces).
  - Automatically fetches and parses downstream VLESS nodes (supports plain text, base64 subscriptions, and JSON arrays).
  - **Self-Loop Protection**: If the subscription contains this server's own node (matching `HOST` and `PORT`), it is automatically ignored.
  - Background periodic polling (`NEXT_HOP_UPDATE_INTERVAL=3600`): re-fetches subscription and hot-reloads Xray when nodes change.
  - Automatic latency probing via Xray `observatory` (`NEXT_HOP_PROBE_URL`, `NEXT_HOP_PROBE_INTERVAL`).
  - Automatic `leastPing` load balancing and failover: traffic is routed to the fastest healthy node.
- **Supabase Synchronization**: Automatically pushes generated client VLESS URIs on startup to the Supabase Edge Function (`submit_server`).
- **Hourly Server-wide Traffic Reporter**: Background daemon that tracks network traffic in non-overlapping 1-hour UTC intervals and pushes metrics to Supabase (`submit_traffic`).

## Quick Start

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Fill in the environment variables:
   ```bash
   vim .env
   ```
3. Run with Docker Compose:
   ```bash
   docker compose up -d
   ```

## Runtime Commands

The Docker image has one entrypoint (`entrypoint.py`) and uses explicit commands to select the runtime mode. In `docker-compose.yml`, the Xray service runs `launch`; the traffic accounting service runs `traffic-reporter`.

### `launch`

Starts the main Xray server daemon:

```bash
python3 entrypoint.py launch
```

This is the default command when no argument is provided. It performs the full server startup flow:

1. Parses runtime configuration from environment variables and validates required values.
2. Derives or generates the Reality keypair and client UUIDs.
3. Automatically discovers the best Reality SNI/fallback target pair from live TLS probes.
4. Generates the Xray `config.json` and client VLESS URIs.
5. Prints the selected SNI, fallback target, fingerprint, generated client URIs, and launch progress to stdout.
6. Submits generated client URIs to the OpenWhispr backend via Supabase Edge Functions when `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, and `SUPABASE_SERVER_UUID` are set.
7. Starts Xray and keeps supervising it. If `NEXT_HOP` is a subscription URL, it periodically refreshes downstream relays and reloads Xray when they change.

The command logs three visible startup stages:

```text
[launch] Stage 1/3: parsing runtime configuration and deriving identities...
[launch] Stage 2/3: selecting Reality SNI/fallback target and generating Xray config...
[launch] Stage 3/3: starting Xray supervisor...
```

### `traffic-reporter`

Starts the traffic accounting daemon:

```bash
python3 entrypoint.py traffic-reporter
```

This command is separate from `launch`. It does not start Xray and does not generate proxy configuration. It reads host network counters from `/proc/net/dev`, records non-overlapping hourly traffic intervals, persists unsent records under `/data`, and submits them to the OpenWhispr backend via Supabase Edge Functions when the Supabase environment variables are available. If credentials are missing, it prints that traffic reporting is disabled and keeps the container alive.

### `probe <domain>`

Runs a one-off diagnostic check for a specific SNI candidate:

```bash
python3 entrypoint.py probe example.com
```

It prints TLS version, negotiated ALPN, HTTP/2 support, CDN detection, IPv4/IPv6 reachability, certificate CN/SAN/issuer data, quality score, recommended fallback target, and final suitability verdict. It does not start Xray.

### `discover-sni [host_ip]`

Runs the automatic SNI discovery scanner without starting Xray:

```bash
python3 entrypoint.py discover-sni 74.208.191.75
```

The scanner probes neighbor IPs in the server's `/24` subnet, validates candidate certificate names, rejects unsuitable targets, and prints the selected SNI, fallback target, and selection source. If `host_ip` is omitted, it uses the `HOST` environment variable.

### `help`

Prints the command summary:

```bash
python3 entrypoint.py --help
```

## Runtime Module Layout

- `entrypoint.py`: Docker entrypoint and explicit command dispatcher for `launch`, `traffic-reporter`, and diagnostics.
- `rkn_deploy/__init__.py`: package facade exporting runtime operations used by the entrypoint.
- `rkn_deploy/config.py`: environment parsing and immutable runtime configuration.
- `rkn_deploy/crypto.py`: X25519 key derivation and deterministic client UUID generation.
- `rkn_deploy/subscription.py`: VLESS and sing-box subscription parsing for next-hop relays.
- `rkn_deploy/xray_config.py`: Xray inbound, outbound, routing, observatory, and client URI builders.
- `rkn_deploy/sni.py`: SNI probing, CDN checks, dual-stack validation, and automatic Reality target selection.
- `rkn_deploy/lifecycle.py`: Xray launch lifecycle, Supabase publishing, process supervision, dynamic next-hop reloads, and hourly traffic reporting.

## Environment Variables

| Variable | Required | Description | Default / Example |
|---|---|---|---|
| `MODE` | **Yes** | Transport mode: `vless-reality-xhttp` or `vless-reality-tcp` | `vless-reality-xhttp` |
| `HOST` | **Yes** | Server public IP address or domain | `74.208.191.75` |
| `SERVER_NAME` | No | Server display name (default: compose project name or host) | `Netherlands 1` |
| `PORT` | **Yes** | Port on which Xray listens | `443` or `8443` |
| `FINGERPRINT` | No | Reality uTLS fingerprint | `chrome` |
| `XHTTP_PATH` | No | URL path for XHTTP transport | `/xhttp` |
| `XHTTP_MODE` | No | XHTTP mode: `auto`, `packet-up`, `stream-up`, `stream-one` | `stream-one` |
| `NUMBER_OF_USERS` | **Yes** | Number of client UUIDs to generate | `256` |
| `SEED` | No | Integer/string seed for deterministic keypair & UUID generation | `123456789` |
| `WHITELIST_DOMAINS` | No | Comma-separated domains to bypass RU blocks | `admin.rezeptibabushki.ru` |
| `SUPABASE_URL` | No | Supabase project URL | `https://xyz.supabase.co` |
| `SUPABASE_SECRET_KEY` | No | Supabase secret key for authentication | `sb_secret_****` |
| `SUPABASE_SERVER_UUID` | No | Server UUID for identification and renaming in Supabase | `e625d2fc-42db-4483-94bb-d7caa21cc341` |
| `NEXT_HOP` | No | Subscription URL (`https://...`) or raw VLESS URIs | `https://my-sub.co/nodes` |
| `NEXT_HOP_UPDATE_INTERVAL` | No | Interval in seconds to poll subscription URL and reload | `3600` |
| `NEXT_HOP_PROBE_URL` | No | URL used by observatory health checks | `http://cp.cloudflare.com/generate_204` |
| `NEXT_HOP_PROBE_INTERVAL` | No | Interval for next-hop latency probe & health checks | `1m` |
