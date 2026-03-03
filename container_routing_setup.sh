#!/bin/bash
# 1. Enable Forwarding
sudo sysctl -w net.ipv4.ip_forward=1

# 2. NAT / Masquerade (Adjust 'wlp3s0' to your actual interface)
sudo iptables -t nat -I POSTROUTING -s 10.45.0.0/16 -o wlp3s0 -j MASQUERADE
sudo iptables -t nat -A POSTROUTING -s 10.10.0.0/16 ! -o br-open5gs -j MASQUERADE

# 3. Firewall Permissions
sudo iptables -I DOCKER-USER 1 -o br-open5gs -d 10.45.0.0/16 -j ACCEPT
sudo iptables -I DOCKER-USER 1 -i br-open5gs -s 10.45.0.0/16 -j ACCEPT

# 4. Routing to UPF
sudo ip route add 10.45.0.0/16 via 10.10.0.10 dev br-open5gs

# 5. Replace Routing if already exists
sudo ip route replace 10.45.0.0/16 via 10.10.0.10 dev br-open5gs