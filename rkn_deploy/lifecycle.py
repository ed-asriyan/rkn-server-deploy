from __future__ import annotations

import datetime
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

from .config import AppConfig, SupabaseConfig
from .crypto import derive_keypair, derive_user_uuids
from .sni import SniSelection, discover_best_sni
from .subscription import VlessNode, resolve_next_hop_outbounds
from .xray_config import ClientUriBuilder, XrayConfigBuilder

# Domain 6: Lifecycle & Supervisor Domain (Lifecycle Domain)
# ==============================================================================

class SupabasePublisher:
    @staticmethod
    def publish_server_uris(cfg: SupabaseConfig, name: str, uris: list[str]) -> bool:
        if not cfg.url or not cfg.secret_key or not cfg.server_uuid:
            print("SUPABASE_URL, SUPABASE_SECRET_KEY, or SUPABASE_SERVER_UUID not set. Skipping Supabase upload.")
            return False

        print(f"Submitting {len(uris)} URIs to Supabase at {cfg.url}...")
        url = f"{cfg.url.rstrip('/')}/functions/v1/submit_server"
        payload_data = {
            "id": cfg.server_uuid,
            "server_id": cfg.server_uuid,
            "name": name,
            "server_name": name,
            "uris": uris,
        }
        payload = json.dumps(payload_data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {cfg.secret_key}",
                "apikey": cfg.secret_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp_data = resp.read().decode("utf-8")
                print(f"Supabase response ({resp.status}): {resp_data}")
                return True
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
            print(f"WARNING: Failed to submit URIs to Supabase: HTTP Error {e.code}: {e.reason}. Body: {err_body}", file=sys.stderr)
        except Exception as e:
            print(f"WARNING: Failed to submit URIs to Supabase: {e}", file=sys.stderr)
        return False


class XraySupervisor:
    def __init__(self, app_config: AppConfig, uuids: list[str], private_key: str, public_key: str):
        self.app_cfg = app_config
        self.uuids = uuids
        self.private_key = private_key
        self.public_key = public_key
        self.xray_proc: subprocess.Popen | None = None
        self.config_path = "/etc/xray/config.json"
        self.current_nodes: list[VlessNode] = []
        self.sni_selection: SniSelection | None = None

    def setup_signal_handlers(self):
        def sig_handler(signum, frame):
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGTERM, sig_handler)
        signal.signal(signal.SIGINT, sig_handler)

    def resolve_sni_and_fallback(self):
        """Resolves the Reality SNI and fallback target from active probes only."""
        self.sni_selection = discover_best_sni(self.app_cfg.server.host)

    @property
    def chosen_snis(self) -> list[str]:
        if not self.sni_selection:
            return []
        return [self.sni_selection.server_name]

    @property
    def chosen_fallback_target(self) -> str:
        if not self.sni_selection:
            return ""
        return self.sni_selection.fallback_target

    def start(self):
        self.setup_signal_handlers()

        print("[launch] Stage 2/3: selecting Reality SNI/fallback target and generating Xray config...", flush=True)
        self.resolve_sni_and_fallback()

        # Generate and print client VLESS URIs
        client_uris = ClientUriBuilder.build_uris(
            self.app_cfg.server,
            self.uuids,
            self.public_key,
            self.chosen_snis,
        )

        print(f"\n=======================================================")
        print(f"Generated {len(client_uris)} client VLESS URIs for provider '{self.app_cfg.server.server_name}' (mode: {self.app_cfg.server.mode.value}):")
        print(f"SNI: {self.chosen_snis} | Fallback: {self.chosen_fallback_target} | Fingerprint: {self.app_cfg.server.fingerprint}")
        print(f"=======================================================")
        for u in client_uris:
            print(u)
        sys.stdout.flush()

        # Publish URIs to Supabase
        SupabasePublisher.publish_server_uris(
            self.app_cfg.supabase,
            self.app_cfg.server.server_name,
            client_uris,
        )

        # Initial next-hop resolution
        if self.app_cfg.relay.next_hop:
            print("Waiting 5 seconds for backend node redistribution before resolving NEXT_HOP...")
            time.sleep(5)

        next_hop_outbounds, self.current_nodes = resolve_next_hop_outbounds(
            self.app_cfg.relay,
            self.app_cfg.server.host,
            self.app_cfg.server.port,
            self.app_cfg.supabase.secret_key,
        )

        if next_hop_outbounds:
            print(f"Configured {len(next_hop_outbounds)} next-hop relay server(s) from NEXT_HOP with dynamic leastPing load balancing:")
            for n in self.current_nodes:
                print(f"  -> [{n.name}] {n.host}:{n.port} ({n.net_type})")

        # Generate config and start Xray
        self._write_config(next_hop_outbounds)

        print("[launch] Stage 3/3: starting Xray supervisor...", flush=True)
        self._spawn_xray()

        # Supervisor loop
        self._run_loop()

    def _write_config(self, next_hop_outbounds: list[dict]):
        xray_cfg = XrayConfigBuilder.build_config(
            server_cfg=self.app_cfg.server,
            relay_cfg=self.app_cfg.relay,
            uuids=self.uuids,
            private_key=self.private_key,
            fallback_dest=self.chosen_fallback_target,
            snis=self.chosen_snis,
            next_hop_outbounds=next_hop_outbounds,
        )
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(xray_cfg, f, indent=2)
        print(f"Generated Xray config at {self.config_path}")

    def _spawn_xray(self):
        print(f"Starting Xray ({self.app_cfg.xray_binary} run -c {self.config_path})...")
        sys.stdout.flush()
        sys.stderr.flush()
        self.xray_proc = subprocess.Popen([self.app_cfg.xray_binary, "run", "-c", self.config_path])

    def stop(self):
        if self.xray_proc and self.xray_proc.poll() is None:
            try:
                self.xray_proc.terminate()
                self.xray_proc.wait(timeout=5)
            except Exception:
                try:
                    self.xray_proc.kill()
                except Exception:
                    pass

    def _run_loop(self):
        sub_param = self.app_cfg.relay.next_hop.strip()
        is_sub_url = sub_param.startswith(("http://", "https://"))
        last_update_time = time.time()

        while True:
            try:
                time.sleep(5)
                # Process health check
                if self.xray_proc and self.xray_proc.poll() is not None:
                    print(f"ERROR: Xray process exited with code {self.xray_proc.returncode}.", file=sys.stderr)
                    sys.exit(self.xray_proc.returncode)

                # Periodic subscription polling
                if is_sub_url and (time.time() - last_update_time >= self.app_cfg.relay.update_interval):
                    last_update_time = time.time()
                    print(f"Polling next-hop subscription from {sub_param}...")
                    new_outbounds, new_nodes = resolve_next_hop_outbounds(
                        self.app_cfg.relay,
                        self.app_cfg.server.host,
                        self.app_cfg.server.port,
                        self.app_cfg.supabase.secret_key,
                        fatal_on_empty=False,
                    )
                    if new_outbounds and new_nodes != self.current_nodes:
                        print(f"Subscription updated! Found {len(new_outbounds)} nodes. Reloading Xray...")
                        self.current_nodes = new_nodes
                        self._write_config(new_outbounds)
                        self.stop()
                        self._spawn_xray()
                        print("Xray successfully reloaded with updated next-hop nodes.")
                    elif not new_outbounds:
                        print("WARNING: Background subscription poll returned 0 nodes. Keeping existing configuration.", file=sys.stderr)

            except KeyboardInterrupt:
                break
        self.stop()


