//
// Created by munivg on 2/11/26.
//

#pragma once


#include <msquic.h>
#include <stdlib.h>
#include <string.h>
#include "ogs-core.h"
#include "context.h"

#define OGS_GTPV1_U_QUIC_PORT           4567


typedef struct ogs_quic_context_s
{
    const QUIC_API_TABLE* MsQuic;
    HQUIC Registration;
    HQUIC Configuration;
    HQUIC Listener;
    HQUIC Connection;
    HQUIC active_client_connection;
    QUIC_STATUS status;
} ogs_quic_context_t;


ogs_quic_context_t *ogs_quic_self(void);

int ogs_quic_server_start(const char *bind_address);
void ogs_quic_server_stop(void);
QUIC_STATUS StartQuicServer(ogs_quic_context_t* ServerCtx, const char* alpn, const char* app_name, const char* bind_address);
void StopQuicServer(ogs_quic_context_t* ServerCtx);
void quic_server_send_downlink(const char *dest_gnb_ip,uint32_t teid, uint8_t *packet_data, uint16_t packet_len);
void quic_server_handle_uplink(const QUIC_BUFFER* buffer);
