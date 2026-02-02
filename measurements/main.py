import subprocess
import os
import time
from pathlib import Path
import json
import matplotlib.pyplot as plt
import pandas as pd
from datetime import datetime


def run_command(command, shell=True, timeout=120):
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

    # Kill any existing iperf3 server
    run_command(f"docker exec {container_name} pkill -9 iperf3", timeout=5)
    time.sleep(1)

    # Start iperf3 server in background
    cmd = f"docker exec -d {container_name} iperf3 -s"
    stdout, stderr, returncode = run_command(cmd, timeout=10)

    if returncode != 0:
        print(f"Failed to start iperf3 server: {stderr}")
        return False

    # Give server time to start
    time.sleep(2)

    # Verify server is running
    stdout, stderr, returncode = run_command(
        f"docker exec {container_name} pgrep -f 'iperf3 -s'", timeout=5
    )

    if stdout and stdout.strip():
        print(f"iperf3 server started successfully (PID: {stdout.strip()})")
        return True
    else:
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


def run_iperf3_test(container_name, server_ip, bind_ip, duration=10):
    """Run iperf3 test from container"""
    print(f"\n Running iperf3 test (duration: {duration}s)...")
    print(f"   Target: {server_ip}, Bind: {bind_ip}")

    # Start iperf3 client with nr-binder
    iperf_cmd = f"docker exec {container_name} /ueransim/build/nr-binder {bind_ip} iperf3 -c {server_ip} -t {duration} -J"

    # Set timeout to duration + 60 seconds buffer
    timeout = duration + 60
    stdout, stderr, returncode = run_command(iperf_cmd, timeout=timeout)

    if returncode != 0:
        print(f"iperf3 test failed (exit code: {returncode})")
        if stderr:
            print(f"   Error: {stderr}")
        return None

    if not stdout:
        print(f"iperf3 returned no output")
        return None

    try:
        results = json.loads(stdout)
        print("iperf3 test completed successfully")
        return results
    except json.JSONDecodeError as e:
        print(f"Failed to parse iperf3 output: {e}")
        print(f"   Output preview: {stdout[:200]}...")
        return None


def run_ping_test(container_name, server_ip, bind_ip, count=10, duration=10):
    """Run ping test from container and collect RTT data"""
    print(f"\nRunning ping test (count: {count})...")
    print(f"   Target: {server_ip}, Bind: {bind_ip}")

    # Run ping with nr-binder
    ping_cmd = f"docker exec {container_name} /ueransim/build/nr-binder {bind_ip} ping -c {count} -i {duration/count:.1f} {server_ip}"

    timeout = duration + 30
    stdout, stderr, returncode = run_command(ping_cmd, timeout=timeout)

    if returncode != 0:
        print(f"Ping test failed (exit code: {returncode})")
        if stderr:
            print(f"   Error: {stderr}")
        return None

    if not stdout:
        print(f"Ping returned no output")
        return None

    # Parse RTT values from ping output
    import re

    rtt_values = []
    for line in stdout.split("\n"):
        match = re.search(r"time=(\d+\.\d+)\s*ms", line)
        if match:
            rtt_values.append(float(match.group(1)))

    if rtt_values:
        print(f"Ping test completed successfully ({len(rtt_values)} responses)")
        return rtt_values
    else:
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

    # Add RTT data if available
    if rtt_data:
        # Create time points for RTT data evenly distributed
        if not df.empty:
            max_time = df["time"].max()
            rtt_times = [
                (
                    max_time * i / (len(rtt_data) - 1)
                    if len(rtt_data) > 1
                    else max_time / 2
                )
                for i in range(len(rtt_data))
            ]
            rtt_df = pd.DataFrame({"time": rtt_times, "rtt_ms": rtt_data})
            df = (
                df.merge(rtt_df, on="time", how="outer")
                .sort_values("time")
                .reset_index(drop=True)
            )

    return df


