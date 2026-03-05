//
// Created by munivg on 2/11/26.
//

#include "quic-server.h"
#include <arpa/inet.h>
#include <msquic.h>
#include "gtp-path.h"
#include "metrics.h"


typedef struct ogs_quic_send_ctx_s {
    QUIC_BUFFER quic_buffer;
    uint8_t payload[2048];
} ogs_quic_send_ctx_t;

typedef struct quic_client_node_s {
    ogs_lnode_t lnode;               // Open5GS linked-list node
    HQUIC conn;                      // The MsQuic Connection Handle
    char ip_address[INET6_ADDRSTRLEN]; // The gNB's IP address
} quic_client_node_t;


ogs_list_t quic_client_list;
ogs_thread_mutex_t quic_client_mutex;
OGS_POOL(quic_client_pool, quic_client_node_t);

ogs_thread_mutex_t quic_pool_mutex;
OGS_POOL(quic_send_pool, ogs_quic_send_ctx_t);

static ogs_quic_context_t quic_context = {0};

ogs_quic_context_t *ogs_quic_self(void)
{
    return &quic_context;
}

QUIC_STATUS QUIC_API ConnectionCallback(HQUIC Connection, void *Context, QUIC_CONNECTION_EVENT *Event);
QUIC_STATUS QUIC_API ListnerCallback(HQUIC Listener, void *Context, QUIC_LISTENER_EVENT *Event);

int ogs_quic_server_start(const char *bind_address)
{

    ogs_list_init(&quic_client_list);
    ogs_thread_mutex_init(&quic_client_mutex);
    ogs_pool_init(&quic_client_pool, 128);


    ogs_pool_init(&quic_send_pool, 4096);
    ogs_thread_mutex_init(&quic_pool_mutex);

    ogs_quic_context_t *ctx = ogs_quic_self();

    const char *alpn = "n3-quic";
    const char *app_name = "n3-quic";

    if (QUIC_FAILED(ctx->status = StartQuicServer(ctx, alpn, app_name, bind_address)))
    {
        ogs_error("Failed to start QUIC server! Status: 0x%x\n", ctx->status);
        return OGS_ERROR;
    }

    return OGS_OK;
}

void ogs_quic_server_stop(void)
{
    StopQuicServer(ogs_quic_self());
}

QUIC_STATUS StartQuicServer(ogs_quic_context_t *ServerCtx, const char *alpn, const char *app_name, const char *bind_address)
{
    ogs_quic_context_t *ctx = ogs_quic_self();

    if (QUIC_FAILED(ctx->status = MsQuicOpen2(&ctx->MsQuic)))
    {
        ogs_error("MsQuicOpen2 failed: 0x%x\n", ctx->status);
        return ctx->status;
    }

    QUIC_REGISTRATION_CONFIG RegConfig = {app_name, QUIC_EXECUTION_PROFILE_TYPE_MAX_THROUGHPUT};
    if (QUIC_FAILED(ctx->status = ctx->MsQuic->RegistrationOpen(&RegConfig, &ServerCtx->Registration)))
    {
        ogs_error("Registration open failed: 0x%x\n", ctx->status);
        return ctx->status;
    }

    QUIC_BUFFER AlpnBuffer = {(uint32_t)strlen(alpn), (uint8_t *)alpn};
    QUIC_SETTINGS Settings = {0};

    Settings.DatagramReceiveEnabled = 1;
    Settings.IdleTimeoutMs = 0;
    Settings.KeepAliveIntervalMs = 25000;
    Settings.ServerResumptionLevel = QUIC_SERVER_RESUME_AND_ZERORTT;

    Settings.IsSet.DatagramReceiveEnabled = 1;
    Settings.IsSet.IdleTimeoutMs = 1;
    Settings.IsSet.KeepAliveIntervalMs = 1;
    Settings.IsSet.ServerResumptionLevel = 1;

    if (QUIC_FAILED(ctx->status = ctx->MsQuic->ConfigurationOpen(
                        ServerCtx->Registration, &AlpnBuffer, 1, &Settings, sizeof(Settings),
                        ServerCtx, &ServerCtx->Configuration)))
    {
        ogs_error("Configuration open failed: 0x%x\n", ctx->status);
        return ctx->status;
    }

    QUIC_CREDENTIAL_CONFIG CredConfig={0};
    const char *key_path = upf_self()->quic_key_path;
    const char *cert_path = upf_self()->quic_cert_path;
    QUIC_CERTIFICATE_FILE CertFile = {key_path, cert_path};

    CredConfig.Type = QUIC_CREDENTIAL_TYPE_CERTIFICATE_FILE;
    CredConfig.CertificateFile = &CertFile;
    CredConfig.Flags = QUIC_CREDENTIAL_FLAG_NONE;

    if (QUIC_FAILED(ctx->status = ctx->MsQuic->ConfigurationLoadCredential(ServerCtx->Configuration, &CredConfig)))
    {
        ogs_error("Configuration load failed with status: 0x%x\n", ctx->status);
        return ctx->status;
    }

    if (QUIC_FAILED(ctx->status = ctx->MsQuic->ListenerOpen(
                        ServerCtx->Registration, ListnerCallback, ServerCtx, &ServerCtx->Listener)))
    {
        ogs_error("Listener open failed: 0x%x\n", ctx->status);
        return ctx->status;
    }

    QUIC_ADDR Address={0};
    struct sockaddr_in *AddrV4 = (struct sockaddr_in *)&Address;
    AddrV4->sin_family = AF_INET;
    AddrV4->sin_port = htons(OGS_GTPV1_U_QUIC_PORT);

    if (inet_pton(AF_INET, bind_address, &AddrV4->sin_addr) != 1)
    {
        ogs_error("Invalid IPv4 bind address in YAML: %s", bind_address);
        return QUIC_STATUS_INVALID_PARAMETER;
    }

    if (QUIC_FAILED(ctx->status = ctx->MsQuic->ListenerStart(ServerCtx->Listener, &AlpnBuffer, 1, &Address)))
    {
        ogs_error("Listener start failed: 0x%x\n", ctx->status );
        return ctx->status;
    }

    return QUIC_STATUS_SUCCESS;
}

