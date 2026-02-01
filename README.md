# QUICUP PROJECT

5G Core Network setup using Open5GS and UERANSIM.

Also, tested on UERANSIM on fedora and OPEN5GS on ubuntu VM - Worked fine.

# TODO

- [ ] Migrate the project to Gitlab
- [ ] Explore few alternatives in 3GPP
- [ ] Fix static ports address using docker bridge
- [ ] Start implementation of QUIC with msquic


## Prerequisites

- Docker & Linux(better SCTP and tun device support)

## Quick Start (WIP)

1. Clone repository with submodules:

```bash
git clone --recurse-submodules https://github.com/wenkey-gm/QUICUP_PROJECT.git
cd QUICUP_PROJECT
```

Or if already cloned:

```bash
git submodule update --init --recursive
```

1. Build base image:

```bash
docker compose build base
```

2. Build and start services

```bash
docker compose up --build -d
```

### Access Web UI

1. Access WebUI: <http://10.10.0.3:9999>
   - Default credentials: admin/1423
