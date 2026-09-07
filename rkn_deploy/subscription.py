from __future__ import annotations

import base64
import dataclasses
import json
import re
import sys
import urllib.parse
import urllib.request

# Domain 3: Subscription & Relay Node Domain (Subscription Domain)
# ==============================================================================

@dataclasses.dataclass(frozen=True)
class VlessNode:
    user_id: str
    host: str
    port: int
    net_type: str
    security: str
    pbk: str
    fp: str
    sni: str
    flow: str
    path: str
    mode: str
    spx: str
    name: str


def parse_vless_uri(uri: str) -> VlessNode | None:
    uri = uri.strip()
    if not uri.startswith("vless://"):
        return None
    try:
        parsed = urllib.parse.urlparse(uri)
        user_id = parsed.username or ""
        host = parsed.hostname or ""
        port = parsed.port or 443
        qs = urllib.parse.parse_qs(parsed.query)

        def q_get(k: str, default: str = "") -> str:
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

        return VlessNode(
            user_id=user_id,
            host=host,
            port=port,
            net_type=net_type,
            security=security,
            pbk=pbk,
            fp=fp,
            sni=sni,
            flow=flow,
            path=path,
            mode=mode,
            spx=spx,
            name=name,
        )
    except Exception as e:
        print(f"WARNING: Failed to parse VLESS URI '{uri}': {e}", file=sys.stderr)
        return None


def parse_singbox_outbounds(data: dict) -> list[VlessNode]:
    nodes: list[VlessNode] = []
    for ob in data.get("outbounds", []):
        if ob.get("type") != "vless":
            continue
        host = ob.get("server")
        if not host:
            continue
        port = int(ob.get("server_port", 443))
        uuid_str = ob.get("uuid", "")
        flow = ob.get("flow", "")

        tls_cfg = ob.get("tls", {}) if isinstance(ob.get("tls"), dict) else {}
        reality_cfg = tls_cfg.get("reality", {}) if isinstance(tls_cfg.get("reality"), dict) else {}
        is_reality = reality_cfg.get("enabled", False) or bool(reality_cfg.get("public_key"))
        pbk = reality_cfg.get("public_key", "")
        sni = tls_cfg.get("server_name") or host
        utls_cfg = tls_cfg.get("utls", {}) if isinstance(tls_cfg.get("utls"), dict) else {}
        fp = utls_cfg.get("fingerprint", "chrome") if isinstance(utls_cfg, dict) else "chrome"

        tr_cfg = ob.get("transport", {}) if isinstance(ob.get("transport"), dict) else {}
        net_type = tr_cfg.get("type", "tcp").lower()
        path = tr_cfg.get("path", "/")
        mode = tr_cfg.get("mode", "auto")

        nodes.append(VlessNode(
            user_id=uuid_str,
            host=host,
            port=port,
            net_type=net_type,
            security="reality" if is_reality else ("tls" if tls_cfg.get("enabled") else "none"),
            pbk=pbk,
            fp=fp,
            sni=sni,
            flow=flow,
            path=path,
            mode=mode,
            spx="/",
            name=ob.get("tag", f"{host}:{port}"),
        ))
    return nodes


def vless_node_to_outbound(v: VlessNode, tag: str) -> dict:
    users: list[dict] = [
        {
            "id": v.user_id,
            "email": v.user_id,
            "encryption": "none",
        }
    ]
    if v.flow:
        users[0]["flow"] = v.flow

    stream_settings: dict = {"network": v.net_type}

    if v.security == "reality":
        stream_settings["security"] = "reality"
        stream_settings["realitySettings"] = {
            "fingerprint": v.fp,
            "serverName": v.sni,
            "publicKey": v.pbk,
            "shortId": "",
            "spiderX": v.spx or "/",
        }
    elif v.security == "tls":
        stream_settings["security"] = "tls"
        stream_settings["tlsSettings"] = {
            "fingerprint": v.fp,
            "serverName": v.sni,
            "allowInsecure": False,
        }

    if v.net_type == "xhttp":
        stream_settings["xhttpSettings"] = {
            "path": v.path if v.path.startswith("/") else "/" + v.path,
            "mode": v.mode or "auto",
        }
    elif v.net_type == "ws":
        stream_settings["wsSettings"] = {
            "path": v.path if v.path.startswith("/") else "/" + v.path,
            "headers": {
                "Host": v.sni,
            },
        }
    elif v.net_type == "grpc":
        stream_settings["grpcSettings"] = {
            "serviceName": v.path.lstrip("/"),
            "multiMode": True,
        }

    return {
        "tag": tag,
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": v.host,
                    "port": v.port,
                    "users": users,
                }
            ]
        },
        "streamSettings": stream_settings,
    }


def fetch_subscription_nodes(
    url_or_content: str,
    self_host: str,
    self_port: int,
    supabase_key: str | None = None,
) -> list[VlessNode]:
    url_or_content = url_or_content.strip()
    if not url_or_content:
        return []

    if url_or_content.startswith(("http://", "https://")):
        headers = {"User-Agent": "v2rayNG/1.9.0 (Xray-Relay-Updater)"}
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

    # 1. Base64 format decoding
    try:
        decoded = base64.b64decode(content).decode("utf-8", errors="replace")
        if "vless://" in decoded or "outbounds" in decoded:
            content = decoded.strip()
    except Exception:
        pass

    # 2. JSON sing-box or JSON list of URIs
    try:
        data = json.loads(content)
        if isinstance(data, dict) and "outbounds" in data:
            nodes = parse_singbox_outbounds(data)
            return [n for n in nodes if not (n.host == self_host and n.port == self_port)]
        elif isinstance(data, list):
            nodes = []
            for item in data:
                if isinstance(item, str) and item.startswith("vless://"):
                    p = parse_vless_uri(item)
                    if p and not (p.host == self_host and p.port == self_port):
                        nodes.append(p)
            if nodes:
                return nodes
    except Exception:
        pass

    # 3. Raw vless:// URIs
    raw_uris = re.findall(r"vless://[^\s,]+", content)
    nodes = []
    for u in raw_uris:
        p = parse_vless_uri(u)
        if not p:
            continue
        if p.host == self_host and p.port == self_port:
            print(f"Skipping self-referencing next-hop node: {p.name} ({p.host}:{p.port})")
            continue
        nodes.append(p)

    return nodes


def resolve_next_hop_outbounds(
    relay_cfg: RelayConfig,
    self_host: str,
    self_port: int,
    supabase_key: str | None = None,
    fatal_on_empty: bool = True,
) -> tuple[list[dict], list[VlessNode]]:
    sub_param = relay_cfg.next_hop.strip()
    if sub_param:
        nodes = fetch_subscription_nodes(sub_param, self_host, self_port, supabase_key)
        if nodes:
            outbounds = []
            for i, n in enumerate(nodes):
                tag = f"next-hop-{i+1}"
                outbounds.append(vless_node_to_outbound(n, tag))
            return outbounds, nodes
        else:
            msg = (
                f"FATAL ERROR: NEXT_HOP was specified ('{sub_param}'), but no valid next-hop nodes "
                f"could be extracted (failed to download, invalid format, or all nodes matched self host:port)!"
            )
            print(msg, file=sys.stderr)
            if fatal_on_empty:
                sys.exit(1)

    return [], []


# ==============================================================================
