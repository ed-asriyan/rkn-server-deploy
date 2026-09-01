#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519

xray_proc = None


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


def parse_vless_uri(uri: str) -> dict | None:
    uri = uri.strip()
    if not uri.startswith("vless://"):
        return None
    try:
        parsed = urllib.parse.urlparse(uri)
        user_id = parsed.username
        host = parsed.hostname
        port = parsed.port or 443
        qs = urllib.parse.parse_qs(parsed.query)

        def q_get(k, default=""):
            return qs.get(k, [default])[0]

        net_type = q_get("type", "tcp").lower()
        security = q_get("security", "none").lower()
        pbk = q_get("pbk") or q_get("publicKey")
        fp = q_get("fp") or q_get("fingerprint") or "chrome"
        sni = q_get("sni") or q_get("serverName") or host
        flow = q_get("flow")
        path = q_get("path", "/")
        mode = q_get("mode", "auto")
        spx = q_get("spx", "/")
        name = urllib.parse.unquote(parsed.fragment) if parsed.fragment else f"{host}:{port}"

        return {
            "user_id": user_id,
            "host": host,
            "port": port,
            "net_type": net_type,
            "security": security,
            "pbk": pbk,
            "fp": fp,
            "sni": sni,
            "flow": flow,
            "path": path,
            "mode": mode,
            "spx": spx,
            "name": name
        }
    except Exception as e:
        print(f"WARNING: Failed to parse VLESS URI '{uri}': {e}", file=sys.stderr)
        return None


def vless_dict_to_outbound(v: dict, tag: str) -> dict:
    users = [
        {
            "id": v["user_id"],
            "email": v["user_id"],
            "encryption": "none"
        }
    ]
    if v.get("flow"):
        users[0]["flow"] = v["flow"]

    stream_settings = {"network": v["net_type"]}

    if v["security"] == "reality":
        stream_settings["security"] = "reality"
        stream_settings["realitySettings"] = {
            "fingerprint": v["fp"],
            "serverName": v["sni"],
            "publicKey": v["pbk"],
            "shortId": "",
            "spiderX": v.get("spx") or "/"
        }
    elif v["security"] == "tls":
        stream_settings["security"] = "tls"
        stream_settings["tlsSettings"] = {
            "fingerprint": v["fp"],
            "serverName": v["sni"],
            "allowInsecure": False
        }

    if v["net_type"] == "xhttp":
        stream_settings["xhttpSettings"] = {
            "path": v["path"] if v["path"].startswith("/") else "/" + v["path"],
            "mode": v.get("mode") or "auto"
        }
    elif v["net_type"] == "ws":
        stream_settings["wsSettings"] = {
            "path": v["path"] if v["path"].startswith("/") else "/" + v["path"],
            "headers": {
                "Host": v["sni"]
            }
        }
    elif v["net_type"] == "grpc":
        stream_settings["grpcSettings"] = {
            "serviceName": v["path"].lstrip("/"),
            "multiMode": True
        }

    return {
        "tag": tag,
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": v["host"],
                    "port": v["port"],
                    "users": users
                }
            ]
        },
        "streamSettings": stream_settings
    }


