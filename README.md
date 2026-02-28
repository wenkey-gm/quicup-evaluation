# QUICUP PROJECT (WIP)

5G Core Network setup with QUICUP and GTP-U using Open5GS and UERANSIM.

Also, tested on UERANSIM on fedora and OPEN5GS on ubuntu VM.

# TODO

- [x] Migrate the project to Gitlab
- [x] Fix static ports address using docker bridge
- [x] Explore few alternatives in 3GPP: SRv6
- [x] implementation of QUIC with msquic
- [x] Transport mode flag for gnb and upf 
- [x] Integrate siemens/edge_shark to monitor traffic
- [x] Stream music with gstreamer


## Prerequisites

- Docker & Linux(better SCTP and tun device support)

1. Create openssl certificates and place in 'config/secrets'

```bash
openssl req -x509 -newkey rsa:4096 -keyout server.key -out server.crt -days 365 -nodes -subj "/CN=localhost"
```

## Quick Start

1. Clone repository with submodules:

```bash
git clone https://github.com/wenkey-gm/QUICUP_PROJECT.git
cd QUICUP_PROJECT
```

1. Build base image:

```bash
docker compose build base
```

2. Build and start services

```bash
docker compose up --build -d
```

3. Remove docker compose containers

```bash
docker compose down
```

### Access UI Elements

| Service | URL | Credentials |
|---------|-----|-------------|
| WebUI | <http://localhost:9999> | admin/1423 |
| Dozzle | <http://localhost:8080/> | - |
| EdgeShark | <http://localhost:5001/> | - |

### Container Network Configuration

| Container Name | IP Address | Network | Description |
|----------------|------------|---------|-------------|
| open5gs-mongodb | 10.10.0.2 | shared_network | MongoDB database |
| open5gs-webui | 10.10.0.3 | shared_network | Web management interface |
| ueransim-gnb | 10.10.0.4 | shared_network | 5G gNodeB |
| debug-logs | 10.10.0.9 | shared_network | Dozzle log viewer |
| open5gs-run | 10.10.0.10 | shared_network | 5G Core (NRF, AMF, SMF, UPF, etc.) |
| ueransim-ue | 10.10.0.16 | shared_network | User Equipment (UE) |
| gostwire | - | ghost-in-da-edge | Network discovery service |
| edgeshark | - | ghost-in-da-edge | Packet capture service |


## Connect to Internet

1. To forward packets from containers to internet, host acts as a router (resets to 0 on every reboot)

```bash
sudo sysctl -w net.ipv4.ip_forward=1
```

2. Add UE subnet 10.45.0.0/16 to masquerade in host. So, the traffic from ueransim-UE uses wlan0/eth0 address. CHeck this thoroughly.

```bash
sudo iptables -t nat -I POSTROUTING -s 10.45.0.0/16 -o {YOUR_NETWORK_INTERFACE(eth0/wlan)} -j MASQUERADE
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

4. Add traffic returning from internet to 10.45.0.0/16 forward to 10.10.0.10 via br-open5gs (You may need to re-run Step 4 every time you restart your Docker Compose)


```bash
sudo ip route add 10.45.0.0/16 via 10.10.0.10 dev br-open5gs
```

## Music streamer over QUIC

#### Setup: create route with ue with 10.10.0.1 to bypass network inteface(Run this in container)

```bash
ip route add 10.10.0.1/32 dev uesimtun0
```

1. Run this in ue container

```bash
gst-launch-1.0 filesrc location=music.mp3 ! decodebin !   audioconvert ! audioresample !   audio/x-raw,rate=48000,channels=2 !   opusenc ! rtpopuspay !   udpsink host=10.10.0.1 port=5004
```

2. Run this in host container

```bash
gst-launch-1.0 udpsrc port=5004 caps="application/x-rtp,media=audio,encoding-name=OPUS,payload=96,clock-rate=48000" ! rtpopusdepay ! opusdec ! audioconvert ! audioresample ! autoaudiosink
```

## References

- [MSQUIC GitHub](https://github.com/microsoft/msquic)
- [QUIC RFC 9000](https://www.rfc-editor.org/rfc/rfc9000.html)
- [3GPP TS 29.281 - GTP-U Protocol](https://www.3gpp.org/DynaReport/29281.htm)
- [Open5GS Documentation](https://open5gs.org/open5gs/docs/)
