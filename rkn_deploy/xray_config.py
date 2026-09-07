from __future__ import annotations

import urllib.parse

from .config import RelayConfig, ServerConfig, XrayInboundMode

# Domain 4: Xray Config Domain (Xray Config Domain)
# ==============================================================================

class InboundBuilder:
    @staticmethod
    def build_inbound(
        config: ServerConfig,
        uuids: list[str],
        private_key: str,
        fallback_dest: str,
        snis: list[str],
    ) -> dict:
        if config.mode == XrayInboundMode.VLESS_REALITY_TCP:
            clients = [
                {
                    "email": u,
                    "flow": "xtls-rprx-vision",
                    "id": u,
                }
                for u in uuids
            ]
            stream_settings = {
                "network": "tcp",
                "realitySettings": {
                    "dest": fallback_dest,
                    "maxTimediff": 0,
                    "privateKey": private_key,
                    "serverNames": snis,
                    "shortIds": [""],
                    "show": False,
                    "xver": 0,
                },
                "security": "reality",
                "tcpSettings": {
                    "acceptProxyProtocol": False,
                    "header": {
                        "type": "none",
                    },
                },
            }
        else:  # VLESS_REALITY_XHTTP
            clients = [
                {
                    "email": u,
                    "id": u,
                }
                for u in uuids
            ]
            stream_settings = {
                "network": "xhttp",
                "xhttpSettings": {
                    "path": config.xhttp_path,
                    "mode": config.xhttp_mode,
                },
                "realitySettings": {
                    "dest": fallback_dest,
                    "maxTimediff": 0,
                    "privateKey": private_key,
                    "serverNames": snis,
                    "shortIds": [""],
                    "show": False,
                    "xver": 0,
                },
                "security": "reality",
            }

        return {
            "listen": None,
            "port": config.port,
            "protocol": "vless",
            "settings": {
                "clients": clients,
                "decryption": "none",
                "fallbacks": [],
            },
            "streamSettings": stream_settings,
            "tag": "inbound-vless",
            "sniffing": {
                "enabled": True,
                "destOverride": ["http", "tls", "quic"],
            },
            "allocate": {
                "strategy": "always",
                "refresh": 5,
                "concurrency": 3,
            },
        }


class ClientUriBuilder:
    @staticmethod
    def build_uris(
        config: ServerConfig,
        uuids: list[str],
        public_key: str,
        snis: list[str],
    ) -> list[str]:
        uris: list[str] = []
        encoded_name = urllib.parse.quote(config.server_name)
        encoded_path = urllib.parse.quote(config.xhttp_path, safe="")

        for u in uuids:
            for sni in snis:
                if config.mode == XrayInboundMode.VLESS_REALITY_TCP:
                    uri = (
                        f"vless://{u}@{config.host}:{config.port}?"
                        f"type=tcp&security=reality&pbk={public_key}&fp={config.fingerprint}&"
                        f"sni={sni}&spx=%2F&flow=xtls-rprx-vision#{encoded_name}"
                    )
                else:  # VLESS_REALITY_XHTTP
                    uri = (
                        f"vless://{u}@{config.host}:{config.port}?"
                        f"type=xhttp&security=reality&pbk={public_key}&fp={config.fingerprint}&"
                        f"sni={sni}&path={encoded_path}&mode={config.xhttp_mode}&spx=%2F#{encoded_name}"
                    )
                uris.append(uri)
        return uris


class OutboundBuilder:
    @staticmethod
    def build_outbounds(has_next_hop: bool, next_hop_outbounds: list[dict]) -> list[dict]:
        if has_next_hop:
            outbounds = list(next_hop_outbounds)
            outbounds.append({"tag": "blocked", "protocol": "blackhole", "settings": {}})
            return outbounds
        else:
            return [
                {
                    "tag": "direct-ipv4",
                    "protocol": "freedom",
                    "settings": {
                        "domainStrategy": "UseIPv4",
                    },
                },
                {
                    "tag": "direct-ipv6",
                    "protocol": "freedom",
                    "settings": {
                        "domainStrategy": "UseIPv6",
                    },
                },
                {
                    "tag": "blocked",
                    "protocol": "blackhole",
                    "settings": {},
                },
            ]


