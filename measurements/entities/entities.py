from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Profile:
    """A single 5G user-plane measurement target."""

    label: str
    container: str
    server_ip: str
    bind_ip: str
    color: str


@dataclass
class PerformanceMetrics:
    bitrate_mbps: float = field(default=0.0)
    jitter_ms: float = field(default=0.0)
    lost_packets: int = field(default=0)
    total_packets: int = field(default=0)
    lost_percent: float = field(default=0.0)

    def display(self) -> None:
        print("\n  Network Performance Metrics:")
        print(f"Bitrate: {self.bitrate_mbps:.2f} Mbps")
        print(f"Jitter: {self.jitter_ms:.3f} ms")
        print(
            f"Packet Loss : {self.lost_percent:.2f}% ({self.lost_packets}/{self.total_packets})"
        )


def timestamp_now() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
