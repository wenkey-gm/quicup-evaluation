"""
5G User-Plane Performance Benchmarks
=====================================
Compare throughput, jitter, packet loss and latency across three
transport strategies on the N3 interface:

  - QUIC      (QUICUP — natively encrypted)
  - GTP-U     (standard, unencrypted)
  - GTP-U+IPsec (encrypted via StrongSwan ESP tunnel)

Each profile runs an iperf3 UDP test through the corresponding
UE → gNB → UPF path, then a ping latency sweep.  Results are
saved as JSON logs and comparison PNG plots.

Usage
-----
    python main.py                          # all profiles, all metrics
    python main.py -p quic gtpu             # only QUIC and GTP-U
    python main.py -p ipsec                 # only GTP-U+IPsec
    python main.py -m rtt                   # latency only (ping)
    python main.py -m throughput jitter      # iperf3 metrics only
    python main.py -p quic -m rtt throughput # combine both flags
    python main.py --ping-count 50           # 50 ping packets (default: 30)
    python main.py --iperf-duration 60       # 60s iperf3 test (default: 100)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

# ──────────────────────────────────────────────────────────────
#  Data Models
# ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Profile:
    """A single 5G user-plane measurement target."""

    key: str
    label: str
    ue_container: str
    server_ip: str
    bind_ip: str
    color: str


@dataclass(frozen=True)
class MetricPlot:
    """Describes one time-series comparison chart."""

    column: str
    title: str
    y_label: str
    filename: str
    marker: str = "o"


@dataclass
class PerformanceMetrics:
    """Distilled results from a single iperf3 run."""

    bitrate_mbps: float = 0.0
    jitter_ms: float = 0.0
    lost_packets: int = 0
    total_packets: int = 0
    lost_percent: float = 0.0

    def display(self) -> None:
        print("\n  Network Performance Metrics:")
        print(f"     Bitrate      : {self.bitrate_mbps:.2f} Mbps")
        print(f"     Jitter       : {self.jitter_ms:.3f} ms")
        print(
            f"     Packet Loss  : {self.lost_percent:.2f}% "
            f"({self.lost_packets}/{self.total_packets})"
        )


# ──────────────────────────────────────────────────────────────
#  Configuration
# ──────────────────────────────────────────────────────────────

PROFILES: dict[str, Profile] = {
    "quic": Profile(
        key="quic",
        label="QUICUP (Encrypted)",
        ue_container="ueransim-ue-quic",
        server_ip="10.46.0.1",
        bind_ip="10.46.0.2",
        color="#2ecc71",
    ),
    "gtpu": Profile(
        key="gtpu",
        label="GTP-U (Standard)",
        ue_container="ueransim-ue-gtpu",
        server_ip="10.45.0.1",
        bind_ip="10.45.0.2",
        color="#3498db",
    ),
    "ipsec": Profile(
        key="ipsec",
        label="GTP-U + IPsec (Encrypted)",
        ue_container="ueransim-ue-gtpu-ipsec",
        server_ip="10.47.0.1",
        bind_ip="10.47.0.2",
        color="#e74c3c",
    ),
}

ALL_METRIC_PLOTS: dict[str, list[MetricPlot]] = {
    "throughput": [
        MetricPlot(
            "bitrate_mbps", "Bitrate Over Time", "Bitrate (Mbps)", "bitrate", "o"
        ),
        MetricPlot(
            "lost_percent",
            "Packet Loss Over Time",
            "Packet Loss (%)",
            "packet_loss",
            "^",
        ),
    ],
    "jitter": [
        MetricPlot("jitter_ms", "Jitter Over Time", "Jitter (ms)", "jitter", "s"),
    ],
}

AVAILABLE_METRICS = ["rtt", "throughput", "jitter"]

IPERF_DURATION_SEC = 100
IPERF_BANDWIDTH = "100M"
IPERF_MAX_RETRIES = 3
IPERF_RETRY_DELAY_SEC = 10

PING_COUNT = 30
PING_INTERVAL_SEC = 1

OUTPUT_DIR = Path("plots")
COMMAND_TIMEOUT_SEC = 120

# Cooldown between consecutive iperf3 runs to avoid "server busy"
INTER_PROFILE_PAUSE_SEC = 5


# ──────────────────────────────────────────────────────────────
#  Shell Helpers
# ──────────────────────────────────────────────────────────────


def run_shell(
    command: str, timeout: int = COMMAND_TIMEOUT_SEC
) -> tuple[str | None, str | None, int]:
    """Run *command* in a shell and return ``(stdout, stderr, exit_code)``."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return None, f"Command timed out after {timeout}s", -1
    except Exception as exc:
        return None, str(exc), -1


