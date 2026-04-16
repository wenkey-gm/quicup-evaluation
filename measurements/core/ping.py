from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import List

from utils import docker_helpers
from utils.constants import PING_INTERVAL_SEC, PING_PACKET_LENGTH


def collect_ping_rtt(profile, count: int = 100) -> List[float]:
    print(
        f"\nPinging {profile.server_ip} via {profile.container} ({count} packets) ..."
    )
    stdout, _, rc = docker_helpers.docker_exec(
        profile.container,
        f"ping -i {PING_INTERVAL_SEC} -s {PING_PACKET_LENGTH} -c {count} -I {profile.bind_ip} {profile.server_ip}",
        timeout=int(count * (PING_INTERVAL_SEC + 1)) + 30,
    )
    if rc != 0 or not stdout:
        print("Ping test failed")
        return []

    rtt_list = [float(m) for m in re.findall(r"time=([\d.]+)\s*ms", stdout)]

    print(f"Collected {len(rtt_list)} RTT samples")

    return rtt_list


def save_ping_log(rtt_data: list[float], profile, output_dir: Path) -> Path:
    """Save ping RTT data to a JSON file."""
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    timestamp_filename = now.strftime("%Y%m%d_%H%M%S")
    timestamp_iso = now.isoformat()

    output_path = output_dir / f"ping_{profile.label}_{timestamp_filename}.json"

    log_data = {
        "protocol": "ping",
        "profile": profile.label,
        "target_ip": profile.server_ip,
        "rtts": rtt_data,
        "timestamp": timestamp_iso,
        "color": profile.color,
    }

    with open(output_path, "w") as f:
        json.dump(log_data, f, indent=4)

    return output_path
