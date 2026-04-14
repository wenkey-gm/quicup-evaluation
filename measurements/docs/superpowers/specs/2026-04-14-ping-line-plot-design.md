# Design: Ping Latency Line Plot

## Goal
Replace the existing boxplot for ping latency with a line plot to show RTT trends over time (sequence of packets).

## Implementation Details
1.  **Data Transformation**:
    *   Modify `plot_latency_comparison` to handle time-series data.
    *   Add a `Sequence` or `Packet Index` column to the DataFrame generated from `rtt_data`.
2.  **Plotting**:
    *   Switch `sns.boxplot` to `sns.lineplot`.
    *   Use `hue="Protocol"` to distinguish between different protocol stacks.
    *   Retain the existing `palette` for color consistency.
3.  **Aesthetics**:
    *   Update labels: X-axis becomes "Packet Sequence", Y-axis remains "RTT (ms)".
    *   Add markers or adjust line thickness for clarity.
    *   Relocate statistical annotations if they clutter the line data.

## Verification
*   Run the measurement suite and verify the generated PNG file shows lines instead of boxes.
*   Ensure colors match the throughput and jitter plots.
