from entities.entities import Profile

PROFILES: dict[str, Profile] = {
    "quic": Profile(
        label="QUICUP (Encrypted)",
        container="ueransim-ue-quic",
        server_ip="10.46.0.1",
        bind_ip="10.46.0.2",
        color="#2ecc71",
    ),
    "gtpu": Profile(
        label="GTP-U (Standard)",
        container="ueransim-ue-gtpu",
        server_ip="10.45.0.1",
        bind_ip="10.45.0.2",
        color="#3498db",
    ),
    "ipsec": Profile(
        label="GTP-U + IPsec (Encrypted)",
        container="ueransim-ue-gtpu-ipsec",
        server_ip="10.47.0.1",
        bind_ip="10.47.0.2",
        color="#e74c3c",
    ),
}
