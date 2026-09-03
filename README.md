# RKN Server Deploy

Docker-based deployment for Xray VLESS Reality proxy servers with selectable transport modes (`vless-reality-xhttp` and `vless-reality-tcp`), automatic client URI generation, deterministic key generation, dynamic subscription-based next-hop relay routing with `leastPing` load balancing & health checks, hourly traffic reporting, and Supabase synchronization.

## Features

- **Selectable Transport Modes**:
  - `vless-reality-xhttp` (default): Modern XHTTP transport (HTTP/2 & HTTP/3 encapsulation) with Reality TLS camouflage.
  - `vless-reality-tcp`: Classic VLESS over TCP with XTLS Vision flow (`xtls-rprx-vision`) and Reality.
- **Deterministic Keys & UUIDs**: When `SEED` is provided, recreating or updating containers preserves client configuration URIs.
- **Unified Subscription-Based Next-Hop Relay**:
  - Simply pass a subscription URL (`NEXT_HOP=https://...`) or raw VLESS URIs (separated by newlines, commas, or spaces).
  - Automatically fetches and parses all downstream VLESS nodes (supports plain text, base64 subscriptions, and JSON arrays).
  - **Self-Loop Protection**: If the subscription contains this server's own node (matching `HOST` and `PORT`), it is automatically ignored.
  - Background periodic polling (`NEXT_HOP_UPDATE_INTERVAL=3600`): re-fetches the subscription and hot-reloads Xray when nodes change.
  - Automatic latency probing via Xray `observatory` (`NEXT_HOP_PROBE_URL`, `NEXT_HOP_PROBE_INTERVAL`).
  - Automatic `leastPing` load balancing and failover: traffic is routed to the fastest healthy node.
- **Supabase Synchronization**: Automatically pushes generated client VLESS URIs on startup to the Supabase Edge Function (`submit_server`).
- **Hourly Server-wide Traffic Reporter**: Background daemon that tracks network traffic in non-overlapping 1-hour UTC intervals and pushes metrics to Supabase (`submit_traffic`). This does not collect per-user traffic, only aggregate server-wide traffic.

## Quick Start

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Fill in the required environment variables
   ```bash
   vim .env
   ```
3. Run with Docker Compose:
   ```bash
   docker compose up -d
   ```

## Environment Variables
| Variable | Required | Description | Example |
|---|---|---|---|
| `MODE` | **Yes** | Transport mode: `vless-reality-xhttp` or `vless-reality-tcp` | `vless-reality-xhttp` |
| `HOST` | **Yes** | Server public IP address or domain | `1.2.3.4` |
| `SERVER_NAME` | No | Server display name (default: compose project name or host) | `Netherlands 1` |
| `PORT` | **Yes** | Port on which Xray listens | `443` |
| `SNIS` | **Yes** | Comma-separated list of SNIs for Reality | `google.com` |
| `FALLBACK_PROXY_TARGET` | **Yes** | Fallback destination | `google.com:443` |
| `FINGERPRINT` | **Yes** | Reality uTLS fingerprint | `chrome` |
| `XHTTP_PATH` | No | URL path for XHTTP transport | `/` |
| `XHTTP_MODE` | No | XHTTP mode: `auto`, `packet-up`, `stream-up`, `stream-one` | `auto` |
| `NUMBER_OF_USERS` | **Yes** | Number of client UUIDs to generate | `256` |
| `SEED` | No | Integer seed for deterministic keypair & UUID generation | `123456789` |
| `WHITELIST_DOMAINS` | No | Comma-separated domains to bypass RU blocks | `rkn.gov.ru` |
| `SUPABASE_URL` | No | Supabase project URL | `https://xyz.supabase.co` |
| `SUPABASE_SECRET_KEY` | No | Supabase secret key for authentication | `sb_secret_****` |
| `SUPABASE_SERVER_UUID` | No | Server UUID for identification and renaming in Supabase (required if Supabase is used) | `e625d2fc-42db-4483-94bb-d7caa21cc341` |
| `NEXT_HOP` | No | Subscription URL (`https://...`) or raw VLESS URIs (separated by newlines, commas, or spaces) | `https://my-sub.co/nodes` |
| `NEXT_HOP_UPDATE_INTERVAL` | No | Interval in seconds to poll subscription URL and reload | `3600` |
| `NEXT_HOP_PROBE_URL` | No | URL used by observatory health checks | `http://cp.cloudflare.com/generate_204` |
| `NEXT_HOP_PROBE_INTERVAL` | No | Interval for next-hop latency probe & health checks | `1m` |