def is_container_running(name: str) -> bool:
    """Return *True* when a Docker container called *name* is up."""
    stdout, _, _ = run_shell(
        f"docker ps --filter name={name} --format '{{{{.Names}}}}'"
    )
    return name in (stdout or "")


def docker_exec(
    container: str, cmd: str, **kwargs
) -> tuple[str | None, str | None, int]:
    """Shorthand for ``docker exec <container> <cmd>``."""
    return run_shell(f"docker exec {container} {cmd}", **kwargs)


# ──────────────────────────────────────────────────────────────
#  iperf3 — Server Management
# ──────────────────────────────────────────────────────────────


def start_iperf3_server(container: str) -> bool:
    """(Re)start an iperf3 server inside *container*. Returns success flag."""
    print(f"\n  Starting iperf3 server on {container} ...")

    docker_exec(container, "pkill -9 iperf3", timeout=5)
    time.sleep(1)

    _, stderr, rc = run_shell(f"docker exec -d {container} iperf3 -s", timeout=10)
    if rc != 0:
        print(f"  Could not start iperf3 server: {stderr}")
        return False

    time.sleep(2)

    stdout, _, _ = docker_exec(container, "pgrep -f 'iperf3 -s'", timeout=5)
    if stdout and stdout.strip():
        print(f"  iperf3 server running (PID {stdout.strip()})")
        return True

    print("  Could not confirm iperf3 server is running")
    return False


# ──────────────────────────────────────────────────────────────
#  iperf3 — Client Test
# ──────────────────────────────────────────────────────────────


def run_iperf3_test(
    profile: Profile,
    duration: int = IPERF_DURATION_SEC,
    retries: int = IPERF_MAX_RETRIES,
    retry_delay: int = IPERF_RETRY_DELAY_SEC,
) -> dict | None:
    """Execute an iperf3 UDP reverse-mode test through *profile*'s UE container.

    Retries automatically when the server reports "busy".
    Returns the parsed JSON results or *None* on failure.
    """
    container = profile.ue_container
    iperf_flags = (
        f"-c {profile.server_ip} -B {profile.bind_ip} "
        f"-u -b {IPERF_BANDWIDTH} -R --get-server-output -t {duration} -J"
    )

    for attempt in range(1, retries + 1):
        print(f"\n  iperf3 UDP test  (attempt {attempt}/{retries}, {duration}s)")
        print(f"    -> server {profile.server_ip}  bind {profile.bind_ip}")

        stdout, stderr, rc = docker_exec(
            container, f"iperf3 {iperf_flags}", timeout=duration + 60
        )

        if rc != 0:
            print(f"  Test failed (exit {rc})")
            if stderr:
                print(f"    {stderr.strip()}")
            if (
                "server is busy" in (stderr or "") + (stdout or "")
                and attempt < retries
            ):
                print(f"    Server busy — retrying in {retry_delay}s ...")
                time.sleep(retry_delay)
                continue
            return None

        if not stdout:
            print("  iperf3 produced no output")
            return None

        try:
            results = json.loads(stdout)
            print("  iperf3 test completed successfully")
            return results
        except json.JSONDecodeError as exc:
            print(f"  Malformed JSON from iperf3: {exc}")
            if attempt < retries:
                time.sleep(retry_delay)
                continue
            return None

    print("  iperf3 failed after all retries")
    return None


