import subprocess
import os
import re
import time
import json
from datetime import datetime

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns


# Configuration Constants
PROFILES = {
    "QUICUP (Encrypted)": {
        "ue_container": "ueransim-ue-quic",
        "server_ip": "10.46.0.1",
        "bind_ip": "10.46.0.2",
        "color": "#2ecc71",
        "nr_binder_path": "/ueransim/build/nr-binder",
    },
    "GTP-U (Standard)": {
        "ue_container": "ueransim-ue-gtpu",
        "server_ip": "10.45.0.1",
        "bind_ip": "10.45.0.2",
        "color": "#3498db",
        "nr_binder_path": "/ueransim/build/nr-binder",
    },
}
SERVER_CONTAINER = "open5gs-run"
DURATION = 100
PING_COUNT = 30
OUTPUT_DIR = "plots"
COMMAND_TIMEOUT = 120

PLOT_CONFIGS = [
    {
        "y_col": "bitrate_mbps",
        "title": "Bitrate Over Time",
        "ylabel": "Bitrate (Mbps)",
        "filename": "bitrate",
        "color": "#2E86AB",
        "marker": "o",
    },
    {
        "y_col": "jitter_ms",
        "title": "Jitter Over Time",
        "ylabel": "Jitter (ms)",
        "filename": "jitter",
        "color": "#A23B72",
        "marker": "s",
    },
    {
        "y_col": "lost_percent",
        "title": "Packet Loss Over Time",
        "ylabel": "Packet Loss (%)",
        "filename": "packet_loss",
        "color": "#F18F01",
        "marker": "^",
    },
]