def launch_server():
    print("[launch] Stage 1/3: parsing runtime configuration and deriving identities...", flush=True)
    app_config = AppConfig.parse_from_env()

    private_key, public_key = derive_keypair(app_config.server.seed, app_config.server.host)
    uuids = derive_user_uuids(app_config.server.number_of_users, app_config.server.seed, app_config.server.host)

    supervisor = XraySupervisor(app_config, uuids, private_key, public_key)
    supervisor.start()


DEFAULT_TRAFFIC_STATE_FILE = "/data/unsent.json"


def get_traffic_state_file() -> str:
    return (os.environ.get("TRAFFIC_STATE_FILE") or DEFAULT_TRAFFIC_STATE_FILE).strip()


def load_traffic_state() -> dict:
    state_file = get_traffic_state_file()
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load {state_file}: {e}", file=sys.stderr)
    return {"baseline": None, "unsent": []}


def save_traffic_state(state: dict):
    state_file = get_traffic_state_file()
    os.makedirs(os.path.dirname(os.path.abspath(state_file)), exist_ok=True)
    temp_file = f"{state_file}.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(temp_file, state_file)


def get_network_bytes() -> tuple[int, int]:
    """Reads cumulative RX and TX bytes from /proc/net/dev excluding virtual/loopback interfaces."""
    total_rx = 0
    total_tx = 0
    try:
        with open("/proc/net/dev", "r") as f:
            lines = f.readlines()
        for line in lines[2:]:
            if ":" not in line:
                continue
            iface, data = line.split(":", 1)
            iface = iface.strip()
            if iface.startswith(("lo", "docker", "br-", "veth")):
                continue
            fields = data.split()
            if len(fields) >= 9:
                total_rx += int(fields[0])
                total_tx += int(fields[8])
    except Exception as e:
        print(f"Error reading /proc/net/dev: {e}", file=sys.stderr)
    return total_rx, total_tx


