# QUICUP PROJECT

5G Core Network setup using Open5GS and UERANSIM.

Also, tested on UERANSIM on fedora and OPEN5GS on ubuntu VM - Worked fine.

# TODO

- [x] Migrate the project to Gitlab
- [x] Fix static ports address using docker bridge
- [ ] Explore few alternatives in 3GPP
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


## Connect to Internet

1. To forward packets from containers to internet, host acts as a router (resets to 0 on every reboot)

```bash
sudo sysctl -w net.ipv4.ip_forward=1
```

2. Add UE subnet 10.45.0.0/16 to masquerade in host. So, the traffic from ueransim-UE uses wlan0/eth0 address 

```bash
sudo iptables -t nat -I POSTROUTING -s 10.45.0.0/16 -o wlan0 -j MASQUERADE
```

- optional(Internet access in container):

```bash
  sudo iptables -t nat -A POSTROUTING -s 10.10.0.0/16 ! -o br-open5gs -j MASQUERADE
```

3. Add docker custom bridge br-open5gs to iptables for incoming and outgoing to allow traffic to internet from network


```bash
sudo iptables -I DOCKER-USER 1 -o br-open5gs -d 10.45.0.0/16 -j ACCEPT

sudo iptables -I DOCKER-USER 1 -i br-open5gs -s 10.45.0.0/16 -j ACCEPT
```

1. Add traffic returning from internet to 10.45.0.0/16 forward to 10.10.0.10 via br-open5gs (You may need to re-run Step 4 every time you restart your Docker Compose)


```bash
sudo ip route add 10.45.0.0/16 via 10.10.0.10 dev br-open5gs
```