# Utility Functions
def run_command(command, shell=True, timeout=COMMAND_TIMEOUT):
    """Execute a shell command and return output"""
    try:
        result = subprocess.run(
            command, shell=shell, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return None, f"Command timed out after {timeout} seconds", -1
    except Exception as e:
        return None, str(e), -1


def check_container_running(container_name):
    """Check if a Docker container is running"""
    stdout, stderr, returncode = run_command(
        f"docker ps --filter name={container_name} --format '{{{{.Names}}}}'"
    )
    return container_name in stdout if stdout else False


def start_iperf3_server(container_name):
    """Start iperf3 server in background on specified container"""
    print(f"\nStarting iperf3 server on {container_name}...")

    run_command(f"docker exec {container_name} pkill -9 iperf3", timeout=5)
    time.sleep(1)

    cmd = f"docker exec -d {container_name} iperf3 -s"
    stdout, stderr, returncode = run_command(cmd, timeout=10)

    if returncode != 0:
        print(f"Failed to start iperf3 server: {stderr}")
        return False

    time.sleep(2)

    stdout, stderr, returncode = run_command(
        f"docker exec {container_name} pgrep -f 'iperf3 -s'", timeout=5
    )

    if stdout and stdout.strip():
        print(f"iperf3 server started successfully (PID: {stdout.strip()})")
        return True

    print("Could not verify iperf3 server is running")
    return False


def save_iperf3_log(results, output_dir):
    """Save iperf3 JSON results to file"""
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(output_dir, f"iperf3_{timestamp}.json")

    with open(log_file, "w") as f:
        json.dump(results, f, indent=2)

    print(f"Saved iperf3 log: {log_file}")
    return log_file


def run_iperf3_test(
    container_name, server_ip, bind_ip, duration=DURATION, retries=3, retry_delay=10
):
    """Run iperf3 UDP test from container with retry logic"""
    for attempt in range(1, retries + 1):
        print(
            f"\n  Running iperf3 UDP test (duration: {duration}s, attempt {attempt}/{retries})..."
        )
        print(f"     Target: {server_ip}, Bind: {bind_ip}")

        iperf_cmd = f"docker exec {container_name} iperf3 -c {server_ip} -B {bind_ip} -u -b 100M -R --get-server-output -t {duration} -J"
        timeout = duration + 60
        stdout, stderr, returncode = run_command(iperf_cmd, timeout=timeout)

        if returncode != 0:
            print(f"  iperf3 test failed (exit code: {returncode})")
            if stderr:
                print(f"     Error: {stderr}")
            if (
                "server is busy" in (stderr or "") + (stdout or "")
                and attempt < retries
            ):
                print(f"     Server busy, retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                continue
            return None

        if not stdout:
            print("iperf3 returned no output")
            return None

        try:
            results = json.loads(stdout)
            print("iperf3 test completed successfully")
            return results
        except json.JSONDecodeError as e:
            print(f"Failed to parse iperf3 output: {e}")
            if attempt < retries:
                time.sleep(retry_delay)
                continue
            return None

    print("iperf3 failed after all retries")
    return None


def _get_server_output(results):
    """Return server_output_json if available (reverse mode), else results itself."""
    server = results.get("server_output_json")
    if server:
        return server
    return results


def analyze_iperf3_results(results):
    """Extract key metrics from iperf3 results (uses server output in reverse mode)"""
    if not results:
        return None

    try:
        data = _get_server_output(results)
        end = data["end"]
        # UDP uses "sum", TCP uses "sum_received"
        end_data = end.get("sum", end.get("sum_received", {}))
        metrics = {
            "bitrate_mbps": end_data["bits_per_second"] / 1_000_000,
            "jitter_ms": end_data.get("jitter_ms", 0),
            "lost_packets": end_data.get("lost_packets", 0),
            "packets": end_data.get("packets", 0),
            "lost_percent": end_data.get("lost_percent", 0),
        }

        return metrics
    except KeyError as e:
        print(f"Error parsing iperf3 results: {e}")
        return None


def extract_interval_data(results):
    """Extract time-series data from iperf3 intervals (uses server output in reverse mode)"""
    data_source = _get_server_output(results)
    intervals = data_source.get("intervals", [])

    data = {
        "time": [],
        "bitrate_mbps": [],
        "jitter_ms": [],
        "lost_packets": [],
        "packets": [],
        "lost_percent": [],
    }

    for interval in intervals:
        stream = interval.get("sum", {})
        data["time"].append(stream.get("end", 0))
        data["bitrate_mbps"].append(stream.get("bits_per_second", 0) / 1_000_000)
        data["jitter_ms"].append(stream.get("jitter_ms", 0))
        data["lost_packets"].append(stream.get("lost_packets", 0))
        data["packets"].append(stream.get("packets", 0))
        data["lost_percent"].append(stream.get("lost_percent", 0))

    df = pd.DataFrame(data)
    return df


def print_metrics(metrics):
    """Print measurement metrics to console"""
    print("\n  Network Performance Metrics:")
    print(f"     Bitrate: {metrics['bitrate_mbps']:.2f} Mbps")
    print(f"     Jitter: {metrics['jitter_ms']:.3f} ms")
    print(
        f"     Packet Loss: {metrics['lost_percent']:.2f}% "
        f"({metrics['lost_packets']}/{metrics['packets']})"
    )


def create_comparison_plot(all_dfs, timestamp, output_dir, config):
    """Create a comparison plot overlaying both profiles."""
    x_col = "time"
    y_col = config["y_col"]

    fig, ax = plt.subplots(figsize=(12, 6))

    for label, (df, color) in all_dfs.items():
        plot_df = df[[x_col, y_col]].dropna()
        if plot_df.empty:
            continue
        ax.plot(
            plot_df[x_col],
            plot_df[y_col],
            marker=config["marker"],
            linewidth=2,
            color=color,
            label=label,
        )

    ax.set_title(config["title"], fontsize=14, fontweight="bold")
    ax.set_xlabel("Time (seconds)", fontsize=12)
    ax.set_ylabel(config["ylabel"], fontsize=12)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=11)

    plt.tight_layout()
    plot_file = os.path.join(
        output_dir, f"{config['filename']}_comparison_{timestamp}.png"
    )
    plt.savefig(plot_file, dpi=300, bbox_inches="tight")
    print(f"  Saved comparison plot: {plot_file}")
    plt.close()


def collect_ping_rtt(container, target, interface="uesimtun0", count=PING_COUNT):
    """Run ping inside a Docker container and return per-packet RTT values in ms."""
    cmd = f"docker exec {container} ping -i 0.2 -c {count} -I {interface} {target}"
    print(f"\n  Running ping test (count: {count})...")
    print(f"     Target: {target}, Interface: {interface}")
    stdout, stderr, returncode = run_command(cmd, timeout=count * 2 + 30)
    if returncode != 0 or not stdout:
        print(f"  Ping test failed")
        return []
    rtts = [float(m) for m in re.findall(r"time=([\d.]+)\s*ms", stdout)]
    print(f"  Collected {len(rtts)} RTT samples")
    return rtts


def create_latency_boxplot(all_rtts, timestamp, output_dir):
    """Create a comparison boxplot of ping RTT for all profiles."""
    frames = []
    for label, rtts in all_rtts.items():
        if rtts:
            frames.append(pd.DataFrame({"Protocol": label, "RTT (ms)": rtts}))

    if not frames:
        print("  No ping data to plot for boxplot")
        return

    df_all = pd.concat(frames, ignore_index=True)

    palette = {label: PROFILES[label]["color"] for label in all_rtts}

    plt.figure(figsize=(13, 6))
    sns.set_theme(style="whitegrid", context="talk")

    ax = sns.boxplot(
        x="Protocol",
        y="RTT (ms)",
        hue="Protocol",
        data=df_all,
        width=0.4,
        palette=palette,
        showfliers=True,
        legend=False,
    )

    plt.title("Network Latency — Ping RTT", fontsize=16, pad=20)
    plt.xlabel("5G User Plane Protocol", fontsize=14)
    plt.ylabel("RTT (Milliseconds)", fontsize=14)

    col = "RTT (ms)"
    protocols = list(palette.keys())
    for x_pos, label in enumerate(protocols):
        subset = df_all.loc[df_all["Protocol"] == label, col]
        if subset.empty:
            continue
        stats_text = (
            f"mean   = {np.mean(subset):.2f} ms\n"
            f"median = {np.median(subset):.2f} ms\n"
            f"min    = {np.min(subset):.2f} ms\n"
            f"max    = {np.max(subset):.2f} ms\n"
            f"σ      = {np.std(subset):.2f} ms"
        )
        ax.text(
            x_pos + 0.28,
            ax.get_ylim()[1],
            stats_text,
            va="top",
            ha="left",
            fontsize=10,
            fontfamily="monospace",
            color=list(palette.values())[x_pos],
            bbox=dict(
                boxstyle="round,pad=0.4",
                facecolor="white",
                edgecolor=list(palette.values())[x_pos],
                alpha=0.85,
            ),
        )

    plt.tight_layout()
    plot_file = os.path.join(output_dir, f"ping_latency_comparison_{timestamp}.png")
    plt.savefig(plot_file, dpi=300, bbox_inches="tight")
    print(f"  Saved comparison plot: {plot_file}")
    plt.close()


def run_profile(label, cfg):
    """Run measurements for a single profile, return (iperf_results, metrics)."""
    ue = cfg["ue_container"]

    if not check_container_running(ue):
        print(f"  Container '{ue}' is not running — skipping {label}")
        return None

    print(f"  Container '{ue}' is running")

    iperf_results = run_iperf3_test(ue, cfg["server_ip"], cfg["bind_ip"], DURATION)
    if not iperf_results:
        print(f"  Failed to get iperf3 results for {label}")
        return None

    save_iperf3_log(iperf_results, OUTPUT_DIR)

    metrics = analyze_iperf3_results(iperf_results)
    if not metrics:
        return None

    print_metrics(metrics)
    return iperf_results, metrics


def main():
    print("=== 5G iperf3 Measurements — QUIC vs GTP-U ===\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    all_dfs = {}  # label -> (df, color)

    for label, cfg in PROFILES.items():
        print(f"\n{'='*50}")
        print(f"  Profile: {label}")
        print(f"{'='*50}")

        result = run_profile(label, cfg)
        if result is None:
            continue

        # Pause between profiles to avoid iperf3 "server busy"
        time.sleep(5)

        iperf_results, metrics = result
        df = extract_interval_data(iperf_results)
        if not df.empty:
            all_dfs[label] = (df, cfg["color"])

            # Save per-profile CSV
            csv_file = os.path.join(
                OUTPUT_DIR, f"iperf3_data_{label.split()[0].lower()}_{timestamp}.csv"
            )
            df.to_csv(csv_file, index=False)
            print(f"  Saved data: {csv_file}")

    # --- Comparison plots ---
    if len(all_dfs) >= 2:
        print(f"\n{'='*50}")
        print("  Generating comparison plots...")
        print(f"{'='*50}")

        for config in PLOT_CONFIGS:
            create_comparison_plot(all_dfs, timestamp, OUTPUT_DIR, config)
    else:
        print("\nOnly one profile completed — skipping comparison plots.")

    # --- Ping latency boxplot ---
    print(f"\n{'='*50}")
    print("  Running ping latency tests...")
    print(f"{'='*50}")

    all_rtts = {}
    for label, cfg in PROFILES.items():
        ue = cfg["ue_container"]
        if not check_container_running(ue):
            print(f"  Container '{ue}' is not running — skipping ping for {label}")
            continue
        rtts = collect_ping_rtt(ue, cfg["server_ip"], count=PING_COUNT)
        all_rtts[label] = rtts

    if len(all_rtts) >= 2:
        create_latency_boxplot(all_rtts, timestamp, OUTPUT_DIR)
    else:
        print("  Not enough profiles for latency boxplot.")

    print("\nMeasurements complete!")


if __name__ == "__main__":
    main()