def submit_traffic_records(provider_id: str, supabase_url: str, supabase_key: str):
    state = load_traffic_state()
    unsent = state.get("unsent", [])
    if not unsent:
        return

    if not provider_id:
        print("WARNING: SUPABASE_SERVER_UUID is not set. Cannot submit traffic stats.", file=sys.stderr)
        return

    payload = json.dumps({"provider_id": provider_id, "stats": unsent}).encode("utf-8")
    req = urllib.request.Request(
        f"{supabase_url.rstrip('/')}/functions/v1/submit_traffic",
        data=payload,
        headers={
            "Authorization": f"Bearer {supabase_key}",
            "apikey": supabase_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_data = resp.read().decode("utf-8")
            print(f"Successfully submitted {len(unsent)} traffic records to Supabase: {resp_data}")

        state["unsent"] = []
        save_traffic_state(state)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        print(f"WARNING: HTTP error {e.code} submitting traffic to Supabase: {e.reason}. Body: {err_body}", file=sys.stderr)
    except Exception as e:
        print(f"WARNING: Failed to submit traffic to Supabase: {e}", file=sys.stderr)


def add_hourly_traffic_record(state: dict, period_start: str, period_end: str, rx_bytes: int, tx_bytes: int):
    total_bytes = rx_bytes + tx_bytes
    unsent = [
        item for item in state.get("unsent", [])
        if not (item["period_start"] == period_start and item["period_end"] == period_end)
    ]
    unsent.append({
        "period_start": period_start,
        "period_end": period_end,
        "rx_bytes": rx_bytes,
        "tx_bytes": tx_bytes,
        "total_bytes": total_bytes,
    })
    state["unsent"] = unsent
    print(f"Recorded traffic for [{period_start} - {period_end}]: RX={rx_bytes} B, TX={tx_bytes} B, Total={total_bytes} B")


def run_traffic_reporter():
    def sig_handler(signum, frame):
        sys.exit(0)

    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)

    provider_id = (os.environ.get("SUPABASE_SERVER_UUID") or "").strip()
    supabase_url = (os.environ.get("SUPABASE_URL") or "").strip()
    supabase_key = (os.environ.get("SUPABASE_SECRET_KEY") or "").strip()

    if not provider_id or not supabase_url or not supabase_key:
        print("Traffic Reporter: Supabase credentials not provided (SUPABASE_SERVER_UUID, SUPABASE_URL, or SUPABASE_SECRET_KEY). Traffic reporting is disabled (sleeping).")
        while True:
            time.sleep(3600)

    print("Starting Traffic Reporter daemon...")
    submit_traffic_records(provider_id, supabase_url, supabase_key)

    while True:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        current_hour_start = now_utc.replace(minute=0, second=0, microsecond=0)
        next_hour_start = current_hour_start + datetime.timedelta(hours=1)

        state = load_traffic_state()
        baseline = state.get("baseline")
        curr_rx, curr_tx = get_network_bytes()

        if baseline is None:
            state["baseline"] = {
                "timestamp": current_hour_start.isoformat(),
                "rx_bytes": curr_rx,
                "tx_bytes": curr_tx,
            }
            save_traffic_state(state)
            print(f"Initialized baseline at {current_hour_start.isoformat()}: RX={curr_rx}, TX={curr_tx}")
        else:
            base_time_str = baseline.get("timestamp")
            base_rx = baseline.get("rx_bytes", 0)
            base_tx = baseline.get("tx_bytes", 0)
            try:
                base_time = datetime.datetime.fromisoformat(base_time_str)
                if base_time < current_hour_start:
                    delta_rx = curr_rx - base_rx if curr_rx >= base_rx else curr_rx
                    delta_tx = curr_tx - base_tx if curr_tx >= base_tx else curr_tx
                    add_hourly_traffic_record(state, base_time.isoformat(), current_hour_start.isoformat(), delta_rx, delta_tx)
                    state["baseline"] = {
                        "timestamp": current_hour_start.isoformat(),
                        "rx_bytes": curr_rx,
                        "tx_bytes": curr_tx,
                    }
                    save_traffic_state(state)
                    submit_traffic_records(provider_id, supabase_url, supabase_key)
            except Exception as e:
                print(f"Error checking baseline time: {e}", file=sys.stderr)
                state["baseline"] = {
                    "timestamp": current_hour_start.isoformat(),
                    "rx_bytes": curr_rx,
                    "tx_bytes": curr_tx,
                }
                save_traffic_state(state)

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        seconds_to_wait = (next_hour_start - now_utc).total_seconds() + 2
        if seconds_to_wait > 0:
            print(f"Waiting {int(seconds_to_wait)} seconds until next hourly interval at {next_hour_start.isoformat()}...")
            time.sleep(seconds_to_wait)

        end_rx, end_tx = get_network_bytes()
        state = load_traffic_state()
        baseline = state.get("baseline")
        if baseline:
            base_time_str = baseline.get("timestamp")
            base_rx = baseline.get("rx_bytes", 0)
            base_tx = baseline.get("tx_bytes", 0)
            delta_rx = end_rx - base_rx if end_rx >= base_rx else end_rx
            delta_tx = end_tx - base_tx if end_tx >= base_tx else end_tx
            add_hourly_traffic_record(state, base_time_str, next_hour_start.isoformat(), delta_rx, delta_tx)

        state["baseline"] = {
            "timestamp": next_hour_start.isoformat(),
            "rx_bytes": end_rx,
            "tx_bytes": end_tx,
        }
        save_traffic_state(state)
        submit_traffic_records(provider_id, supabase_url, supabase_key)


# ==============================================================================
