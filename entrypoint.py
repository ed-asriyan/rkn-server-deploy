#!/usr/bin/env python3
import base64
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip('=')


def generate_keypair(seed: str | None, seed_host: str):
    if seed:
        raw_priv_seed = hashlib.sha256(f"{seed}:{seed_host}".encode()).digest()
        priv = x25519.X25519PrivateKey.from_private_bytes(raw_priv_seed)
    else:
        priv = x25519.X25519PrivateKey.generate()

    pub = priv.public_key()
    raw_priv = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption()
    )
    raw_pub = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )
    return b64u(raw_priv), b64u(raw_pub)


def generate_uuids(count: int, seed: str | None, seed_host: str) -> list[str]:
    if seed:
        ns = uuid.UUID(int=0)
        return [str(uuid.uuid5(ns, f"{seed}:{seed_host}:{i}")) for i in range(count)]
    else:
        return [str(uuid.uuid4()) for _ in range(count)]


def main():
    name = os.environ.get("SERVER_NAME") or os.environ.get("NAME") or "server"
    host = os.environ.get("HOST")
    if not host:
        print("ERROR: HOST environment variable is required.", file=sys.stderr)
        sys.exit(1)

    port_str = os.environ.get("PORT", "443")
    try:
        port = int(port_str)
    except ValueError:
        print(f"ERROR: Invalid PORT '{port_str}'", file=sys.stderr)
        sys.exit(1)

    snis_str = os.environ.get("SNIS", "")
    snis = [s.strip() for s in snis_str.split(",") if s.strip()]
    if not snis:
        print("ERROR: SNIS environment variable is required (comma-separated).", file=sys.stderr)
        sys.exit(1)

    fallback_proxy_target = os.environ.get("FALLBACK_PROXY_TARGET")
    if not fallback_proxy_target:
        fallback_proxy_target = f"{snis[0]}:443"

    fingerprint = os.environ.get("FINGERPRINT", "chrome")
    number_of_users = int(os.environ.get("NUMBER_OF_USERS", "256"))
    seed = os.environ.get("SEED")

    whitelist_domains_str = os.environ.get("WHITELIST_DOMAINS", "")
    whitelist_domains = [d.strip() for d in whitelist_domains_str.split(",") if d.strip()]

    # Generate keypair and UUIDs for this server
    private_key, public_key = generate_keypair(seed, host)
    uuids = generate_uuids(number_of_users, seed, host)

    # Next-hop / relay configuration
    next_hop_host = os.environ.get("NEXT_HOP_HOST")
    has_next_hop = bool(next_hop_host)

    next_hop_config = None
    if has_next_hop:
        next_hop_port = int(os.environ.get("NEXT_HOP_PORT", "443"))
        next_hop_fingerprint = os.environ.get("NEXT_HOP_FINGERPRINT", fingerprint)
        next_hop_snis_str = os.environ.get("NEXT_HOP_SNIS", "") or os.environ.get("NEXT_HOP_SNI", "")
        next_hop_snis = [s.strip() for s in next_hop_snis_str.split(",") if s.strip()]
        if not next_hop_snis:
            print("ERROR: NEXT_HOP_SNIS or NEXT_HOP_SNI is required when NEXT_HOP_HOST is set.", file=sys.stderr)
            sys.exit(1)

        # Public key for next hop: use explicit env var or derive from SEED + NEXT_HOP_HOST
        next_hop_public_key = os.environ.get("NEXT_HOP_PUBLIC_KEY")
        if not next_hop_public_key:
            if seed:
                _, next_hop_public_key = generate_keypair(seed, next_hop_host)
            else:
                print("ERROR: NEXT_HOP_PUBLIC_KEY is required if SEED is not set.", file=sys.stderr)
                sys.exit(1)

        # User UUID for next hop: use explicit env var or derive first UUID from SEED + NEXT_HOP_HOST
        next_hop_uuid = os.environ.get("NEXT_HOP_UUID")
        if not next_hop_uuid:
            if seed:
                next_hop_uuid = generate_uuids(1, seed, next_hop_host)[0]
            else:
                print("ERROR: NEXT_HOP_UUID is required if SEED is not set.", file=sys.stderr)
                sys.exit(1)

        next_hop_config = {
            "host": next_hop_host,
            "port": next_hop_port,
            "fingerprint": next_hop_fingerprint,
            "sni": next_hop_snis[0],
            "public_key": next_hop_public_key,
            "uuid": next_hop_uuid
        }

    # Generate Xray config
    inbound_clients = [
        {
            "email": u,
            "flow": "xtls-rprx-vision",
            "id": u
        }
        for u in uuids
    ]

    inbounds = [
        {
            "listen": None,
            "port": port,
            "protocol": "vless",
            "settings": {
                "clients": inbound_clients,
                "decryption": "none",
                "fallbacks": []
            },
            "streamSettings": {
                "network": "tcp",
                "realitySettings": {
                    "dest": fallback_proxy_target,
                    "maxTimediff": 0,
                    "privateKey": private_key,
                    "serverNames": snis,
                    "shortIds": [""],
                    "show": False,
                    "xver": 0
                },
                "security": "reality",
                "tcpSettings": {
                    "acceptProxyProtocol": False,
                    "header": {
                        "type": "none"
                    }
                }
            },
            "tag": "inbound-vless",
            "sniffing": {
                "enabled": True,
                **({"routeOnly": True} if has_next_hop else {}),
                "destOverride": ["http", "tls", "quic"]
            },
            "allocate": {
                "strategy": "always",
                "refresh": 5,
                "concurrency": 3
            }
        }
    ]

    if has_next_hop:
        outbounds = [
            {
                "tag": "next-hop",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": next_hop_config["host"],
                            "port": next_hop_config["port"],
                            "users": [
                                {
                                    "id": next_hop_config["uuid"],
                                    "email": next_hop_config["uuid"],
                                    "flow": "xtls-rprx-vision",
                                    "encryption": "none"
                                }
                            ]
                        }
                    ]
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "fingerprint": next_hop_config["fingerprint"],
                        "serverName": next_hop_config["sni"],
                        "publicKey": next_hop_config["public_key"],
                        "shortId": "",
                        "spiderX": ""
                    }
                }
            },
            {
                "tag": "blocked",
                "protocol": "blackhole",
                "settings": {}
            }
        ]
    else:
        outbounds = [
            {
                "tag": "direct-ipv4",
                "protocol": "freedom",
                "settings": {
                    "domainStrategy": "UseIPv4"
                }
            },
            {
                "tag": "direct-ipv6",
                "protocol": "freedom",
                "settings": {
                    "domainStrategy": "UseIPv6"
                }
            },
            {
                "tag": "blocked",
                "protocol": "blackhole",
                "settings": {}
            }
        ]

    rules = []
    if whitelist_domains:
        rules.append({
            "type": "field",
            "inboundTag": ["inbound-vless"],
            **({"outboundTag": "next-hop"} if has_next_hop else {"balancerTag": "smart-balancer"}),
            "domain": [f"domain:{d}" for d in whitelist_domains]
        })

    rules.extend([
        {
            "type": "field",
            "inboundTag": ["inbound-vless"],
            "outboundTag": "blocked",
            "ip": ["geoip:private"]
        },
        {
            "type": "field",
            "inboundTag": ["inbound-vless"],
            "outboundTag": "blocked",
            "domain": ["geosite:category-ru"]
        },
        {
            "type": "field",
            "inboundTag": ["inbound-vless"],
            "outboundTag": "blocked",
            "ip": ["geoip:ru"]
        }
    ])

    if has_next_hop:
        rules.extend([
            {
                "type": "field",
                "inboundTag": ["inbound-vless"],
                "ip": ["::/0"],
                "outboundTag": "next-hop"
            },
            {
                "type": "field",
                "inboundTag": ["inbound-vless"],
                "outboundTag": "next-hop"
            }
        ])
    else:
        rules.append({
            "type": "field",
            "network": "tcp,udp",
            "balancerTag": "smart-balancer"
        })

    routing = {
        "domainStrategy": "IPIfNonMatch",
        **({} if has_next_hop else {
            "balancers": [
                {
                    "tag": "smart-balancer",
                    "selector": ["direct-ipv4", "direct-ipv6"],
                    "strategy": {
                        "type": "leastPing"
                    }
                }
            ]
        }),
        "rules": rules
    }

    xray_config = {
        "inbounds": inbounds,
        "outbounds": outbounds,
        "routing": routing,
        "log": {
            "access": "none",
            "dnsLog": False,
            "loglevel": "warning",
            "maskAddress": ""
        }
    }

    if not has_next_hop:
        xray_config["observatory"] = {
            "subjectSelector": ["direct-ipv4", "direct-ipv6"],
            "probeURL": "http://cp.cloudflare.com/generate_204",
            "probeInterval": "24h",
            "enableConcurrency": True
        }

    os.makedirs("/etc/xray", exist_ok=True)
    config_path = "/etc/xray/config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(xray_config, f, indent=2)
    print(f"Generated Xray config at {config_path}")

    # Generate VLESS client URIs
    uris = []
    for u in uuids:
        for sni in snis:
            encoded_name = urllib.parse.quote(name)
            uri = f"vless://{u}@{host}:{port}?type=tcp&security=reality&pbk={public_key}&fp={fingerprint}&sni={sni}&spx=%2F&flow=xtls-rprx-vision#{encoded_name}"
            uris.append(uri)

    print(f"Generated {len(uris)} client VLESS URIs for provider '{name}'")

    # Upload URIs to Supabase
    supabase_url = os.environ.get("SUPABASE_URL")
    SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY")

    if supabase_url and SUPABASE_SECRET_KEY:
        print(f"Submitting {len(uris)} URIs to Supabase function at {supabase_url}...")
        url = f"{supabase_url.rstrip('/')}/functions/v1/submit_server"
        payload = json.dumps(uris).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
                "apikey": SUPABASE_SECRET_KEY,
                "Content-Type": "application/json"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req) as resp:
                resp_data = resp.read().decode("utf-8")
                print(f"Supabase response ({resp.status}): {resp_data}")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
            print(f"WARNING: Failed to submit URIs to Supabase: HTTP Error {e.code}: {e.reason}. Response body: {err_body}", file=sys.stderr)
        except Exception as e:
            print(f"WARNING: Failed to submit URIs to Supabase: {e}", file=sys.stderr)
    else:
        print("SUPABASE_URL or SUPABASE_SECRET_KEY not provided. Skipping Supabase upload.")

    # Start Xray
    xray_binary = os.environ.get("XRAY_BINARY", "xray")
    print(f"Starting Xray ({xray_binary} run -c {config_path})...")
    sys.stdout.flush()
    sys.stderr.flush()
    os.execvp(xray_binary, [xray_binary, "run", "-c", config_path])


if __name__ == "__main__":
    main()