void StopQuicServer(ogs_quic_context_t *ServerCtx)
{
    ogs_quic_context_t *ctx = ogs_quic_self();
    if (ctx->MsQuic != NULL)
    {
        if (ServerCtx->Listener != NULL)
        {
            ctx->MsQuic->ListenerStop(ServerCtx->Listener);
        }
        ogs_thread_mutex_lock(&quic_client_mutex);
        quic_client_node_t *node = NULL;
        ogs_list_for_each(&quic_client_list, node) {
            ctx->MsQuic->ConnectionShutdown(node->conn, QUIC_CONNECTION_SHUTDOWN_FLAG_NONE, 0);
        }
        ogs_thread_mutex_unlock(&quic_client_mutex);
        if (ServerCtx->Configuration != NULL)
        {
            ctx->MsQuic->ConfigurationClose(ServerCtx->Configuration);
            ServerCtx->Configuration = NULL;
        }
        if (ServerCtx->Registration != NULL)
        {
            ctx->MsQuic->RegistrationClose(ServerCtx->Registration);
            ServerCtx->Registration = NULL;
        }

        MsQuicClose(ctx->MsQuic);
        ctx->MsQuic = NULL;

        ogs_pool_final(&quic_send_pool);
        ogs_thread_mutex_destroy(&quic_pool_mutex);

        ogs_pool_final(&quic_client_pool);
        ogs_thread_mutex_destroy(&quic_client_mutex);

        ogs_debug("Server resources cleanly destroyed.\n");
    }
}

void quic_server_send_downlink(const char *dest_gnb_ip,uint32_t teid, uint8_t *packet_data, uint16_t packet_len)
{
    ogs_quic_context_t *ctx = ogs_quic_self();

    HQUIC target_connection = NULL;

    ogs_thread_mutex_lock(&quic_client_mutex);
    quic_client_node_t *node = NULL;
    ogs_list_for_each(&quic_client_list, node) {
        if (strcmp(node->ip_address, dest_gnb_ip) == 0) {
            target_connection = node->conn;
            break;
        }
    }
    ogs_thread_mutex_unlock(&quic_client_mutex);

    if (target_connection == NULL) {
        ogs_warn("QUIC Downlink dropped: gNB %s is not connected.", dest_gnb_ip);
        return;
    }

    if (!ctx || !ctx->MsQuic)
    {
        ogs_warn("QUIC Downlink dropped: No active client connected.");
        return;
    }

    if (!packet_data || packet_len == 0)
    {
        ogs_warn("QUIC Downlink dropped: Invalid packet data.");
        return;
    }


    const uint16_t header_len = sizeof(uint32_t);
    const uint16_t total_len = header_len + packet_len;

    ogs_quic_send_ctx_t* send_buffer = NULL;
    ogs_thread_mutex_lock(&quic_pool_mutex);
    ogs_pool_alloc(&quic_send_pool, &send_buffer);
    ogs_thread_mutex_unlock(&quic_pool_mutex);
    if (!send_buffer)
    {
        ogs_error("QUIC Downlink dropped: Out of memory (pool failed).");
        return;
    }

    uint32_t network_teid = htonl(teid);
    memcpy(send_buffer->payload, &network_teid, header_len);
    memcpy(send_buffer->payload + header_len, packet_data, packet_len);

    send_buffer->quic_buffer.Length = total_len;
    send_buffer->quic_buffer.Buffer = send_buffer->payload;

    if (QUIC_FAILED(ctx->status = ctx->MsQuic->DatagramSend(
        target_connection,
        &send_buffer->quic_buffer,
        1,
        QUIC_SEND_FLAG_NONE,
        send_buffer)))
    {
        ogs_error("QUIC Downlink: DatagramSend failed! 0x%x", ctx->status);
        ogs_thread_mutex_lock(&quic_pool_mutex);
        ogs_pool_free(&quic_send_pool, send_buffer);
        ogs_thread_mutex_unlock(&quic_pool_mutex);
    }
    upf_metrics_inst_global_add(UPF_METR_GLOB_CTR_QUIC_OUTDATAPKTN3UPF, 1);
}

