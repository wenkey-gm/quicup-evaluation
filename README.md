# QUICUP PROJECT

5G Core Network setup using Open5GS and UERANSIM.

## Prerequisites
- Docker & Docker Compose
- Linux system (for TUN device support)

## Quick Start

1. Copy environment template:
```bash
cp .env.example .env
```

2. Edit `.env` with your settings

3. Build and start services:
```bash
docker compose up --build -d
```

4. Access WebUI: http://localhost:9998
   - Default credentials: admin/1423

## Services
- **MongoDB**: Database (port 27018)
- **WebUI**: Management interface (port 9998)
- **dev**: Open5GS core network
- **ueransim-gnb**: gNodeB simulator
- **ueransim-ue**: UE simulator

## Commands
```bash
# View logs
docker compose logs -f

# Stop services
docker compose down

# Rebuild
docker compose up --build -d
```