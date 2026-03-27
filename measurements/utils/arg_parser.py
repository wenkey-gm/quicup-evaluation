import argparse
from pathlib import Path

from profiles.profiles import PROFILES
from utils.constants import (
    AVAILABLE_TOOLS,
    IPERF_DURATION_SEC,
    PING_COUNT,
)


def arg_parser() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="5G User-Plane Measurements QUIC vs GTP-U vs GTP-U+IPsec",
    )
    parser.add_argument(
        "-p",
        "--profile",
        nargs="+",
        choices=list(PROFILES.keys()),
        default=list(PROFILES.keys()),
        metavar="PROFILE",
        dest="profile",
        help="Profiles to run. Defaults to all profiles matching the chosen --direction.",
    )
    parser.add_argument(
        "-m",
        "--measure",
        nargs="+",
        choices=AVAILABLE_TOOLS,
        default=AVAILABLE_TOOLS,
        metavar="MEASURE",
        dest="measure",
        help="Tools to measure (default: all). Choices: ping, iperf.",
    )
    parser.add_argument(
        "-pc",
        "--ping-count",
        type=int,
        default=PING_COUNT,
        metavar="N",
        dest="ping_count",
        help="Number of ping packets per profile.",
    )
    parser.add_argument(
        "--iperf-duration",
        type=int,
        default=IPERF_DURATION_SEC,
        metavar="SEC",
        dest="iperf_duration",
        help="Duration of each iperf3 test in seconds.",
    )
    parser.add_argument(
        "-dir",
        "--direction",
        choices=["downlink", "uplink"],
        default="downlink",
        dest="direction",
        help="Sets the plot titles and output folder (default: downlink).",
    )
    parser.add_argument(
        "-ifile",
        "--input-file",
        nargs="+",
        type=Path,
        dest="input_file",
        metavar="FILE",
        help="Path to existing iperf3 or ping files. If provided, skips live measurements.",
    )
    return parser.parse_args()