class RoutingBuilder:
    @staticmethod
    def build_routing(has_next_hop: bool, whitelist_domains: list[str]) -> dict:
        rules: list[dict] = []
        balancer_tag = "next-hop-balancer" if has_next_hop else "smart-balancer"

        if whitelist_domains:
            rules.append({
                "type": "field",
                "inboundTag": ["inbound-vless"],
                "balancerTag": balancer_tag,
                "domain": [f"domain:{d}" for d in whitelist_domains],
            })

        rules.extend([
            {
                "type": "field",
                "inboundTag": ["inbound-vless"],
                "outboundTag": "blocked",
                "ip": ["geoip:private"],
            },
            {
                "type": "field",
                "inboundTag": ["inbound-vless"],
                "outboundTag": "blocked",
                "domain": ["geosite:category-ru"],
            },
            {
                "type": "field",
                "inboundTag": ["inbound-vless"],
                "outboundTag": "blocked",
                "ip": ["geoip:ru"],
            },
        ])

        if has_next_hop:
            rules.extend([
                {
                    "type": "field",
                    "inboundTag": ["inbound-vless"],
                    "ip": ["::/0"],
                    "balancerTag": "next-hop-balancer",
                },
                {
                    "type": "field",
                    "inboundTag": ["inbound-vless"],
                    "balancerTag": "next-hop-balancer",
                },
            ])
        else:
            rules.append({
                "type": "field",
                "network": "tcp,udp",
                "balancerTag": "smart-balancer",
            })

        balancers = [
            {
                "tag": "next-hop-balancer",
                "selector": ["next-hop-"],
                "strategy": {
                    "type": "leastPing",
                },
            }
        ] if has_next_hop else [
            {
                "tag": "smart-balancer",
                "selector": ["direct-ipv4", "direct-ipv6"],
                "strategy": {
                    "type": "leastPing",
                },
            }
        ]

        return {
            "domainStrategy": "IPIfNonMatch",
            "balancers": balancers,
            "rules": rules,
        }


class ObservatoryBuilder:
    @staticmethod
    def build_observatory(has_next_hop: bool, relay_cfg: RelayConfig) -> dict:
        if has_next_hop:
            return {
                "subjectSelector": ["next-hop-"],
                "probeURL": relay_cfg.probe_url,
                "probeInterval": relay_cfg.probe_interval,
                "enableConcurrency": True,
            }
        else:
            return {
                "subjectSelector": ["direct-ipv4", "direct-ipv6"],
                "probeURL": "http://cp.cloudflare.com/generate_204",
                "probeInterval": "24h",
                "enableConcurrency": True,
            }


class XrayConfigBuilder:
    @staticmethod
    def build_config(
        server_cfg: ServerConfig,
        relay_cfg: RelayConfig,
        uuids: list[str],
        private_key: str,
        fallback_dest: str,
        snis: list[str],
        next_hop_outbounds: list[dict],
    ) -> dict:
        has_next_hop = len(next_hop_outbounds) > 0
        inbound = InboundBuilder.build_inbound(
            server_cfg, uuids, private_key, fallback_dest, snis
        )
        if has_next_hop:
            inbound["sniffing"]["routeOnly"] = True

        outbounds = OutboundBuilder.build_outbounds(has_next_hop, next_hop_outbounds)
        routing = RoutingBuilder.build_routing(has_next_hop, server_cfg.whitelist_domains)
        observatory = ObservatoryBuilder.build_observatory(has_next_hop, relay_cfg)

        return {
            "inbounds": [inbound],
            "outbounds": outbounds,
            "routing": routing,
            "observatory": observatory,
            "log": {
                "access": "none",
                "dnsLog": False,
                "loglevel": "warning",
                "maskAddress": "",
            },
        }


# ==============================================================================
