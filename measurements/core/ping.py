from __future__ import annotations

import re
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
