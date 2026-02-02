import subprocess
import json
import re
import os
import pandas as pd
import matplotlib.pyplot as plt

TARGET_IP = "8.8.8.8"
BIND_IP = "10.45.0.x"


def run_cmd(cmd, timeout):
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=True
    )
    if not proc.stdout:
        raise ValueError(f"{cmd[2]} returned no output")
    return proc.stdout


def get_metrics():
    binder = ["../UERANSIM/build/nr-binder", BIND_IP]

    try:
        iperf_out = run_cmd(
            binder
            + [
                "iperf3",
                "-c",
                TARGET_IP,
                "-u",
                "-b",
                "10M",
                "-l",
                "1200",
                "-t",
                "10",
                "-J",
            ],
            30,
        )
        ping_out = run_cmd(binder + ["ping", "-c", "10", TARGET_IP], 20)

        iperf_data = json.loads(iperf_out)
        rtts = [float(r) for r in re.findall(r"time=([\d.]+) ms", ping_out)]

        sum_udp = iperf_data.get("end", {}).get("sum") or iperf_data.get("end", {}).get(
            "sum_receiver"
        )
        if not sum_udp:
            raise ValueError("Missing iperf3 sum data")

        return {
            "Bitrate (Mbps)": sum_udp["bits_per_second"] / 1e6,
            "Jitter (ms)": sum_udp["jitter_ms"],
            "Avg RTT (ms)": sum(rtts) / len(rtts) if rtts else 0.0,
        }
    except Exception as e:
        print(f"Error: {e}")
        return None


def plot_bar(df, metrics, title, ylabel, filename, figsize=(8, 6)):
    fig, ax = plt.subplots(figsize=figsize)
    df[df["Metric"].isin(metrics)].plot(
        x="Metric", y="Value", kind="bar", ax=ax, legend=False
    )
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.set_ylabel(ylabel, fontsize=12)
    ax.set_xlabel("")
    ax.tick_params(axis="x", rotation=0)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()

    os.makedirs("plots", exist_ok=True)
    plt.savefig(f"plots/{filename}", dpi=300, bbox_inches="tight")
    print(f"Plot saved: plots/{filename}")


def main():
    print(f"Measuring: {TARGET_IP} via {BIND_IP}\n")

    metrics = get_metrics()
    if not metrics:
        print("❌ Failed. Check: iperf3 installed, server running, IPs reachable")
        return

    df = pd.DataFrame(list(metrics.items()), columns=["Metric", "Value"])
    print("\n--- RESULTS ---")
    print(df.to_string(index=False))

    os.makedirs("plots", exist_ok=True)
    df.to_csv("plots/results.csv", index=False)
    print(f"Table saved: plots/results.csv")

    plot_bar(
        df,
        ["Bitrate (Mbps)", "Jitter (ms)", "Avg RTT (ms)"],
        "Network Performance Metrics",
        "Value",
        "network_performance.png",
        (10, 6),
    )
    plt.show()


if __name__ == "__main__":
    main()