void quic_server_handle_uplink(const QUIC_BUFFER *buffer)
{
    if (buffer->Length < 4)
    {
        ogs_warn("QUIC Uplink dropped: Datagram too small.");
        return;
    }

    uint32_t network_teid;
    memcpy(&network_teid, buffer->Buffer, sizeof(uint32_t));
    uint32_t teid = ntohl(network_teid);

    const uint8_t *ip_packet = buffer->Buffer + 4;
    uint32_t ip_packet_len = buffer->Length - 4;

    ogs_pkbuf_t *pkbuf = ogs_pkbuf_alloc(NULL, OGS_TUN_MAX_HEADROOM + ip_packet_len);
    if (!pkbuf)
    {
        ogs_error("QUIC Uplink: Failed to allocate Open5GS pkbuf!");
        return;
    }
    ogs_pkbuf_reserve(pkbuf, OGS_TUN_MAX_HEADROOM);

    ogs_pkbuf_put_data(pkbuf, ip_packet, ip_packet_len);

    upf_n3_route_uplink(teid, pkbuf);
}

QUIC_STATUS QUIC_API ConnectionCallback(HQUIC Conn, void* Context, QUIC_CONNECTION_EVENT* Event)
{
    ogs_quic_context_t *ctx = ogs_quic_self();
    switch (Event->Type)
    {
    case QUIC_CONNECTION_EVENT_CONNECTED:
        {
            QUIC_ADDR remote_addr = {0};
            uint32_t addr_len = sizeof(remote_addr);
            ctx->MsQuic->GetParam(Conn, QUIC_PARAM_CONN_REMOTE_ADDRESS, &addr_len, &remote_addr);

            char ip_str[INET6_ADDRSTRLEN] = {0};
            if (remote_addr.Ipv4.sin_family == AF_INET) {
                inet_ntop(AF_INET, &remote_addr.Ipv4.sin_addr, ip_str, sizeof(ip_str));
            } else {
                inet_ntop(AF_INET6, &remote_addr.Ipv6.sin6_addr, ip_str, sizeof(ip_str));
            }

            ogs_info("gNB connected from IP: %s", ip_str);

            quic_client_node_t *node;
            ogs_pool_alloc(&quic_client_pool, &node);
            node->conn = Conn;
            strcpy(node->ip_address, ip_str);

            ogs_thread_mutex_lock(&quic_client_mutex);
            ogs_list_add(&quic_client_list, node);
            ogs_thread_mutex_unlock(&quic_client_mutex);

            if (Event->CONNECTED.SessionResumed)
            {
                ogs_debug("Connection established! (RESUMED 0-RTT/1-RTT)\n");
            }
            else
            {
                if QUIC_FAILED(ctx->status = ctx->MsQuic->ConnectionSendResumptionTicket(Conn, QUIC_SEND_RESUMPTION_FLAG_NONE, 0, NULL))
                {
                    ogs_error("Sending Resumption ticket failed! 0x%x", ctx->status);
                    return ctx->status;
                }
            }
        }
        break;
    case QUIC_CONNECTION_EVENT_SHUTDOWN_INITIATED_BY_TRANSPORT:
        ogs_error("Connection shutdown by transport. Error Code: 0x%llx (%" PRIu64 ")\n",
                  (unsigned long long)Event->SHUTDOWN_INITIATED_BY_TRANSPORT.ErrorCode,
                  Event->SHUTDOWN_INITIATED_BY_TRANSPORT.ErrorCode);
        break;
    case QUIC_CONNECTION_EVENT_SHUTDOWN_INITIATED_BY_PEER:
        ogs_debug("Terminated by peer.\n");
        break;
    case QUIC_CONNECTION_EVENT_SHUTDOWN_COMPLETE:
        {
            ogs_thread_mutex_lock(&quic_client_mutex);

            quic_client_node_t *node = NULL;

            ogs_list_for_each(&quic_client_list, node) {
                if (node->conn == Conn) {
                    ogs_info("Removing gNB %s from active list.", node->ip_address);
                    ogs_list_remove(&quic_client_list, node);
                    ogs_pool_free(&quic_client_pool, node);
                    break;
                }
            }
            ogs_thread_mutex_unlock(&quic_client_mutex);
            ctx->MsQuic->ConnectionClose(Conn);
            ogs_debug("Connection Closed.\n");
        }
        break;
    case QUIC_CONNECTION_EVENT_LOCAL_ADDRESS_CHANGED:
        ogs_debug("Local address Changed.\n");
        break;
    case QUIC_CONNECTION_EVENT_PEER_ADDRESS_CHANGED:
        ogs_debug("peer address changed.\n");
        break;
    case QUIC_CONNECTION_EVENT_PEER_STREAM_STARTED:
        ogs_debug("Stream Started by Peer.\n");
        break;
    case QUIC_CONNECTION_EVENT_STREAMS_AVAILABLE:
        ogs_debug("Streams Available.\n");
        break;
    case QUIC_CONNECTION_EVENT_PEER_NEEDS_STREAMS:
        ogs_debug("Streams Need to be available.");
        break;
    case QUIC_CONNECTION_EVENT_IDEAL_PROCESSOR_CHANGED:
        ogs_debug("Processor Changed Ideal Processor.");
        break;
    case QUIC_CONNECTION_EVENT_DATAGRAM_STATE_CHANGED:
    {
        ogs_debug("Datagram State Changed. Max Send Length: %u",
                  Event->DATAGRAM_STATE_CHANGED.MaxSendLength);
        break;
    }
    case QUIC_CONNECTION_EVENT_DATAGRAM_RECEIVED:
    {
        const QUIC_BUFFER *Buffer = Event->DATAGRAM_RECEIVED.Buffer;
        ogs_debug("MsQuic Server: DATAGRAM RECEIVED! Length: %d", Event->DATAGRAM_RECEIVED.Buffer->Length);
        quic_server_handle_uplink(Buffer);
        upf_metrics_inst_global_inc(UPF_METR_GLOB_CTR_QUIC_INDATAPKTN3UPF);
        break;
    }
    case QUIC_CONNECTION_EVENT_DATAGRAM_SEND_STATE_CHANGED:
    {
        int state = Event->DATAGRAM_SEND_STATE_CHANGED.State;
        if (state == QUIC_DATAGRAM_SEND_ACKNOWLEDGED ||
            state == QUIC_DATAGRAM_SEND_ACKNOWLEDGED_SPURIOUS ||
            state == QUIC_DATAGRAM_SEND_CANCELED ||
            state == QUIC_DATAGRAM_SEND_LOST_DISCARDED)
        {
            ogs_quic_send_ctx_t *send_context = Event->DATAGRAM_SEND_STATE_CHANGED.ClientContext;
            if (send_context!=NULL)
            {
                ogs_thread_mutex_lock(&quic_pool_mutex);
                ogs_pool_free(&quic_send_pool, send_context);
                ogs_thread_mutex_unlock(&quic_pool_mutex);
            }
        }
        break;
    }
    case QUIC_CONNECTION_EVENT_RESUMED:
        ogs_debug("Resumed.");
        break;
    case QUIC_CONNECTION_EVENT_RESUMPTION_TICKET_RECEIVED:
        ogs_debug("Resumption Ticket Received.");
        break;
    case QUIC_CONNECTION_EVENT_PEER_CERTIFICATE_RECEIVED:
        ogs_debug("Peer Certificate Received.");
        break;
    }
    return QUIC_STATUS_SUCCESS;
}