def create_plot(
    df,
    timestamp,
    output_dir,
    x_col,
    y_col,
    title,
    ylabel,
    filename,
    color,
    marker,
    fill=True,
):
    """Create and save a single plot"""
    fig, ax = plt.subplots(figsize=(10, 6))

    # Filter out NaN values for plotting
    plot_df = df[[x_col, y_col]].dropna()

    if plot_df.empty:
        print(f"Warning: No data to plot for {filename}")
        plt.close()
        return

    ax.plot(plot_df[x_col], plot_df[y_col], marker=marker, linewidth=2, color=color)
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.set_xlabel("Time (seconds)", fontsize=12)
    ax.set_ylabel(ylabel, fontsize=12)
    ax.grid(True, alpha=0.3)
    if fill:
        ax.fill_between(plot_df[x_col], plot_df[y_col], alpha=0.3, color=color)
    plt.tight_layout()
    plot_file = os.path.join(output_dir, f"{filename}_{timestamp}.png")
    plt.savefig(plot_file, dpi=300, bbox_inches="tight")
    print(f"Saved plot: {plot_file}")
    plt.close()


def plot_iperf3_results(results, metrics_summary, output_dir, rtt_data=None):
    """Create plots for iperf3 results"""
    os.makedirs(output_dir, exist_ok=True)

    # Extract interval data
    df = extract_interval_data(results, rtt_data)

    if df.empty:
        print("No interval data to plot")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Define plot configurations
    plots_config = [
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

    # Add RTT plot if data is available
    if rtt_data and "rtt_ms" in df.columns:
        plots_config.append(
            {
                "y_col": "rtt_ms",
                "title": "Round Trip Time (RTT) Over Time",
                "ylabel": "RTT (ms)",
                "filename": "rtt",
                "color": "#06A77D",
                "marker": "o",  # Use circle markers for RTT plot
                "fill": False,  # No fill for RTT plot
            }
        )

    # Create all plots
    for config in plots_config:
        create_plot(
            df,
            timestamp,
            output_dir,
            "time",
            config["y_col"],
            config["title"],
            config["ylabel"],
            config["filename"],
            config["color"],
            config["marker"],
            config.get("fill", True),  # Default to True for backward compatibility
        )

    # Save data as CSV
    csv_file = os.path.join(output_dir, f"iperf3_data_{timestamp}.csv")
    df.to_csv(csv_file, index=False)
    print(f"Saved data: {csv_file}")


def main():
    print("=== 5G iperf3 Measurements ===\n")

    # Configuration
    UE_CONTAINER = "ueransim-ue"
    SERVER_CONTAINER = "open5gs-run"
    SERVER_IP = "10.10.0.10"  # open5gs-run container IP
    BIND_IP = "10.45.0.2"  # UE subnet address
    DURATION = 10  # iperf3 test duration in seconds
    OUTPUT_DIR = "plots"

    # Check if containers are running
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

    # Start iperf3 server on open5gs-run
    if not start_iperf3_server(SERVER_CONTAINER):
        print("\n Warning: iperf3 server may not be running properly")
        print("   Continuing anyway...")

    # Run iperf3 test
    iperf_results = run_iperf3_test(UE_CONTAINER, SERVER_IP, BIND_IP, DURATION)

    if not iperf_results:
        print("Failed to get iperf3 results")
        return

    # Run ping test for RTT measurement (to external server)
    rtt_data = run_ping_test(
        UE_CONTAINER, "8.8.8.8", BIND_IP, count=10, duration=DURATION
    )

    # Save iperf3 log
    save_iperf3_log(iperf_results, OUTPUT_DIR)

    # Analyze results
    metrics = analyze_iperf3_results(iperf_results, rtt_data)
    if metrics:
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

        # Create plots
        print("\nGenerating plots...")
        plot_iperf3_results(iperf_results, metrics, OUTPUT_DIR, rtt_data)

    print("\nMeasurements complete!")


if __name__ == "__main__":
    main()