# ──────────────────────────────────────────────────────────────
#  iperf3 — Result Parsing
# ──────────────────────────────────────────────────────────────


def _server_output(results: dict) -> dict:
    """In reverse mode iperf3 nests the real data under *server_output_json*."""
    return results.get("server_output_json") or results


def parse_summary(results: dict) -> PerformanceMetrics | None:
    """Distil the final summary from an iperf3 run into a `PerformanceMetrics`."""
    if not results:
        return None
    try:
        end = _server_output(results)["end"]
        summary = end.get("sum") or end.get("sum_received", {})
        return PerformanceMetrics(
            bitrate_mbps=summary["bits_per_second"] / 1_000_000,
            jitter_ms=summary.get("jitter_ms", 0),
            lost_packets=summary.get("lost_packets", 0),
            total_packets=summary.get("packets", 0),
            lost_percent=summary.get("lost_percent", 0),
        )
    except KeyError as exc:
        print(f"  Could not parse iperf3 summary — missing key: {exc}")
        return None


def parse_intervals(results: dict) -> pd.DataFrame:
    """Unpack per-second interval data into a tidy DataFrame."""
    intervals = _server_output(results).get("intervals", [])

    rows: list[dict] = []
    for interval in intervals:
        stream = interval.get("sum", {})
        rows.append(
            {
                "time": stream.get("end", 0),
                "bitrate_mbps": stream.get("bits_per_second", 0) / 1_000_000,
                "jitter_ms": stream.get("jitter_ms", 0),
                "lost_packets": stream.get("lost_packets", 0),
                "packets": stream.get("packets", 0),
                "lost_percent": stream.get("lost_percent", 0),
            }
        )

    return pd.DataFrame(rows)


# ──────────────────────────────────────────────────────────────
#  Logging
# ──────────────────────────────────────────────────────────────


