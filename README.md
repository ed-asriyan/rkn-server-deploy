# RKN Server Deploy
Docker-based deployment for Xray VLESS Reality proxy servers with automatic client URI generation, deterministic key generation, next-hop relay routing, and Supabase synchronization.

## Features
- **Xray Core with VLESS + XTLS-Reality**: Fast and secure proxy server.
- **Deterministic Keys & UUIDs**: When `SEED` is provided, recreating or updating containers preserves client configuration URIs.
- **Relay / Next-Hop Routing**: Easily chain servers (e.g. forward traffic from a domestic relay server to an overseas server).
- **Supabase Integration**: Automatically pushes generated client VLESS URIs to a Supabase Edge Function (`submit_server`).
- **Domain Whitelisting & RU GeoIP/Geosite Blocking**: Built-in routing rules.

## Quick Start
1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Fill in the required environment variables:
   ```env
   SERVER_NAME=myserver1
   HOST=1.2.3.4
   PORT=443
   SNIS=google.com
   FALLBACK_PROXY_TARGET=google.com:443
   FINGERPRINT=chrome
   NUMBER_OF_USERS=256
   SEED=1234
   WHITELIST_DOMAINS=rkn.gov.ru

   # Optional: Supabase sync
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_SECRET_KEY=your-service-role-key
   ```

3. Run with Docker Compose:
   ```bash
   docker compose up -d
   ```

## Environment Variables
| Variable | Required | Description | Example |
|---|---|---|---|
| `HOST` | **Yes** | Server public IP address or domain | `1.2.3.4` |
| `SERVER_NAME` | No | Server/provider name tag (default: compose project name) | `myserver1` |
| `PORT` | No | Port on which Xray listens (default: `443`) | `443` |
| `SNIS` | **Yes** | Comma-separated list of SNIs for Reality | `google.com` |
| `FALLBACK_PROXY_TARGET` | No | Fallback destination (default: `<SNIS[0]>:443`) | `google.com:443` |
| `FINGERPRINT` | No | Reality uTLS fingerprint (default: `chrome`) | `chrome` |
| `NUMBER_OF_USERS` | No | Number of client UUIDs to generate (default: `256`) | `256` |
| `SEED` | No | Integer seed for deterministic keypair & UUID generation | `2343` |
| `WHITELIST_DOMAINS` | No | Comma-separated domains to bypass RU blocks | `rkn.gov.ru` |
| `SUPABASE_URL` | No | Supabase project URL | `https://xyz.supabase.co` |
| `SUPABASE_SECRET_KEY` | No | Supabase secret key for authentication | `eyJ...` |
| `NEXT_HOP_HOST` | No | Destination server IP for relay/chaining | `5.6.7.8` |
| `NEXT_HOP_PORT` | No | Destination server port (default: `443`) | `8443` |
| `NEXT_HOP_SNIS` | No | Destination server SNI for Reality | `google.com` |
| `NEXT_HOP_FINGERPRINT` | No | Destination server fingerprint (default: `chrome`) | `chrome` |
| `NEXT_HOP_PUBLIC_KEY` | No | Destination server X25519 public key (auto-derived if `SEED` is set) | |
| `NEXT_HOP_UUID` | No | Destination server client UUID (auto-derived if `SEED` is set) | |
