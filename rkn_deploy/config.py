from __future__ import annotations

import dataclasses
import enum
import os
import sys

# Domain 1: Configuration & Validation Domain (Config Domain)
# ==============================================================================

class XrayInboundMode(str, enum.Enum):
    VLESS_REALITY_TCP = "vless-reality-tcp"
    VLESS_REALITY_XHTTP = "vless-reality-xhttp"


@dataclasses.dataclass(frozen=True)
class ServerConfig:
    mode: XrayInboundMode
    server_name: str
    host: str
    port: int
    fingerprint: str
    xhttp_path: str
    xhttp_mode: str
    number_of_users: int
    seed: str | None
    whitelist_domains: list[str]


@dataclasses.dataclass(frozen=True)
class RelayConfig:
    next_hop: str
    update_interval: int
    probe_url: str
    probe_interval: str


@dataclasses.dataclass(frozen=True)
class SupabaseConfig:
    url: str | None
    secret_key: str | None
    server_uuid: str | None


@dataclasses.dataclass(frozen=True)
class AppConfig:
    server: ServerConfig
    relay: RelayConfig
    supabase: SupabaseConfig
    xray_binary: str

    @classmethod
    def parse_from_env(cls) -> AppConfig:
        def get_env(key: str, default: str = "") -> str:
            return (os.environ.get(key) or default).strip()

        mode_raw = get_env("MODE").lower()
        if not mode_raw:
            print("ERROR: MODE environment variable is required (vless-reality-tcp or vless-reality-xhttp).", file=sys.stderr)
            sys.exit(1)
        try:
            mode = XrayInboundMode(mode_raw)
        except ValueError:
            print(f"ERROR: Invalid MODE '{mode_raw}'. Must be 'vless-reality-tcp' or 'vless-reality-xhttp'.", file=sys.stderr)
            sys.exit(1)

        host = get_env("HOST")
        if not host:
            print("ERROR: HOST environment variable is required.", file=sys.stderr)
            sys.exit(1)

        server_name = get_env("SERVER_NAME") or get_env("COMPOSE_PROJECT_NAME") or host

        port_str = get_env("PORT")
        if not port_str:
            print("ERROR: PORT environment variable is required.", file=sys.stderr)
            sys.exit(1)
        try:
            port = int(port_str)
        except ValueError:
            print(f"ERROR: Invalid PORT '{port_str}'. Must be an integer.", file=sys.stderr)
            sys.exit(1)

        # For XHTTP Reality, uTLS fingerprint must be chrome (random breaks ALPN h2)
        fingerprint = get_env("FINGERPRINT", "chrome") or "chrome"

        xhttp_path = get_env("XHTTP_PATH", "/xhttp")
        if not xhttp_path.startswith("/"):
            xhttp_path = "/" + xhttp_path

        xhttp_mode = get_env("XHTTP_MODE", "stream-one").lower()

        num_users_str = get_env("NUMBER_OF_USERS")
        if not num_users_str:
            print("ERROR: NUMBER_OF_USERS environment variable is required.", file=sys.stderr)
            sys.exit(1)
        try:
            number_of_users = int(num_users_str)
        except ValueError:
            print(f"ERROR: Invalid NUMBER_OF_USERS '{num_users_str}'. Must be an integer.", file=sys.stderr)
            sys.exit(1)

        seed = get_env("SEED") or None

        whitelist_domains_str = get_env("WHITELIST_DOMAINS")
        whitelist_domains = [d.strip() for d in whitelist_domains_str.split(",") if d.strip()]

        # Supabase config
        supabase_url = get_env("SUPABASE_URL") or None
        supabase_key = get_env("SUPABASE_SECRET_KEY") or get_env("SUPABASE_SERVICE_ROLE_KEY") or None
        supabase_uuid = get_env("SUPABASE_SERVER_UUID") or get_env("SERVER_UUID") or None

        if supabase_url and supabase_key and not supabase_uuid:
            print("ERROR: SUPABASE_SERVER_UUID is required when SUPABASE_URL is provided.", file=sys.stderr)
            sys.exit(1)

        # Relay next-hop config
        next_hop = get_env("NEXT_HOP")
        try:
            update_interval = int(get_env("NEXT_HOP_UPDATE_INTERVAL", "3600"))
        except ValueError:
            update_interval = 3600

        probe_url = get_env("NEXT_HOP_PROBE_URL", "http://cp.cloudflare.com/generate_204")
        probe_interval = get_env("NEXT_HOP_PROBE_INTERVAL", "1m")

        xray_binary = get_env("XRAY_BINARY", "xray")

        server_cfg = ServerConfig(
            mode=mode,
            server_name=server_name,
            host=host,
            port=port,
            fingerprint=fingerprint,
            xhttp_path=xhttp_path,
            xhttp_mode=xhttp_mode,
            number_of_users=number_of_users,
            seed=seed,
            whitelist_domains=whitelist_domains,
        )

        relay_cfg = RelayConfig(
            next_hop=next_hop,
            update_interval=update_interval,
            probe_url=probe_url,
            probe_interval=probe_interval,
        )

        supabase_cfg = SupabaseConfig(
            url=supabase_url,
            secret_key=supabase_key,
            server_uuid=supabase_uuid,
        )

        return cls(
            server=server_cfg,
            relay=relay_cfg,
            supabase=supabase_cfg,
            xray_binary=xray_binary,
        )


# ==============================================================================