def save_json_log(results: dict, output_dir: Path) -> Path:
    """Persist the raw iperf3 JSON to *output_dir*."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"iperf3_{stamp}.json"
    path.write_text(json.dumps(results, indent=2))
    print(f"  Saved raw log  : {path}")
    return path


# ──────────────────────────────────────────────────────────────
#  Plotting — Time-Series Comparison
# ──────────────────────────────────────────────────────────────


def plot_metric_comparison(
    series: dict[str, tuple[pd.DataFrame, str]],
    metric: MetricPlot,
    timestamp: str,
    output_dir: Path,
) -> None:
    """Overlay a single metric across all profiles."""
    fig, ax = plt.subplots(figsize=(12, 6))

    for label, (dataframe, color) in series.items():
        valid = dataframe[["time", metric.column]].dropna()
        if valid.empty:
            continue
        ax.plot(
            valid["time"],
            valid[metric.column],
            marker=metric.marker,
            linewidth=2,
            color=color,
            label=label,
        )

    ax.set_title(metric.title, fontsize=14, fontweight="bold")
    ax.set_xlabel("Time (s)", fontsize=12)
    ax.set_ylabel(metric.y_label, fontsize=12)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=11)
    plt.tight_layout()

    path = output_dir / f"{metric.filename}_comparison_{timestamp}.png"
    fig.savefig(path, dpi=300, bbox_inches="tight")
    print(f"  Saved plot : {path}")
    plt.close(fig)


# ──────────────────────────────────────────────────────────────
#  Plotting — Latency Box-Plot
# ──────────────────────────────────────────────────────────────


def collect_ping_rtt(
    profile: Profile, count: int = PING_COUNT, interface: str = "uesimtun0"
) -> list[float]:
    """Ping *profile.server_ip* from inside the UE container and return RTTs in ms."""
    print(f"\n  Pinging {profile.server_ip} via {interface} ({count} packets) ...")
    stdout, _, rc = docker_exec(
        profile.ue_container,
        f"ping -i {PING_INTERVAL_SEC} -c {count} -I {interface} {profile.server_ip}",
        timeout=int(count * (PING_INTERVAL_SEC + 1)) + 30,
    )
    if rc != 0 or not stdout:
        print("  Ping test failed")
        return []

    rtts = [float(m) for m in re.findall(r"time=([\d.]+)\s*ms", stdout)]
    print(f"  Collected {len(rtts)} RTT samples")
    return rtts


def _stats_annotation(values: pd.Series) -> str:
    """Build a compact statistics block for a box-plot annotation."""
    return (
        f"mean   = {np.mean(values):.2f} ms\n"
        f"median = {np.median(values):.2f} ms\n"
        f"min    = {np.min(values):.2f} ms\n"
        f"max    = {np.max(values):.2f} ms\n"
        f"\u03c3      = {np.std(values):.2f} ms"
    )


def plot_latency_comparison(
    rtt_data: dict[str, list[float]],
    palette: dict[str, str],
    timestamp: str,
    output_dir: Path,
) -> None:
    """Draw side-by-side RTT box-plots with embedded summary statistics."""
    frames = [
        pd.DataFrame({"Protocol": label, "RTT (ms)": rtts})
        for label, rtts in rtt_data.items()
        if rtts
    ]
    if not frames:
        print("  No ping data available — skipping latency plot")
        return

    combined = pd.concat(frames, ignore_index=True)

    plt.figure(figsize=(13, 6))
    sns.set_theme(style="whitegrid", context="talk")

    ax = sns.boxplot(
        x="Protocol",
        y="RTT (ms)",
        hue="Protocol",
        data=combined,
        width=0.4,
        palette=palette,
        showfliers=True,
        legend=False,
    )

    plt.title("Network Latency \u2014 Ping RTT", fontsize=16, pad=20)
    plt.xlabel("5G User Plane Protocol", fontsize=14)
    plt.ylabel("RTT (ms)", fontsize=14)

    protocols = list(palette.keys())
    colors = list(palette.values())
    for x_pos, label in enumerate(protocols):
        subset = combined.loc[combined["Protocol"] == label, "RTT (ms)"]
        if subset.empty:
            continue
        ax.text(
            x_pos + 0.28,
            ax.get_ylim()[1],
            _stats_annotation(subset),
            va="top",
            ha="left",
            fontsize=10,
            fontfamily="monospace",
            color=colors[x_pos],
            bbox=dict(
                boxstyle="round,pad=0.4",
                facecolor="white",
                edgecolor=colors[x_pos],
                alpha=0.85,
            ),
        )

    plt.tight_layout()
    path = output_dir / f"ping_latency_comparison_{timestamp}.png"
    plt.savefig(path, dpi=300, bbox_inches="tight")
    print(f"  Saved plot : {path}")
    plt.close()


# ──────────────────────────────────────────────────────────────
#  Per-Profile Measurement Orchestration
# ──────────────────────────────────────────────────────────────


def measure_profile(
    profile: Profile, duration: int = IPERF_DURATION_SEC
) -> tuple[dict, PerformanceMetrics] | None:
    """Run iperf3 + analysis for a single *profile*.

    Returns ``(raw_results, metrics)`` on success, or *None* on failure.
    """
    if not is_container_running(profile.ue_container):
        print(f"  Container '{profile.ue_container}' is not running — skipping")
        return None

    print(f"  Container '{profile.ue_container}' is up")

    raw_results = run_iperf3_test(profile, duration=duration)
    if raw_results is None:
        print(f"  Failed to collect iperf3 data for {profile.label}")
        return None

    save_json_log(raw_results, OUTPUT_DIR)

    metrics = parse_summary(raw_results)
    if metrics is None:
        return None

    metrics.display()
    return raw_results, metrics


# ──────────────────────────────────────────────────────────────
#  CLI & Entry Point
# ──────────────────────────────────────────────────────────────

SEPARATOR = "=" * 55


def build_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="5G User-Plane Measurements — QUIC vs GTP-U vs GTP-U+IPsec",
    )
    parser.add_argument(
        "-p",
        "--profile",
        nargs="+",
        choices=list(PROFILES.keys()),
        default=list(PROFILES.keys()),
        metavar="PROFILE",
        help="Profiles to run (default: all). Choices: quic, gtpu, ipsec.",
    )
    parser.add_argument(
        "-m",
        "--metrics",
        nargs="+",
        choices=AVAILABLE_METRICS,
        default=AVAILABLE_METRICS,
        metavar="METRIC",
        help="Metrics to measure (default: all). Choices: rtt, throughput, jitter.",
    )
    parser.add_argument(
        "-pc",
        "--ping-count",
        type=int,
        default=PING_COUNT,
        metavar="N",
        help=f"Number of ping packets per profile (default: {PING_COUNT}).",
    )
    parser.add_argument(
        "--iperf-duration",
        type=int,
        default=IPERF_DURATION_SEC,
        metavar="SEC",
        help=f"Duration of each iperf3 test in seconds (default: {IPERF_DURATION_SEC}).",
    )
    return parser.parse_args()


def main() -> None:
    args = build_cli()
    active_profiles = [PROFILES[key] for key in args.profile]
    selected_metrics = set(args.metrics)

    banner = " vs ".join(p.label for p in active_profiles)
    metric_tags = ", ".join(sorted(selected_metrics))
    print(f"=== 5G Measurements \u2014 {banner} ===\n")
    print(f"  Metrics : {metric_tags}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    needs_iperf = bool(selected_metrics & {"throughput", "jitter"})
    needs_ping = "rtt" in selected_metrics

    # ── Phase 1: iperf3 throughput / jitter tests ─────────────

    time_series: dict[str, tuple[pd.DataFrame, str]] = {}

    if needs_iperf:
        for profile in active_profiles:
            print(f"\n{SEPARATOR}")
            print(f"  Profile: {profile.label}")
            print(SEPARATOR)

            result = measure_profile(profile, duration=args.iperf_duration)
            if result is None:
                continue

            raw_results, _ = result
            interval_data = parse_intervals(raw_results)
            if not interval_data.empty:
                time_series[profile.label] = (interval_data, profile.color)

            time.sleep(INTER_PROFILE_PAUSE_SEC)

    # ── Phase 2: iperf3 comparison plots ──────────────────────

    if needs_iperf and len(time_series) >= 2:
        print(f"\n{SEPARATOR}")
        print("  Generating comparison plots ...")
        print(SEPARATOR)

        active_plots = [
            plot
            for metric_key in ("throughput", "jitter")
            if metric_key in selected_metrics
            for plot in ALL_METRIC_PLOTS[metric_key]
        ]
        for metric in active_plots:
            plot_metric_comparison(time_series, metric, timestamp, OUTPUT_DIR)
    elif needs_iperf:
        print("\n  Only one profile completed — comparison plots skipped.")

    # ── Phase 3: ping latency sweep ──────────────────────────

    if needs_ping:
        print(f"\n{SEPARATOR}")
        print("  Running ping latency tests ...")
        print(SEPARATOR)

        rtt_data: dict[str, list[float]] = {}
        palette: dict[str, str] = {}

        for profile in active_profiles:
            if not is_container_running(profile.ue_container):
                print(f"  '{profile.ue_container}' not running — skipping ping")
                continue
            rtt_data[profile.label] = collect_ping_rtt(profile, count=args.ping_count)
            palette[profile.label] = profile.color

        if len(rtt_data) >= 2:
            plot_latency_comparison(rtt_data, palette, timestamp, OUTPUT_DIR)
        else:
            print("  Not enough profiles for a latency comparison.")

    print("\nAll measurements complete!")


if __name__ == "__main__":
    main()
