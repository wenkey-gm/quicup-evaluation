# UERANSIM + Open5GS Network Performance Documentation

## 1. Working Baseline Topology + Diagram

<p align="center">
  <img src="basic_topology_structure.png" width="700" alt="Basic Topology">
</p>

## 2. Baseline Measurement Results

### Performance Metrics

- **Bitrate (Mbps) - 9.99; Jitter (ms) - 0.010; Avg RTT (ms) - 0.327**

<p align="center">
  <img src="../measurements/plots/bitrate_20260202_174754.png" width="500" alt="Network Performance Metrics">
</p>

### Overhead for IPv4
- GTP-U: 20(IP) + 8(UDP) + 12(GTP-U) = 40 Bytes
- GTP-U with IPSec: 20(IP)+8(UDP)+12(GTP-U)+58(IPsec) = 98 Bytes
- QUICUP: 20(IP)+8(UDP)+34(QUIC/Auth Tag)=62 Bytes

**Approx. 20 bytes more for IPv6**

## 3. QUIC-Based User-Plane Architecture

<p align="center">
  <img src="quic_diagram.png" width="700" alt="QUIC Architecture">
</p>

**Implementation Approach:**

1. **Direct Replacement**: Replace GTP-U with QUIC streams in the N3 interface
   - Simplified architecture
   - Reduced protocol overhead
   - Requires modifications to both UERANSIM and OPEN5GS

2. **Encapsulation Mode**: Encapsulate GTP-U within QUIC tunnel
   - Maintains 3GPP compatibility
   - Easier integration with existing infrastructure
   - Gradual migration path


## 4. QUIC Library Evaluation

| Library | Language | License | Features |
|---------|----------|---------|----------|
| **quiche** (Cloudflare) | Rust (C/C++ bindings) | BSD-2-Clause | Production-ready, HTTP/3 support |
| **msquic** (Microsoft) | C | MIT | IETF RFC 9000, Windows/Azure proven |
| **ngtcp2** (nghttp2) | C | MIT | Lightweight, minimal dependencies |
| **lsquic** (LiteSpeed) | C | MIT | High performance, web-optimized |
| **mvfst** (Meta) | C++ | MIT | Meta-scale tested, advanced congestion control |

### Recommended Implementation: **msquic**

- **Native C API**: Seamless integration with Open5GS C codebase
- **Cross-platform**: Linux compatibility for 5G deployments
- **License compatibility**: MIT license suitable for open-source projects