from __future__ import annotations

import argparse
import json
import os
import re
import math
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
IPERF_BANDWIDTH = "10M"
IPERF_MAX_RETRIES = 3
IPERF_RETRY_DELAY_SEC = 10

PING_COUNT = 30
PING_INTERVAL_SEC = 1

OUTPUT_DIR = Path("plots")
COMMAND_TIMEOUT_SEC = 120

INTER_PROFILE_PAUSE_SEC = 5


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


def run_iperf3_test(
    profile: Profile,
    duration: int = IPERF_DURATION_SEC,
    retries: int = IPERF_MAX_RETRIES,
    retry_delay: int = IPERF_RETRY_DELAY_SEC,
) -> dict | None:
    """Execute an iperf3 UDP bidirectional test through *profile*'s UE container.

    Retries automatically when the server reports "busy".
    Returns the parsed JSON results or *None* on failure.
    """
    container = profile.ue_container
    iperf_flags = (
        f"-c {profile.server_ip} -B {profile.bind_ip} -p 5201 -i 0.5 -l 1300 "
        f"-u -b {IPERF_BANDWIDTH} --json-stream -t {duration} --get-server-output"
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
                print(f"    STDERR: {stderr.strip()}")
            if stdout:
                print(f"    STDOUT: {stdout.strip()}")

            # Some iperf3 builds emit usable JSON stream data before non-zero exit.
            if stdout:
                try:
                    parsed = parse_iperf3_output(stdout)
                    if parsed.get("intervals"):
                        print(
                            "    Parsed interval data from non-zero iperf3 run; continuing"
                        )
                        return parsed
                except Exception:
                    pass

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
            results = parse_iperf3_output(stdout)

            print("  iperf3 test completed successfully")
            return results

        except Exception as exc:
            print(f"  Failed to parse iperf3 stream: {exc}")
            return None
    return None


def parse_iperf3_output(stdout: str) -> dict:
    """Parse iperf3 output supporting both `-J` and `--json-stream` formats."""
    payload = stdout.strip()
    if not payload:
        raise ValueError("iperf3 produced empty output")

    # Standard iperf3 `-J` output: a single JSON object with start/intervals/end.
    try:
        top_level = json.loads(payload)
        if isinstance(top_level, dict) and (
            "start" in top_level or "intervals" in top_level or "end" in top_level
        ):
            return top_level
    except json.JSONDecodeError:
        pass

    # iperf3 `--json-stream` output: newline-delimited event payloads.
    results: dict = {"intervals": []}
    for line in payload.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            # Skip unrelated/corrupted lines and keep parsing remaining events.
            continue

        event = obj.get("event")
        data = obj.get("data")

        if event == "start":
            results["start"] = data
        elif event == "interval":
            results["intervals"].append(data)
        elif event == "end":
            results["end"] = data
        elif event == "server_output_json":
            results["server_output_json"] = data
        elif "server_output_json" in obj:
            # Handle top-level wrapper objects that include server_output_json
            sobj = obj.get("server_output_json")
            if isinstance(sobj, dict):
                if "start" in sobj:
                    results["start"] = sobj.get("start")
                if "intervals" in sobj:
                    results["intervals"].extend(sobj.get("intervals") or [])
                if "end" in sobj:
                    results["end"] = sobj.get("end")
                results["server_output_json"] = sobj
            else:
                results["server_output_json"] = sobj
        elif event == "error":
            print(f"    iperf3 stream error: {data}")
        elif isinstance(obj, dict) and (
            "start" in obj or "intervals" in obj or "end" in obj
        ):
            # Merge defensive fallback for mixed/non-event JSON lines instead
            # of returning immediately so we keep parsing remaining stream
            if "start" in obj:
                results["start"] = obj.get("start")
            if "intervals" in obj:
                # append any intervals found in the object
                ivs = obj.get("intervals") or []
                # ensure we always extend with a list of interval dicts
                results["intervals"].extend(ivs)
            if "end" in obj:
                results["end"] = obj.get("end")
            if "server_output_json" in obj:
                results["server_output_json"] = obj.get("server_output_json")

    if (
        not results.get("start")
        and not results.get("intervals")
        and not results.get("end")
    ):
        raise ValueError("No valid iperf3 JSON objects found")
    return results


def _server_output(results: dict) -> dict:
    """In reverse mode iperf3 nests the real data under *server_output_json*."""
    return results.get("server_output_json") or results


def parse_summary(results: dict) -> PerformanceMetrics | None:
    """Distil the final summary from an iperf3 run into a `PerformanceMetrics`."""
    if not results:
        return None
    try:
        end = _server_output(results)["end"]
        # Prefer a received/remote-facing summary when available for downstream metrics.
        summary = (
            end.get("sum_received_bidir_reverse")
            or end.get("sum_bidir_reverse")
            or end.get("sum_received")
            or end.get("sum")
            or {}
        )
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
    """Unpack per-second interval data into a tidy DataFrame, separating bidir streams."""
    intervals = results.get("intervals", [])
    if not intervals:
        intervals = _server_output(results).get("intervals", [])

    rows: list[dict] = []
    for interval in intervals:
        streams = interval.get("streams", [])

        # Some iperf3 JSON variants expose per-interval values in "sum" only.
        if not streams and interval.get("sum"):
            streams = [interval["sum"]]

        # For bidir output the per-interval reverse-direction summary can
        # appear in "sum_bidir_reverse". Only append it when the
        # existing `streams` array does not already contain a reverse entry
        # to avoid producing duplicate RX rows.
        if interval.get("sum_bidir_reverse"):
            has_reverse = any(
                (s.get("sender") is False) or (s.get("sender") == False)
                for s in streams
            )
            if not has_reverse:
                reverse = (
                    dict(interval["sum_bidir_reverse"])
                    if isinstance(interval["sum_bidir_reverse"], dict)
                    else {"sum": interval["sum_bidir_reverse"]}
                )
                reverse["sender"] = False
                streams.append(reverse)

        for stream in streams:
            is_sender = stream.get("sender", True)
            direction = "TX (Uplink)" if is_sender else "RX (Downlink)"

            lost_packets = stream.get("lost_packets", 0)
            packets = stream.get("packets", 0)
            lost_percent = (lost_packets / packets) * 100.0 if packets > 0 else 0.0

            rows.append(
                {
                    "time": stream.get("end", 0),
                    "bitrate_mbps": stream.get("bits_per_second", 0) / 1_000_000,
                    "jitter_ms": stream.get("jitter_ms", 0),
                    "lost_packets": lost_packets,
                    "packets": packets,
                    "lost_percent": lost_percent,
                    "direction": direction,
                }
            )

    return pd.DataFrame(rows)


def save_json_log(results: dict, output_dir: Path) -> Path:
    """Persist the raw iperf3 JSON to *output_dir*."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"iperf3_{stamp}.jsonl"

    # If the parsed results look like a stream (have start/intervals/end),
    # write one JSON object per line as event records. Otherwise fall back
    # to writing a single pretty-printed JSON object (still with .jsonl ext).
    lines: list[str] = []
    start = results.get("start")
    intervals = results.get("intervals") or []
    end = results.get("end")
    server_out = results.get("server_output_json")

    if start or intervals or end:
        if start:
            lines.append(
                json.dumps({"event": "start", "data": start}, separators=(",", ":"))
            )
        for iv in intervals:
            lines.append(
                json.dumps({"event": "interval", "data": iv}, separators=(",", ":"))
            )
        if end:
            lines.append(
                json.dumps({"event": "end", "data": end}, separators=(",", ":"))
            )
        # include any server_output_json as its own top-level line for completeness
        if server_out and not (server_out is start or server_out is end):
            lines.append(
                json.dumps({"server_output_json": server_out}, separators=(",", ":"))
            )

        path.write_text("\n".join(lines) + "\n")
    else:
        path.write_text(json.dumps(results, indent=2))

    print(f"  Saved raw log  : {path}")
    return path


def plot_individual_summaries(
    series: dict[str, tuple[pd.DataFrame, str]],
    timestamp: str,
    output_dir: Path,
) -> None:
    if not series:
        return

    # Academic style: High-contrast white with modern tick marks
    sns.set_theme(style="ticks")

    for label, (df, color) in series.items():
        if df.empty:
            continue

        # Increase DPI for high-resolution sharp images
        fig, axes = plt.subplots(
            nrows=3, ncols=1, figsize=(11, 9), sharex=True, dpi=300
        )
        ax_bitrate, ax_jitter, ax_loss = axes

        fig.suptitle(
            f"5G User Plane Analysis: {label}", fontsize=18, fontweight="bold", y=0.98
        )

        # Handle bitrate scaling
        max_val = df["bitrate_mbps"].max()
        scale, unit = (1000.0, "Gbps") if max_val >= 1000 else (1.0, "Mbps")

        has_direction = "direction" in df.columns
        directions = df["direction"].unique() if has_direction else [None]

        for direction in directions:
            subset = df[df["direction"] == direction] if direction else df
            is_tx = direction and "TX" in direction
            line_style = (0, (5, 2)) if is_tx else "-"
            legend_label = direction if direction else "Throughput"

            # --- Panel 1: Data Rate (with Area Fill) ---
            (line,) = ax_bitrate.plot(
                subset["time"],
                subset["bitrate_mbps"] / scale,
                lw=2.5,
                ls=line_style,
                color=color,
                label=legend_label,
                zorder=3,
            )
            # Area Fill: Fills the space between 0 and the line
            ax_bitrate.fill_between(
                subset["time"],
                subset["bitrate_mbps"] / scale,
                color=color,
                alpha=0.15,
                zorder=2,
            )

            # --- Panel 2: Jitter (with Area Fill) ---
            ax_jitter.plot(
                subset["time"],
                subset["jitter_ms"],
                lw=2.5,
                ls=line_style,
                color=color,
                label=legend_label,
                alpha=0.9,
                zorder=3,
            )
            ax_jitter.fill_between(
                subset["time"], subset["jitter_ms"], color=color, alpha=0.1, zorder=2
            )

            # --- Panel 3: Packet Loss (with markers) ---
            ax_loss.plot(
                subset["time"],
                subset["lost_percent"],
                lw=2.5,
                ls=line_style,
                color=color,
                label=legend_label,
                marker="^" if not is_tx else None,
                markevery=10,
                markersize=7,
                zorder=3,
            )

        # --- REFINEMENT & GROUNDING ---
        for i, ax in enumerate(axes):
            # Clean light-gray grid
            ax.grid(
                True,
                which="major",
                linestyle="--",
                linewidth=0.5,
                color="#e0e0e0",
                alpha=0.8,
                zorder=1,
            )

            # Ground Y-axis at 0 and add 15% headroom
            current_ylim = ax.get_ylim()
            headroom = max(current_ylim[1] * 1.15, 0.5)
            ax.set_ylim(bottom=0, top=headroom)

            # Styling
            ax.tick_params(axis="both", which="major", labelsize=11)
            ax.legend(
                loc="upper left",
                frameon=True,
                fontsize=10,
                facecolor="white",
                framealpha=0.9,
            ).set_zorder(100)

        # High-impact labels
        ax_bitrate.set_ylabel(
            f"Throughput\n({unit})", fontsize=12, fontweight="bold", labelpad=10
        )
        ax_jitter.set_ylabel(
            "Jitter\n(ms)", fontsize=12, fontweight="bold", labelpad=10
        )
        ax_loss.set_ylabel(
            "Packet Loss\n(%)", fontsize=12, fontweight="bold", labelpad=10
        )
        ax_loss.set_xlabel("Time (s)", fontsize=13, fontweight="bold")

        # Formatting X-axis
        max_time = df["time"].max()
        ax_loss.set_xlim(0, max_time)
        ax_loss.set_xticks(np.arange(0, max_time + 1, 20))

        sns.despine(
            fig, offset=5, trim=False
        )  # Offset gives the labels room to breathe
        plt.tight_layout(rect=[0, 0.03, 1, 0.95])

        # Save as high-res PNG and vector PDF
        safe_name = re.sub(r"[^a-zA-Z0-9]", "_", label).lower()
        fig.savefig(output_dir / f"{safe_name}_{timestamp}.png", bbox_inches="tight")
        plt.close(fig)


def collect_ping_rtt(
    profile: Profile, count: int = PING_COUNT, interface: str = "uesimtun0"
) -> list[float]:
    """Ping *profile.server_ip* from inside the UE container and return RTTs in ms."""
    print(
        f"\n  Pinging {profile.server_ip} via {profile.ue_container} ({count} packets) ..."
    )
    stdout, _, rc = docker_exec(
        profile.ue_container,
        f"ping -i {PING_INTERVAL_SEC} -s 972 -c {count} -I {interface} {profile.server_ip}",
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

    if needs_iperf and len(time_series) >= 2:
        print(f"\n{SEPARATOR}")
        print("  Generating comparison plots ...")
        print(SEPARATOR)

        plot_individual_summaries(time_series, timestamp, OUTPUT_DIR)
    elif needs_iperf:
        print("\n  Fewer than two profiles completed — comparison plots skipped.")

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
