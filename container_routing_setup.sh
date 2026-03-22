#!/bin/bash
# 1. Enable Forwarding
sudo sysctl -w net.ipv4.ip_forward=1

# 2. NAT / Masquerade (Adjust 'wlp3s0' to your actual interface)
sudo iptables -t nat -I POSTROUTING -s 10.45.0.0/16 -o wlp3s0 -j MASQUERADE
sudo iptables -t nat -I POSTROUTING -s 10.46.0.0/16 -o wlp3s0 -j MASQUERADE
sudo iptables -t nat -I POSTROUTING -s 10.47.0.0/16 -o wlp3s0 -j MASQUERADE
sudo iptables -t nat -A POSTROUTING -s 10.10.0.0/16 ! -o br-open5gs -j MASQUERADE

# 3. Firewall Permissions
sudo iptables -I DOCKER-USER 1 -o br-gtpu -d 10.45.0.0/16 -j ACCEPT
sudo iptables -I DOCKER-USER 1 -i br-gtpu -s 10.45.0.0/16 -j ACCEPT
sudo iptables -I DOCKER-USER 1 -o br-quic -d 10.46.0.0/16 -j ACCEPT
sudo iptables -I DOCKER-USER 1 -i br-quic -s 10.46.0.0/16 -j ACCEPT
sudo iptables -I DOCKER-USER 1 -o br-ipsec -d 10.47.0.0/16 -j ACCEPT
sudo iptables -I DOCKER-USER 1 -i br-ipsec -s 10.47.0.0/16 -j ACCEPT

# 4. Routing to GTP-U UPF
sudo ip route add 10.45.0.0/16 via 172.22.0.11 dev br-gtpu
sudo ip route replace 10.45.0.0/16 via 172.22.0.11 dev br-gtpu

# 5. Routing to QUIC UPF
sudo ip route add 10.46.0.0/16 via 172.21.0.12 dev br-quic
sudo ip route replace 10.46.0.0/16 via 172.21.0.12 dev br-quic

# 6. Routing to GTP-U IPsec UPF
sudo ip route add 10.47.0.0/16 via 172.23.0.13 dev br-ipsec
sudo ip route replace 10.47.0.0/16 via 172.23.0.13 dev br-ipsec