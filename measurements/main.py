import subprocess
import os
import time
import re
import json
from datetime import datetime

import matplotlib.pyplot as plt
import pandas as pd


# Configuration Constants
UE_CONTAINER = "ueransim-ue"
SERVER_CONTAINER = "open5gs-run"
SERVER_IP = "10.10.0.10"
EXTERNAL_SERVER_IP = "8.8.8.8"
BIND_IP = "10.45.0.2"
DURATION = 10
PING_COUNT = 10
OUTPUT_DIR = "plots"
COMMAND_TIMEOUT = 120
NR_BINDER_PATH = "/ueransim/build/nr-binder"

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

RTT_PLOT_CONFIG = {
    "y_col": "rtt_ms",
    "title": "Round Trip Time (RTT) Over Time",
    "ylabel": "RTT (ms)",
    "filename": "rtt",
    "color": "#06A77D",
    "marker": "o",
    "fill": False,
}


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


def run_iperf3_test(container_name, server_ip, bind_ip, duration=DURATION):
    """Run iperf3 test from container"""
    print(f"\nRunning iperf3 test (duration: {duration}s)...")
    print(f"   Target: {server_ip}, Bind: {bind_ip}")

    iperf_cmd = f"docker exec {container_name} {NR_BINDER_PATH} {bind_ip} iperf3 -c {server_ip} -t {duration} -J"
    timeout = duration + 60
    stdout, stderr, returncode = run_command(iperf_cmd, timeout=timeout)

    if returncode != 0:
        print(f"iperf3 test failed (exit code: {returncode})")
        if stderr:
            print(f"   Error: {stderr}")
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
        return None


def run_ping_test(
    container_name, server_ip, bind_ip, count=PING_COUNT, duration=DURATION
):
    """Run ping test from container and collect RTT data"""
    print(f"\nRunning ping test (count: {count})...")
    print(f"   Target: {server_ip}, Bind: {bind_ip}")

    interval = duration / count
    ping_cmd = f"docker exec {container_name} {NR_BINDER_PATH} {bind_ip} ping -c {count} -i {interval:.1f} {server_ip}"
    timeout = duration + 30
    stdout, stderr, returncode = run_command(ping_cmd, timeout=timeout)

    if returncode != 0 or not stdout:
        print(f"Ping test failed")
        return None

    rtt_values = re.findall(r"time=(\d+\.\d+)\s*ms", stdout)
    rtt_values = [float(v) for v in rtt_values]

    if rtt_values:
        print(f"Ping test completed successfully ({len(rtt_values)} responses)")
        return rtt_values

    print("No RTT values found in ping output")
    return None


def analyze_iperf3_results(results, rtt_data=None):
    """Extract key metrics from iperf3 results"""
    if not results:
        return None

    try:
        end_data = results["end"]["sum_received"]
        metrics = {
            "bitrate_mbps": end_data["bits_per_second"] / 1_000_000,
            "jitter_ms": end_data.get("jitter_ms", 0),
            "lost_packets": end_data.get("lost_packets", 0),
            "packets": end_data.get("packets", 0),
            "lost_percent": end_data.get("lost_percent", 0),
        }

        # Add RTT metrics if available
        if rtt_data:
            metrics["avg_rtt_ms"] = sum(rtt_data) / len(rtt_data)
            metrics["min_rtt_ms"] = min(rtt_data)
            metrics["max_rtt_ms"] = max(rtt_data)

        return metrics
    except KeyError as e:
        print(f"Error parsing iperf3 results: {e}")
        return None


def extract_interval_data(results, rtt_data=None):
    """Extract time-series data from iperf3 intervals"""
    intervals = results.get("intervals", [])

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

    if rtt_data and not df.empty:
        max_time = df["time"].max()
        rtt_times = [
            max_time * i / (len(rtt_data) - 1) if len(rtt_data) > 1 else max_time / 2
            for i in range(len(rtt_data))
        ]
        rtt_df = pd.DataFrame({"time": rtt_times, "rtt_ms": rtt_data})
        df = (
            df.merge(rtt_df, on="time", how="outer")
            .sort_values("time")
            .reset_index(drop=True)
        )

    return df


