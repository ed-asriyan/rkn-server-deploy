# RKN Server Deploy

Docker-based deployment for Xray VLESS Reality proxy servers with selectable transport modes (`vless-reality-xhttp` and `vless-reality-tcp`), automatic client URI generation, deterministic key generation, next-hop relay routing, hourly traffic reporting, and Supabase synchronization.

## Features

- **Selectable Transport Modes**:
  - `vless-reality-xhttp` (default): Modern XHTTP transport (HTTP/2 & HTTP/3 encapsulation) with Reality TLS camouflage.
  - `vless-reality-tcp`: Classic VLESS over TCP with XTLS Vision flow (`xtls-rprx-vision`) and Reality.
- **Deterministic Keys & UUIDs**: When `SEED` is provided, recreating or updating containers preserves client configuration URIs.
- **Relay / Next-Hop Routing**: Easily chain servers (e.g. forward traffic from a domestic relay server to an overseas server) with independent mode selection for next hop (`NEXT_HOP_MODE`).
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
| `MODE` | No | Transport mode: `vless-reality-xhttp` or `vless-reality-tcp` (default: `vless-reality-xhttp`) | `vless-reality-xhttp` |
| `HOST` | **Yes** | Server public IP address or domain | `1.2.3.4` |
| `SERVER_NAME` | No | Server/provider name tag (default: compose project name) | `Netherlands 1` |
| `SERVER_UUID` | No | Server UUID for identification and renaming in Supabase | `e625d2fc-42db-4483-94bb-d7caa21cc341` |
| `PORT` | No | Port on which Xray listens (default: `443`) | `443` |
| `SNIS` | **Yes** | Comma-separated list of SNIs for Reality | `google.com` |
| `FALLBACK_PROXY_TARGET` | **Yes** | Fallback destination (default: `<SNIS[0]>:443`) | `google.com:443` |
| `FINGERPRINT` | No | Reality uTLS fingerprint (default: `chrome`) | `chrome` |
| `XHTTP_PATH` | No | URL path for XHTTP transport (default: `/`) | `/` |
| `XHTTP_MODE` | No | XHTTP mode: `auto`, `packet-up`, `stream-up`, `stream-one` (default: `auto`) | `auto` |
| `NUMBER_OF_USERS` | No | Number of client UUIDs to generate (default: `256`) | `256` |
| `SEED` | No | Integer seed for deterministic keypair & UUID generation | `123456789` |
| `WHITELIST_DOMAINS` | No | Comma-separated domains to bypass RU blocks | `rkn.gov.ru` |
| `SUPABASE_URL` | No | Supabase project URL | `https://xyz.supabase.co` |
| `SUPABASE_SECRET_KEY` | No | Supabase secret key for authentication | `sb_secret_****` |
| `NEXT_HOP_MODE` | No | Destination server mode: `vless-reality-xhttp` or `vless-reality-tcp` (default: value of `MODE`) | `vless-reality-xhttp` |
| `NEXT_HOP_HOST` | No | Destination server IP for relay/chaining | `5.6.7.8` |
| `NEXT_HOP_PORT` | No | Destination server port (default: `443`) | `8443` |
| `NEXT_HOP_SNIS` | No | Destination server SNI for Reality | `google.com` |
| `NEXT_HOP_FINGERPRINT` | No | Destination server fingerprint (default: `chrome`) | `chrome` |
| `NEXT_HOP_PUBLIC_KEY` | No | Destination server X25519 public key (auto-derived if `SEED` is set) | |
| `NEXT_HOP_UUID` | No | Destination server client UUID (auto-derived if `SEED` is set) | |
| `NEXT_HOP_XHTTP_PATH` | No | Destination server XHTTP path (default: `XHTTP_PATH` or `/`) | `/` |
| `NEXT_HOP_XHTTP_MODE` | No | Destination server XHTTP mode (default: `XHTTP_MODE` or `auto`) | `auto` |
