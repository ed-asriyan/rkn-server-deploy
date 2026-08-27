#!/usr/bin/env python3
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

STATE_FILE = os.environ.get("TRAFFIC_STATE_FILE", "/data/unsent.json")


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load {STATE_FILE}: {e}", file=sys.stderr)
    return {"baseline": None, "unsent": []}


def save_state(state: dict):
    os.makedirs(os.path.dirname(os.path.abspath(STATE_FILE)), exist_ok=True)
    temp_file = f"{STATE_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(temp_file, STATE_FILE)


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
            # Ignore loopback and internal virtual interfaces
            if iface.startswith(("lo", "docker", "br-", "veth")):
                continue
            fields = data.split()
            if len(fields) >= 9:
                rx = int(fields[0])
                tx = int(fields[8])
                total_rx += rx
                total_tx += tx
    except Exception as e:
        print(f"Error reading /proc/net/dev: {e}", file=sys.stderr)
    return total_rx, total_tx


def send_unsent_records(provider_id: str, supabase_url: str, supabase_key: str):
    state = load_state()
    unsent = state.get("unsent", [])
    if not unsent:
        return

    if not provider_id:
        print("WARNING: SERVER_UUID is not set. Cannot submit traffic stats.", file=sys.stderr)
        return

    payload_data = {
        "provider_id": provider_id,
        "stats": unsent
    }

    payload = json.dumps(payload_data).encode("utf-8")

    url = f"{supabase_url.rstrip('/')}/functions/v1/submit_traffic"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {supabase_key}",
            "apikey": supabase_key,
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_data = resp.read().decode("utf-8")
            print(f"Successfully submitted {len(unsent)} traffic records to Supabase: {resp_data}")

        # Clear unsent queue on success
        state["unsent"] = []
        save_state(state)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        print(f"WARNING: HTTP error {e.code} submitting traffic to Supabase: {e.reason}. Body: {err_body}", file=sys.stderr)
    except Exception as e:
        print(f"WARNING: Failed to submit traffic to Supabase: {e}", file=sys.stderr)


def add_hourly_record(state: dict, period_start: str, period_end: str, rx_bytes: int, tx_bytes: int):
    total_bytes = rx_bytes + tx_bytes
    # Avoid duplicate period entries
    unsent = [item for item in state.get("unsent", []) if not (item["period_start"] == period_start and item["period_end"] == period_end)]
    unsent.append({
        "period_start": period_start,
        "period_end": period_end,
        "rx_bytes": rx_bytes,
        "tx_bytes": tx_bytes,
        "total_bytes": total_bytes
    })
    state["unsent"] = unsent
    print(f"Recorded traffic for [{period_start} - {period_end}]: RX={rx_bytes} B, TX={tx_bytes} B, Total={total_bytes} B")


def main():
    print("Starting Traffic Reporter daemon...")
    provider_id = os.environ.get("SERVER_UUID")
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SECRET_KEY")

    if not provider_id or not supabase_url or not supabase_key:
        print("WARNING: SERVER_UUID, SUPABASE_URL, or SUPABASE_SECRET_KEY environment variable is not set.", file=sys.stderr)
        return

    # Try sending any pending backlog on startup
    send_unsent_records(provider_id, supabase_url, supabase_key)

    while True:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        current_hour_start = now_utc.replace(minute=0, second=0, microsecond=0)
        next_hour_start = current_hour_start + datetime.timedelta(hours=1)

        state = load_state()
        baseline = state.get("baseline")
        curr_rx, curr_tx = get_network_bytes()

        if baseline is None:
            state["baseline"] = {
                "timestamp": current_hour_start.isoformat(),
                "rx_bytes": curr_rx,
                "tx_bytes": curr_tx
            }
            save_state(state)
            print(f"Initialized baseline at {current_hour_start.isoformat()}: RX={curr_rx}, TX={curr_tx}")
        else:
            base_time_str = baseline.get("timestamp")
            base_rx = baseline.get("rx_bytes", 0)
            base_tx = baseline.get("tx_bytes", 0)
            try:
                base_time = datetime.datetime.fromisoformat(base_time_str)
                if base_time < current_hour_start:
                    # An hour has completed! Calculate delta for [base_time, current_hour_start]
                    delta_rx = curr_rx - base_rx if curr_rx >= base_rx else curr_rx
                    delta_tx = curr_tx - base_tx if curr_tx >= base_tx else curr_tx
                    add_hourly_record(state, base_time.isoformat(), current_hour_start.isoformat(), delta_rx, delta_tx)
                    state["baseline"] = {
                        "timestamp": current_hour_start.isoformat(),
                        "rx_bytes": curr_rx,
                        "tx_bytes": curr_tx
                    }
                    save_state(state)
                    if supabase_url and supabase_key and provider_id:
                        send_unsent_records(provider_id, supabase_url, supabase_key)
            except Exception as e:
                print(f"Error checking baseline time: {e}", file=sys.stderr)
                state["baseline"] = {
                    "timestamp": current_hour_start.isoformat(),
                    "rx_bytes": curr_rx,
                    "tx_bytes": curr_tx
                }
                save_state(state)

        # Sleep until the top of the next hour (+2 seconds to guarantee passing the boundary)
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        seconds_to_wait = (next_hour_start - now_utc).total_seconds() + 2
        if seconds_to_wait > 0:
            print(f"Waiting {int(seconds_to_wait)} seconds until next hourly interval at {next_hour_start.isoformat()}...")
            time.sleep(seconds_to_wait)

        # After waking up, compute the completed hour [current_hour_start, next_hour_start]
        end_rx, end_tx = get_network_bytes()
        state = load_state()
        baseline = state.get("baseline")
        if baseline:
            base_time_str = baseline.get("timestamp")
            base_rx = baseline.get("rx_bytes", 0)
            base_tx = baseline.get("tx_bytes", 0)
            delta_rx = end_rx - base_rx if end_rx >= base_rx else end_rx
            delta_tx = end_tx - base_tx if end_tx >= base_tx else end_tx
            add_hourly_record(state, base_time_str, next_hour_start.isoformat(), delta_rx, delta_tx)

        state["baseline"] = {
            "timestamp": next_hour_start.isoformat(),
            "rx_bytes": end_rx,
            "tx_bytes": end_tx
        }
        save_state(state)

        send_unsent_records(provider_id, supabase_url, supabase_key)


if __name__ == "__main__":
    main()
