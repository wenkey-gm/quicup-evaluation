# QUICUP PROJECT

5G Core Network setup using Open5GS and UERANSIM.

Also, tested on UERANSIM on fedora and OPEN5GS on ubuntu VM - Worked fine.

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

1. Build and start services:

```bash
docker compose up --build -d
```

1. Access WebUI: <http://localhost:9999>
   - Default credentials: admin/1423

## Commands

```bash
# docker commands
docker compose up --build
docker compose down

# Update submodules
git submodule update --remote
```
