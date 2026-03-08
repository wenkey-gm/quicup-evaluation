#!/bin/sh
# Usage: ipsec_setup.sh <ipsec_conf>
#   ipsec_conf - which conf file to use (e.g. upf_ipsec_gtpu.conf or gnb_ipsec_gtpu.conf)
#
# The initiator side should use auto=start in its .conf (non-blocking).
# The responder side should use auto=add in its .conf (waits for peer).
# This script only starts the daemon — tunnel negotiation is handled by charon.
#
# Controlled by the IPSEC env var (true/false).

IPSEC_CONF="$1"

if [ "${IPSEC:-false}" = "true" ]; then
    echo "[ipsec_setup] IPsec enabled — starting StrongSwan"
    cp "/ipsec/${IPSEC_CONF}" /etc/ipsec.conf
    cp /ipsec/ipsec.secrets /etc/ipsec.secrets
    chmod 600 /etc/ipsec.secrets
    ipsec start
    sleep 2
    echo "[ipsec_setup] StrongSwan started"
else
    echo "[ipsec_setup] IPsec disabled — skipping"
fi
