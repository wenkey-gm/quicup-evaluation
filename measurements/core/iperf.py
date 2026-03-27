from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
from utils import docker_helpers
from utils.constants import (
    IPERF_BANDWIDTH,
    IPERF_DURATION_SEC,
    IPERF_MAX_RETRIES,
    IPERF_RETRY_DELAY_SEC,
)
from entities.entities import PerformanceMetrics


def _server_output(results: dict) -> dict:
    return results.get("server_output_json") or results


def parse_iperf3_output(stdout: str) -> dict:
    payload = stdout.strip()
    if not payload:
        raise ValueError("iperf3 produced empty output")
    try:
        top_level = json.loads(payload)
        if isinstance(top_level, dict) and (
            "start" in top_level or "intervals" in top_level or "end" in top_level
        ):
            return top_level
    except json.JSONDecodeError:
        pass

    results: dict = {"intervals": []}
    for line in payload.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
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
            print("iperf3 stream error event detected:")
            print(f"  data: {data}")
            print(f"  full object: {obj}")
            try:
                print(f"  raw line: {line}")
            except Exception:
                pass
        elif isinstance(obj, dict) and (
            "start" in obj or "intervals" in obj or "end" in obj
        ):
            if "start" in obj:
                results["start"] = obj.get("start")
            if "intervals" in obj:
                ivs = obj.get("intervals") or []
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


def parse_summary(results: dict) -> PerformanceMetrics | None:
    if not results:
        return None
    try:
        end = _server_output(results)["end"]
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
        print(f"Missing key: {exc}")
        return None


def parse_intervals(results: dict, direction_label: str):
    data_source = _server_output(results)
    intervals = data_source.get("intervals", [])

    rows: list[dict] = []
    for interval in intervals:
        streams = interval.get("streams", [])

        if not streams:
            if interval.get("sum"):
                streams = [interval["sum"]]
            elif interval.get("sum_bidir_reverse"):
                streams = [interval["sum_bidir_reverse"]]

        for stream in streams:
            is_sender = stream.get("sender", True)
            suffix = " (TX)" if is_sender else " (RX)"
            row_direction = f"{direction_label}{suffix}"

            lost_packets = stream.get("lost_packets", 0)
            packets = stream.get("packets", 0)
            lost_percent = stream.get("lost_percent", 0.0)

            if lost_percent == 0.0 and packets > 0 and lost_packets > 0:
                lost_percent = (lost_packets / packets) * 100.0

            rows.append(
                {
                    "time": stream.get("end", 0),
                    "bitrate_mbps": stream.get("bits_per_second", 0) / 1_000_000,
                    "jitter_ms": stream.get("jitter_ms", 0),
                    "lost_packets": lost_packets,
                    "packets": packets,
                    "lost_percent": lost_percent,
                    "direction": row_direction,
                }
            )

    return pd.DataFrame(rows)


def save_json_log(results: dict, output_dir: Path, profile_key: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = Path().name

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"iperf3_{profile_key}_{stamp}.jsonl"
    path.write_text(json.dumps(results, indent=2))

    print(f"Saved raw log : {path}")
    return path


def run_iperf3_test(
    profile,
    uplink: bool = False,
    duration: int = IPERF_DURATION_SEC,
    retries: int = IPERF_MAX_RETRIES,
    retry_delay: int = IPERF_RETRY_DELAY_SEC,
) -> dict | None:
    container = profile.container
    iperf_flags = (
        f"-c {profile.server_ip} -B {profile.bind_ip} -p 5201 -i 0.5 -l 1300 "
        f"-u -b {IPERF_BANDWIDTH} -R --json -t {duration}"
    )

    if uplink:
        iperf_flags = iperf_flags.replace("-R", "")
        iperf_flags = iperf_flags + " --get-server-output"

    for attempt in range(1, retries + 1):
        print(f"\n  iperf3 UDP test (attempt {attempt}/{retries}, {duration}s)")
        print(
            f"Server {profile.server_ip}  bind {profile.bind_ip} in {'uplink' if uplink else 'downlink'}"
        )
        print(f"Running: iperf3 {iperf_flags}")

        stdout, stderr, rc = docker_helpers.docker_exec(
            container, f"iperf3 {iperf_flags}", timeout=duration + 60
        )

        if rc != 0:
            print(f"Test failed (exit {rc})")
            if stderr:
                print(f"STDERR: {stderr.strip()}")
            if stdout:
                try:
                    parsed = parse_iperf3_output(stdout)
                    if parsed.get("intervals"):
                        print("Parsed interval data from non-zero iperf3 run")
                        return parsed
                except Exception:
                    pass

            if (
                "server is busy" in (stderr or "") + (stdout or "")
                and attempt < retries
            ):
                print(f"Server busy, retrying in {retry_delay}s ...")
                time.sleep(retry_delay)
                continue
            return None

        if not stdout:
            print("iperf3 produced no output")
            return None

        try:
            results = parse_iperf3_output(stdout)
            print("iperf3 test completed successfully")
            return results
        except Exception as exc:
            print(f"Failed to parse iperf3 stream: {exc}")
            return None
    return None
