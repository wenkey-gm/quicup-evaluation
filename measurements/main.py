import time
from pathlib import Path
from typing import Optional
import pandas as pd

from core.iperf import (
    run_iperf3_test,
    parse_iperf3_output,
    parse_intervals,
    parse_summary,
    save_json_log,
)
from utils.docker_helpers import is_container_running
from core.ping import collect_ping_rtt, save_ping_log
from utils.plotting import (
    plot_metric_comparisons,
    plot_latency_comparison,
)
from entities.entities import Profile, timestamp_now
from profiles.profiles import PROFILES
from utils.arg_parser import arg_parser
from utils.constants import (
    INTER_PROFILE_PAUSE_SEC,
    IPERF_DURATION_SEC,
    PLOTS_DIR,
    SEPARATOR,
)


def measure_profile(
    profile_key: str,
    profile: Profile,
    direction: str,
    output_dir: Path,
    duration: int = IPERF_DURATION_SEC,
) -> Optional[tuple[dict, object]]:
    if not is_container_running(profile.container):
        print(f"Container '{profile.container}' is not running")
        return None

    print(f"Container '{profile.container}' is up")
    raw_results = run_iperf3_test(
        profile, uplink=(direction == "uplink"), duration=duration
    )

    if raw_results is None:
        print(f"Failed to collect iperf3 data for {profile.label}")
        return None

    save_json_log(raw_results, output_dir, profile_key=profile_key)

    metrics = parse_summary(raw_results)
    if metrics is None:
        return None

    metrics.display()
    return raw_results, metrics


def parse_iperf_json(file_path: Path, direction: str) -> Optional[dict]:
    time_series: dict[str, tuple[pd.DataFrame, str]] = {}

    raw_content = file_path.read_text()
    raw_results = parse_iperf3_output(raw_content)
    start_data = raw_results.get("start", {})
    connected_ips = [c.get("local_host") for c in start_data.get("connected", [])]
    file_ip_addr = connected_ips[0] if connected_ips else None

    matched_profile: Profile | None = None
    if file_ip_addr:
        matched_profile = next(
            (p_obj for p_obj in PROFILES.values() if p_obj.bind_ip == file_ip_addr),
            None,
        )

    if matched_profile:
        print(
            f"Matched {file_path.name} -> {matched_profile.label} (via IP {file_ip_addr})"
        )
        interval_data = parse_intervals(raw_results, direction)
        if not interval_data.empty:
            time_series[matched_profile.label] = (
                interval_data,
                matched_profile.color,
            )
    else:
        print(f"Could not match profile for {file_path.name} (IP: {file_ip_addr})")

    return time_series


def parse_ping_txt_file(file_path: Path) -> Optional[dict]:
    import re

    raw = file_path.read_text()
    rtts = [float(m) for m in re.findall(r"time=([\d.]+)\s*ms", raw)]

    ip_match = None
    m = re.search(r"PING\s+[^\s]+\s+\((\d{1,3}(?:\.\d{1,3}){3})\)", raw)
    if m:
        ip_match = m.group(1)
    else:
        m = re.search(r"from\s+(\d{1,3}(?:\.\d{1,3}){3})", raw)
        if m:
            ip_match = m.group(1)

    matched_profile = None
    if ip_match:
        for p in PROFILES.values():
            if p.bind_ip == ip_match or p.server_ip == ip_match:
                matched_profile = p
                break

    if matched_profile:
        print(
            f"Matched {file_path.name} -> {matched_profile.label} (via IP {ip_match})"
        )
    else:
        print(f"Parsed {file_path.name} (no profile match) -> {len(rtts)} RTT samples")

    return {
        "file": file_path.name,
        "ip": ip_match,
        "profile": matched_profile.label if matched_profile else None,
        "color": matched_profile.color if matched_profile else None,
        "rtts": rtts,
    }


def main() -> None:
    args = arg_parser()
    timestamp = timestamp_now()

    current_out_dir = Path(PLOTS_DIR) / args.direction
    current_out_dir.mkdir(parents=True, exist_ok=True)

    direction_label = args.direction.capitalize()
    selected_metrics = set(args.measure)

    active_profiles = {
        key: profile for key, profile in PROFILES.items() if key in args.profile
    }

    Path(PLOTS_DIR).mkdir(parents=True, exist_ok=True)

    needs_iperf = bool(selected_metrics & {"iperf"})
    needs_ping = "ping" in selected_metrics

    time_series: dict[str, tuple[pd.DataFrame, str]] = {}

    if args.input_file:
        print(f"\n{SEPARATOR}")
        print("Mode: Offline Analysis - Parsing input files")
        print(SEPARATOR)

        rtt_data: dict[str, list[float]] = {}
        palette: dict[str, str] = {}
        time_series: dict[str, tuple[pd.DataFrame, str]] = {}

        for infile in args.input_file:
            try:
                if "iperf" in selected_metrics:
                    ts = parse_iperf_json(file_path=infile, direction=args.direction)
                    for label, data in ts.items():
                        unique_label = label
                        if unique_label in time_series:
                            unique_label = f"{label} ({infile.name})"
                        time_series[unique_label] = data

                if "ping" in selected_metrics:
                    parsed = parse_ping_txt_file(file_path=infile)
                    if parsed and parsed.get("rtts"):
                        label = parsed.get("profile") or parsed.get("file")
                        unique_label = label
                        if unique_label in rtt_data:
                            unique_label = f"{label} ({infile.name})"
                        
                        rtt_data[unique_label] = parsed.get("rtts")
                        if parsed.get("color"):
                            palette[unique_label] = parsed.get("color")
                        else:
                            palette[unique_label] = "#808080" 
            except Exception as e:
                print(f"Error processing {infile.name}: {e}")

        if time_series:
            plot_metric_comparisons(
                time_series, timestamp, current_out_dir, direction_label
            )
        if rtt_data:
            plot_latency_comparison(
                rtt_data, palette, timestamp, current_out_dir
            )

    elif needs_iperf:
        for key, profile in active_profiles.items():
            print(f"\n{SEPARATOR}")
            print(f"Profile: {profile.label}")
            print(SEPARATOR)

            result = measure_profile(
                profile_key=key,
                profile=profile,
                direction=args.direction,
                output_dir=current_out_dir,
                duration=args.iperf_duration,
            )
            if result is None:
                continue

            raw_results, _ = result
            interval_data = parse_intervals(raw_results, args.direction)
            if not interval_data.empty:
                time_series[profile.label] = (interval_data, profile.color)

            time.sleep(INTER_PROFILE_PAUSE_SEC)

        print(f"\n{SEPARATOR}")
        print("Generating comparison plots...")
        print(SEPARATOR)

        plot_metric_comparisons(
            time_series, timestamp, current_out_dir, direction_label
        )

    else:
        if needs_ping:
            print(f"\n{SEPARATOR}")
            print("Running ping latency tests ...")
            print(SEPARATOR)

            rtt_data: dict[str, list[float]] = {}
            palette: dict[str, str] = {}

            for profile in active_profiles.values():
                if not is_container_running(profile.container):
                    print(f"{profile.container} not running — skipping ping")
                    continue
                
                rtts = collect_ping_rtt(profile, count=args.ping_count)
                if rtts:
                    save_ping_log(rtts, profile, current_out_dir)
                    rtt_data[profile.label] = rtts
                    palette[profile.label] = profile.color

            plot_latency_comparison(rtt_data, palette, timestamp, current_out_dir)

    print("\nAll measurements complete!")


if __name__ == "__main__":
    main()
