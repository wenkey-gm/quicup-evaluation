import json

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns


def extract_rtt_from_iperf(filename, protocol_name):
    rtt_values = []
    try:
        with open(filename, "r") as f:
            data = json.load(f)

            # iperf3 stores time intervals in a list. We loop through them to get the RTT.
            for interval in data.get("intervals", []):
                for stream in interval.get("streams", []):
                    # iperf3 outputs RTT in microseconds. We convert it to milliseconds.
                    if "rtt" in stream:
                        rtt_us = stream["rtt"]  # already in microseconds
                        rtt_values.append(rtt_us)

    except FileNotFoundError:
        print(
            f"Error: {filename} not found. "
            "Please ensure the iperf3 JSON file is in the same directory."
        )
        return pd.DataFrame()
    except json.JSONDecodeError:
        print(
            f"Error: {filename} is not a valid JSON. "
            "Check if your iperf3 command used the -J flag correctly."
        )
        return pd.DataFrame()

    return pd.DataFrame(
        {"Protocol": protocol_name, "Latency under Load (µs)": rtt_values}
    )


# 1. Parse the iperf3 JSON files
df_gtpu = extract_rtt_from_iperf("gtpu_iperf.json", "GTP-U (Standard)")
df_quicup = extract_rtt_from_iperf("quic_iperf.json", "QUICUP (Encrypted)")

# 2. Combine into a single DataFrame
df_all = pd.concat([df_gtpu, df_quicup], ignore_index=True)

if not df_all.empty:
    # 3. Set up the visual style
    plt.figure(figsize=(13, 6))
    sns.set_theme(style="whitegrid", context="talk")

    # 4. Create the Boxplot
    palette = {
        "GTP-U (Standard)": "#3498db",
        "QUICUP (Encrypted)": "#2ecc71",
    }
    ax = sns.boxplot(
        x="Protocol",
        y="Latency under Load (µs)",
        hue="Protocol",
        data=df_all,
        width=0.4,
        palette=palette,
        showfliers=True,
        legend=False,
    )

    # Add a swarmplot to show the actual distribution of data points
    sns.swarmplot(
        x="Protocol",
        y="Latency under Load (µs)",
        hue="Protocol",
        data=df_all,
        color=".25",
        alpha=0.6,
        size=6,
        legend=False,
    )

    # 5. Add titles and labels
    plt.title("Network Latency Under Heavy Load (iperf3 RTT)", fontsize=16, pad=20)
    plt.xlabel("5G User Plane Protocol", fontsize=14)
    plt.ylabel("RTT Latency (Microseconds)", fontsize=14)

    # 6. Annotate stats beside each box
    col = "Latency under Load (µs)"
    protocols = list(palette.keys())
    for x_pos, label in enumerate(protocols):
        subset = df_all.loc[df_all["Protocol"] == label, col]
        if subset.empty:
            continue
        stats_text = (
            f"mean   = {np.mean(subset):.1f} µs\n"
            f"median = {np.median(subset):.1f} µs\n"
            f"min    = {np.min(subset):.1f} µs\n"
            f"max    = {np.max(subset):.1f} µs\n"
            f"σ      = {np.std(subset):.1f} µs"
        )
        # Place text to the right of each box
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

    # 7. Save and show
    plt.tight_layout()
    out = "plots/iperf3_latency_boxplot.png"
    plt.savefig(out, dpi=300)
    print(f"Success! Plot saved as '{out}'")
    plt.show()
else:
    print("No data to plot. Please fix the JSON files and try again.")
