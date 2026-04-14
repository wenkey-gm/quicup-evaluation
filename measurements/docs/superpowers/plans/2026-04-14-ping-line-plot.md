# Ping Latency Line Plot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the boxplot in `utils/plotting.py` with a multi-line plot to visualize RTT trends across packet sequences.

**Architecture:** We will modify `plot_latency_comparison` to restructure the `rtt_data` into a long-form DataFrame with a `Sequence` column and use `sns.lineplot` for visualization.

**Tech Stack:** Python, Pandas, Seaborn, Matplotlib.

---

### Task 1: Refactor Data Preparation in `plot_latency_comparison`

**Files:**
- Modify: `utils/plotting.py`

- [ ] **Step 1: Update the DataFrame creation logic**
Replace the existing list comprehension with a loop that adds a `Sequence` column for each RTT sample.

```python
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
```

- [ ] **Step 2: Commit changes**

```bash
git add utils/plotting.py
git commit -m "refactor: prepare long-form data with sequence for ping plotting"
```

### Task 2: Replace Boxplot with Line Plot

**Files:**
- Modify: `utils/plotting.py`

- [ ] **Step 1: Switch Seaborn plotting function**
Replace `sns.boxplot` with `sns.lineplot` using the `Sequence` column for the X-axis.

```python
    plt.figure(figsize=(13, 6))
    sns.set_theme(style="whitegrid", context="talk")

    ax = sns.lineplot(
        x="Sequence",
        y="RTT (ms)",
        hue="Protocol",
        data=combined,
        palette=palette,
        linewidth=2.5,
        alpha=0.85
    )

    plt.title("Network Latency Trend \u2014 Ping RTT", fontsize=16, pad=20)
    plt.xlabel("Packet Sequence", fontsize=14)
    plt.ylabel("RTT (ms)", fontsize=14)
```

- [ ] **Step 2: Update axis limits and grid**
Ensure the plot looks clean with appropriate padding and grid lines.

```python
    ax.grid(True, which="major", linestyle="--", linewidth=0.5, alpha=0.7)
    ax.set_ylim(bottom=0)
```

- [ ] **Step 3: Commit changes**

```bash
git add utils/plotting.py
git commit -m "feat: replace ping boxplot with lineplot"
```

### Task 3: Relocate Statistical Annotations

**Files:**
- Modify: `utils/plotting.py`

- [ ] **Step 1: Reposition stats annotations**
Since the X-axis is no longer categorical, we'll move the stats boxes to a fixed position on the right side of the plot area.

```python
    protocols = list(palette.keys())
    colors = list(palette.values())
    
    # Calculate an offset for stacking multiple annotation boxes
    y_offset = 0.95
    for i, label in enumerate(protocols):
        subset = combined.loc[combined["Protocol"] == label, "RTT (ms)"]
        if subset.empty:
            continue
            
        ax.text(
            1.02, 
            y_offset - (i * 0.15),
            f"{label}:\n{_stats_annotation(subset)}",
            transform=ax.transAxes,
            va="top",
            ha="left",
            fontsize=9,
            fontfamily="monospace",
            color=colors[i],
            bbox=dict(
                boxstyle="round,pad=0.3",
                facecolor="white",
                edgecolor=colors[i],
                alpha=0.8
            )
        )
```

- [ ] **Step 2: Adjust plot layout for annotations**
Ensure `plt.tight_layout()` or `plt.subplots_adjust()` accounts for the new text box positions.

```python
    plt.subplots_adjust(right=0.82)
```

- [ ] **Step 3: Commit changes**

```bash
git add utils/plotting.py
git commit -m "style: reposition ping stats annotations for line plot"
```

---

### Task 4: Verification

- [ ] **Step 1: Run a test measurement**
If possible, run a mock ping or a short measurement to verify the PNG output.

```bash
python main.py -m ping --ping-count 20
```

- [ ] **Step 2: Inspect generated plot**
Verify `ping_latency_comparison_<timestamp>.png` shows lines with clear stats on the side.