def fetch_subscription(url_or_content: str, self_host: str, self_port: int) -> list[str]:
    url_or_content = url_or_content.strip()
    if not url_or_content:
        return []

    if url_or_content.startswith(("http://", "https://")):
        headers = {"User-Agent": "v2rayNG/1.9.0 (Xray-Relay-Updater)"}
        supabase_key = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if supabase_key and "supabase.co" in url_or_content:
            headers["Authorization"] = f"Bearer {supabase_key}"
            headers["apikey"] = supabase_key

        req = urllib.request.Request(url_or_content, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            print(f"WARNING: Failed to fetch subscription from {url_or_content}: {e}", file=sys.stderr)
            return []
    else:
        content = url_or_content

    content = content.strip()

    # Try decoding base64 subscription format
    try:
        decoded = base64.b64decode(content).decode("utf-8", errors="replace")
        if "vless://" in decoded:
            content = decoded
    except Exception:
        pass

    # Try JSON array or dictionary format
    try:
        data = json.loads(content)
        if isinstance(data, list):
            content = chr(10).join(str(item) for item in data)
        elif isinstance(data, dict) and "uris" in data and isinstance(data["uris"], list):
            content = chr(10).join(str(item) for item in data["uris"])
    except Exception:
        pass

    # Extract all vless:// URIs (supporting newline, comma, or space delimiters)
    raw_uris = re.findall(r"vless://[^\s,]+", content)

    # Filter out self-referencing nodes (matching this server's host/IP and port)
    filtered_uris = []
    for u in raw_uris:
        parsed = parse_vless_uri(u)
        if not parsed:
            continue
        if parsed["host"] == self_host and parsed["port"] == self_port:
            node_name = parsed["name"]
            node_host = parsed["host"]
            node_port = parsed["port"]
            print(f"Skipping self-referencing next-hop node: {node_name} ({node_host}:{node_port})")
            continue
        filtered_uris.append(u)

    return filtered_uris


def resolve_next_hop_outbounds(self_host: str, self_port: int) -> tuple[list[dict], list[str]]:
    sub_param = (os.environ.get("NEXT_HOP") or "").strip()
    if sub_param:
        uris = fetch_subscription(sub_param, self_host, self_port)
        if uris:
            outbounds = []
            for i, u in enumerate(uris):
                parsed = parse_vless_uri(u)
                if parsed:
                    tag = f"next-hop-{i+1}"
                    outbounds.append(vless_dict_to_outbound(parsed, tag))
            if outbounds:
                return outbounds, uris

    return [], []


def generate_xray_config(
    port: int,
    inbound_clients: list[dict],
    inbound_stream_settings: dict,
    outbounds: list[dict],
    has_next_hop: bool,
    whitelist_domains: list[str]
) -> dict:
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
            "streamSettings": inbound_stream_settings,
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

    rules = []
    if whitelist_domains:
        rules.append({
            "type": "field",
            "inboundTag": ["inbound-vless"],
            **({"balancerTag": "next-hop-balancer"} if has_next_hop else {"balancerTag": "smart-balancer"}),
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
                "balancerTag": "next-hop-balancer"
            },
            {
                "type": "field",
                "inboundTag": ["inbound-vless"],
                "balancerTag": "next-hop-balancer"
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
        "balancers": [
            {
                "tag": "next-hop-balancer",
                "selector": ["next-hop-"],
                "strategy": {
                    "type": "leastPing"
                }
            } if has_next_hop else {
                "tag": "smart-balancer",
                "selector": ["direct-ipv4", "direct-ipv6"],
                "strategy": {
                    "type": "leastPing"
                }
            }
        ],
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

    if has_next_hop:
        probe_url = os.environ.get("NEXT_HOP_PROBE_URL") or os.environ.get("PROBE_URL", "http://cp.cloudflare.com/generate_204")
        probe_interval = os.environ.get("NEXT_HOP_PROBE_INTERVAL") or os.environ.get("PROBE_INTERVAL", "1m")
        xray_config["observatory"] = {
            "subjectSelector": ["next-hop-"],
            "probeURL": probe_url,
            "probeInterval": probe_interval,
            "enableConcurrency": True
        }
    else:
        xray_config["observatory"] = {
            "subjectSelector": ["direct-ipv4", "direct-ipv6"],
            "probeURL": "http://cp.cloudflare.com/generate_204",
            "probeInterval": "24h",
            "enableConcurrency": True
        }

    return xray_config


def sig_handler(signum, frame):
    global xray_proc
    if xray_proc and xray_proc.poll() is None:
        try:
            xray_proc.terminate()
            xray_proc.wait(timeout=5)
        except Exception:
            pass
    sys.exit(0)


def main():
    global xray_proc

    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)

    if len(sys.argv) > 1 and sys.argv[1] in ("traffic-reporter", "traffic_reporter"):
        os.execvp("python3", ["python3", "/usr/local/bin/traffic_reporter.py"])

    mode = os.environ.get("MODE", "vless-reality-xhttp").strip().lower()
    if mode not in ("vless-reality-tcp", "vless-reality-xhttp"):
        print(f"ERROR: Invalid MODE '{mode}'. Must be 'vless-reality-tcp' or 'vless-reality-xhttp'.", file=sys.stderr)
        sys.exit(1)

    server_uuid = os.environ.get("SERVER_UUID")
    name = os.environ.get("SERVER_NAME") or server_uuid
    host = os.environ.get("HOST")

    if not host:
        print("ERROR: HOST environment variable is required.", file=sys.stderr)
        sys.exit(1)

    if not server_uuid:
        print("ERROR: SERVER_UUID environment variable is required.", file=sys.stderr)
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
    xhttp_path = os.environ.get("XHTTP_PATH", "/")
    if not xhttp_path.startswith("/"):
        xhttp_path = "/" + xhttp_path
    xhttp_mode = os.environ.get("XHTTP_MODE", "auto")

    number_of_users = int(os.environ.get("NUMBER_OF_USERS", "256"))
    seed = os.environ.get("SEED")

    whitelist_domains_str = os.environ.get("WHITELIST_DOMAINS", "")
    whitelist_domains = [d.strip() for d in whitelist_domains_str.split(",") if d.strip()]

    # Generate keypair and UUIDs for this server
    private_key, public_key = generate_keypair(seed, host)
    uuids = generate_uuids(number_of_users, seed, host)

    # Inbound setup
    if mode == "vless-reality-tcp":
        inbound_clients = [
            {
                "email": u,
                "flow": "xtls-rprx-vision",
                "id": u
            }
            for u in uuids
        ]
        inbound_stream_settings = {
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
        }
    else:  # vless-reality-xhttp
        inbound_clients = [
            {
                "email": u,
                "id": u
            }
            for u in uuids
        ]
        inbound_stream_settings = {
            "network": "xhttp",
            "xhttpSettings": {
                "path": xhttp_path,
                "mode": xhttp_mode
            },
            "realitySettings": {
                "dest": fallback_proxy_target,
                "maxTimediff": 0,
                "privateKey": private_key,
                "serverNames": snis,
                "shortIds": [""],
                "show": False,
                "xver": 0
            },
            "security": "reality"
        }

    # Generate VLESS client URIs
    uris = []
    encoded_name = urllib.parse.quote(name)
    encoded_path = urllib.parse.quote(xhttp_path, safe="")
    for u in uuids:
        for sni in snis:
            if mode == "vless-reality-tcp":
                uri = f"vless://{u}@{host}:{port}?type=tcp&security=reality&pbk={public_key}&fp={fingerprint}&sni={sni}&spx=%2F&flow=xtls-rprx-vision#{encoded_name}"
            else:  # vless-reality-xhttp
                uri = (
                    f"vless://{u}@{host}:{port}?"
                    f"type=xhttp&"
                    f"security=reality&"
                    f"pbk={public_key}&"
                    f"fp={fingerprint}&"
                    f"sni={sni}&"
                    f"path={encoded_path}&"
                    f"mode={xhttp_mode}&"
                    f"spx=%2F#{encoded_name}"
                )
            uris.append(uri)

    print(f"Generated {len(uris)} client VLESS URIs for provider '{name}' (mode: {mode}):")
    for uri in uris:
        print(uri)
    sys.stdout.flush()

    # Upload URIs to Supabase
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_secret_key = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if supabase_url and supabase_secret_key:
        print(f"Submitting {len(uris)} URIs to Supabase function at {supabase_url}...")
        url = f"{supabase_url.rstrip('/')}/functions/v1/submit_server"
        payload_data = {
            "id": server_uuid,
            "server_id": server_uuid,
            "name": name,
            "server_name": name,
            "uris": uris
        }
        payload = json.dumps(payload_data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {supabase_secret_key}",
                "apikey": supabase_secret_key,
                "Content-Type": "application/json"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp_data = resp.read().decode("utf-8")
                print(f"Supabase response ({resp.status}): {resp_data}")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
            print(f"WARNING: Failed to submit URIs to Supabase: HTTP Error {e.code}: {e.reason}. Response body: {err_body}", file=sys.stderr)
        except Exception as e:
            print(f"WARNING: Failed to submit URIs to Supabase: {e}", file=sys.stderr)
    else:
        print("SUPABASE_URL or SUPABASE_SECRET_KEY not provided. Skipping Supabase upload.")

    # Next-hop / Outbounds resolution
    next_hop_outbounds, current_hop_signatures = resolve_next_hop_outbounds(host, port)
    has_next_hop = len(next_hop_outbounds) > 0

    if has_next_hop:
        outbounds = list(next_hop_outbounds)
        outbounds.append({"tag": "blocked", "protocol": "blackhole", "settings": {}})
        print(f"Configured {len(next_hop_outbounds)} next-hop relay server(s) from NEXT_HOP with dynamic leastPing load balancing.")
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

    xray_config = generate_xray_config(
        port, inbound_clients, inbound_stream_settings, outbounds, has_next_hop, whitelist_domains
    )

    os.makedirs("/etc/xray", exist_ok=True)
    config_path = "/etc/xray/config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(xray_config, f, indent=2)
    print(f"Generated Xray config at {config_path} (mode: {mode})")

    # Start Xray process
    xray_binary = os.environ.get("XRAY_BINARY", "xray")
    print(f"Starting Xray ({xray_binary} run -c {config_path})...")
    sys.stdout.flush()
    sys.stderr.flush()

    xray_proc = subprocess.Popen([xray_binary, "run", "-c", config_path])

    # Periodic subscription updater loop (if NEXT_HOP is an HTTP/HTTPS subscription URL)
    sub_param = (os.environ.get("NEXT_HOP") or "").strip()
    is_sub_url = sub_param.startswith(("http://", "https://"))

    try:
        update_interval = int(os.environ.get("NEXT_HOP_UPDATE_INTERVAL", "3600"))
    except ValueError:
        update_interval = 3600

    last_update_time = time.time()

    while True:
        try:
            time.sleep(5)
            # Check if Xray died
            if xray_proc.poll() is not None:
                print(f"ERROR: Xray process exited with code {xray_proc.returncode}.", file=sys.stderr)
                sys.exit(xray_proc.returncode)

            if is_sub_url and (time.time() - last_update_time >= update_interval):
                last_update_time = time.time()
                print(f"Polling next-hop subscription from {sub_param}...")
                new_outbounds, new_signatures = resolve_next_hop_outbounds(host, port)
                if new_outbounds and new_signatures != current_hop_signatures:
                    print(f"Subscription updated! Found {len(new_outbounds)} nodes. Updating config and reloading Xray...")
                    current_hop_signatures = new_signatures
                    new_out_list = list(new_outbounds)
                    new_out_list.append({"tag": "blocked", "protocol": "blackhole", "settings": {}})
                    new_cfg = generate_xray_config(
                        port, inbound_clients, inbound_stream_settings, new_out_list, True, whitelist_domains
                    )
                    with open(config_path, "w", encoding="utf-8") as f:
                        json.dump(new_cfg, f, indent=2)

                    # Gracefully restart Xray
                    xray_proc.terminate()
                    try:
                        xray_proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        xray_proc.kill()

                    xray_proc = subprocess.Popen([xray_binary, "run", "-c", config_path])
                    print("Xray successfully reloaded with updated next-hop nodes.")
        except KeyboardInterrupt:
            break

    if xray_proc and xray_proc.poll() is None:
        xray_proc.terminate()
        xray_proc.wait(timeout=5)


if __name__ == "__main__":
    main()
