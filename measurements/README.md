5G User-Plane Performance Benchmarks
=====================================

Compare throughput, jitter, packet loss and latency across three
transport strategies on the N3 interface:

- QUIC      (QUICUP — natively encrypted)
- GTP-U     (standard, unencrypted)
- GTP-U+IPsec (encrypted via StrongSwan ESP tunnel)

Each profile runs an iperf3 UDP test through the corresponding
UE → gNB → UPF path, then a ping latency sweep.  Results are
saved as JSON logs and comparison PNG plots.

Usage
-----

    python main.py                          # all profiles, all metrics
    python main.py -p quic gtpu             # only QUIC and GTP-U
    python main.py -p ipsec                 # only GTP-U+IPsec
    python main.py -m rtt                   # latency only (ping)
    python main.py -m throughput jitter      # iperf3 metrics only
    python main.py -p quic -m rtt throughput # combine both flags
    python main.py --ping-count 50           # 50 ping packets (default: 30)
    python main.py --iperf-duration 60       # 60s iperf3 test (default: 100)
    python main.py -dir uplink               # specify whether to use uplink or downlink