def create_plot(df, timestamp, output_dir, config):
    """Create and save a single plot"""
    x_col = "time"
    y_col = config["y_col"]

    plot_df = df[[x_col, y_col]].dropna()

    if plot_df.empty:
        print(f"Warning: No data to plot for {config['filename']}")
        return

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(
        plot_df[x_col],
        plot_df[y_col],
        marker=config["marker"],
        linewidth=2,
        color=config["color"],
    )
    ax.set_title(config["title"], fontsize=14, fontweight="bold")
    ax.set_xlabel("Time (seconds)", fontsize=12)
    ax.set_ylabel(config["ylabel"], fontsize=12)
    ax.grid(True, alpha=0.3)

    if config.get("fill", True):
        ax.fill_between(
            plot_df[x_col], plot_df[y_col], alpha=0.3, color=config["color"]
        )

    plt.tight_layout()
    plot_file = os.path.join(output_dir, f"{config['filename']}_{timestamp}.png")
    plt.savefig(plot_file, dpi=300, bbox_inches="tight")
    print(f"Saved plot: {plot_file}")
    plt.close()


def plot_results(results, output_dir, rtt_data=None):
    """Create all plots for measurement results"""
    os.makedirs(output_dir, exist_ok=True)

    df = extract_interval_data(results, rtt_data)

    if df.empty:
        print("No interval data to plot")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    plots_to_create = PLOT_CONFIGS.copy()
    if rtt_data and "rtt_ms" in df.columns:
        plots_to_create.append(RTT_PLOT_CONFIG)

    for config in plots_to_create:
        create_plot(df, timestamp, output_dir, config)

    csv_file = os.path.join(output_dir, f"iperf3_data_{timestamp}.csv")
    df.to_csv(csv_file, index=False)
    print(f"Saved data: {csv_file}")


def print_metrics(metrics, rtt_data):
    """Print measurement metrics to console"""
    print("\nNetwork Performance Metrics:")
    print(f"   Bitrate: {metrics['bitrate_mbps']:.2f} Mbps")
    print(f"   Jitter: {metrics['jitter_ms']:.3f} ms")
    print(
        f"   Packet Loss: {metrics['lost_percent']:.2f}% ({metrics['lost_packets']}/{metrics['packets']})"
    )

    if rtt_data:
        print(
            f"   Avg RTT: {metrics['avg_rtt_ms']:.2f} ms (min: {metrics['min_rtt_ms']:.2f}, max: {metrics['max_rtt_ms']:.2f})"
        )


def main():
    print("=== 5G iperf3 Measurements ===\n")

    if not check_container_running(UE_CONTAINER):
        print(f"Container '{UE_CONTAINER}' is not running!")
        print("  Start it with: docker-compose up -d")
        return

    if not check_container_running(SERVER_CONTAINER):
        print(f"Container '{SERVER_CONTAINER}' is not running!")
        print("  Start it with: docker-compose up -d")
        return

    print(f"Container '{UE_CONTAINER}' is running")
    print(f"Container '{SERVER_CONTAINER}' is running\n")

    if not start_iperf3_server(SERVER_CONTAINER):
        print("\nWarning: iperf3 server may not be running properly")
        print("   Continuing anyway...")

    iperf_results = run_iperf3_test(UE_CONTAINER, SERVER_IP, BIND_IP, DURATION)
    if not iperf_results:
        print("Failed to get iperf3 results")
        return

    rtt_data = run_ping_test(
        UE_CONTAINER, EXTERNAL_SERVER_IP, BIND_IP, PING_COUNT, DURATION
    )

    save_iperf3_log(iperf_results, OUTPUT_DIR)

    metrics = analyze_iperf3_results(iperf_results, rtt_data)
    if not metrics:
        return

    print_metrics(metrics, rtt_data)

    print("\nGenerating plots...")
    plot_results(iperf_results, OUTPUT_DIR, rtt_data)

    print("\nMeasurements complete!")


if __name__ == "__main__":
    main()
