from __future__ import annotations

import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns


def select_rx_subset(df: pd.DataFrame) -> pd.DataFrame:
    if "direction" not in df.columns:
        return df
    rx_subset = df[df["direction"].str.contains("RX", na=False)]
    return rx_subset


def _stats_annotation(values: pd.Series) -> str:
    return (
        f"mean   = {np.mean(values):.2f} ms\n"
        f"median = {np.median(values):.2f} ms\n"
        f"min    = {np.min(values):.2f} ms\n"
        f"max    = {np.max(values):.2f} ms\n"
        f"\u03c3      = {np.std(values):.2f} ms"
    )


def plot_metric_comparisons(
    series: dict[str, tuple[pd.DataFrame, str]],
    timestamp: str,
    output_dir: Path,
    direction_label: str,
) -> None:
    sns.set_theme(style="whitegrid", context="talk")

    metrics_to_plot = [
        (
            "bitrate_mbps",
            "Throughput Comparison",
            "Throughput (Mbps)",
            "throughput_comparison",
        ),
        ("jitter_ms", "Jitter Comparison", "Jitter (ms)", "jitter_comparison"),
        (
            "lost_percent",
            "Packet Loss Comparison",
            "Packet Loss (%)",
            "loss_comparison",
        ),
    ]

    for column, title, y_label, filename in metrics_to_plot:
        fig, ax = plt.subplots(figsize=(12, 5), dpi=300)
        plotted = False
        max_time = 0.0

        for label, (df, color) in series.items():
            if df.empty or column not in df.columns or "time" not in df.columns:
                continue

            subset = select_rx_subset(df)

            if subset.empty:
                continue

            ax.plot(
                subset["time"],
                subset[column],
                label=label,
                color=color,
                linewidth=2.4,
                alpha=0.9,
            )
            plotted = True
            max_time = max(max_time, float(subset["time"].max()))

        if not plotted:
            plt.close(fig)
            continue

        ax.set_title(
            f"{title} ({direction_label})", fontsize=16, fontweight="bold", pad=14
        )
        ax.set_xlabel("Time (s)", fontsize=13, fontweight="bold")
        ax.set_ylabel(y_label, fontsize=13, fontweight="bold")
        ax.grid(True, which="major", linestyle="--", linewidth=0.5, alpha=0.8)

        current_ylim = ax.get_ylim()
        ax.set_ylim(bottom=0, top=max(current_ylim[1] * 1.15, 0.5))
        ax.set_xlim(left=0, right=max_time)
        ax.legend(
            loc="upper right",
            frameon=True,
            fontsize=10,
            facecolor="white",
            framealpha=0.9,
        )

        plt.tight_layout()
        out_path = output_dir / f"{filename}_{timestamp}.png"
        fig.savefig(out_path, bbox_inches="tight")
        plt.close(fig)
        print(f"  Saved metric plot: {out_path}")


def plot_latency_comparison(
    rtt_data: dict[str, list[float]],
    palette: dict[str, str],
    timestamp: str,
    output_dir: Path,
) -> None:
    frames = []
    for label, rtts in rtt_data.items():
        if not rtts:
            continue
        df = pd.DataFrame({
            "Protocol": label,
            "RTT (ms)": rtts,
            "Sequence": range(len(rtts))
        })
        frames.append(df)

    if not frames:
        print("No ping data available — skipping latency plot")
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
    print(f"Saved plot : {path}")
    plt.close()
