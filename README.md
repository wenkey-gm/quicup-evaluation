# QUICUP PROJECT

5G Core Network setup using Open5GS and UERANSIM.

Tested on UERANSIM on fedora and OPEN5GS on ubuntu VM - Worked fine.

WIP: Docker compose not yet work because of same host network https://github.com/aligungr/UERANSIM/issues/673

## Prerequisites

- Docker & Docker Compose
- Linux system (for TUN device support)
- Git (for submodules)

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
# View logs
docker compose logs -f

# Stop services
docker compose down

# Rebuild
docker compose up --build -d

# Update submodules
git submodule update --remote
```