QUIC_STATUS QUIC_API ListnerCallback(HQUIC Listener, void *Context, QUIC_LISTENER_EVENT *Event)
{
    ogs_quic_context_t *ctx = ogs_quic_self();
    switch (Event->Type)
    {
    case QUIC_LISTENER_EVENT_NEW_CONNECTION:
    {
        ogs_debug("New Connection from Client\n");
        ctx->MsQuic->SetCallbackHandler(Event->NEW_CONNECTION.Connection, (void *)ConnectionCallback, Context);

        if (QUIC_FAILED(ctx->status = ctx->MsQuic->ConnectionSetConfiguration(Event->NEW_CONNECTION.Connection, ctx->Configuration)))
        {
            ogs_error("ConnectionSetConfiguration failed: 0x%x\n", ctx->status);
            return ctx->status;
        }
        return QUIC_STATUS_SUCCESS;
    }
    case QUIC_LISTENER_EVENT_STOP_COMPLETE:
        if (ctx->Listener!=NULL){
            ctx->MsQuic->ListenerClose(ctx->Listener);
            ctx->Listener=NULL;
        }
        break;

    case QUIC_LISTENER_EVENT_DOS_MODE_CHANGED:
        ogs_debug("Dos mode changed.\n");
        break;
    }

    return QUIC_STATUS_SUCCESS;
}
