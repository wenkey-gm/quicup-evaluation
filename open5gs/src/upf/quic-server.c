//
// Created by munivg on 2/11/26.
//

#include <arpa/inet.h>
#include "quic-server.h"
#include "upf-sm.h"
#include "event.h"
#include "gtp-path.h"

static ogs_quic_context_t quic_context = {0};

ogs_quic_context_t *ogs_quic_self(void)
{
    return &quic_context;
}

QUIC_STATUS QUIC_API ConnectionCallback(HQUIC Connection, void *Context, QUIC_CONNECTION_EVENT *Event);
QUIC_STATUS QUIC_API ListnerCallback(HQUIC Listener, void *Context, QUIC_LISTENER_EVENT *Event);

int ogs_quic_server_start(const char *bind_address, uint16_t port)
{
    ogs_info("[QUIC N3] Starting MsQuic Server on %s...", bind_address);

    ogs_quic_context_t *ctx = ogs_quic_self();

    const char *alpn = "n3-quic";
    const char *app_name = "n3-quic";

    QUIC_STATUS status = StartQuicServer(ctx, alpn, app_name, bind_address, port);
    if (QUIC_FAILED(status))
    {
        ogs_error("Failed to start QUIC server! Status: %u", status);
        return OGS_ERROR;
    }

    ogs_info("[QUIC N3] MsQuic Server started successfully!");
    return OGS_OK;
}

void ogs_quic_server_stop(void)
{
    ogs_info("[QUIC N3] Stopping MsQuic Server...");

    // Call your actual shutdown logic here
    StopQuicServer(ogs_quic_self());
}

QUIC_STATUS StartQuicServer(ogs_quic_context_t *ServerCtx, const char *alpn, const char *app_name, const char *bind_address, uint16_t port)
{
    ogs_quic_context_t *ctx = ogs_quic_self();
    QUIC_STATUS Status;

    // 1. Open MsQuic API Table
    if (QUIC_FAILED(Status = MsQuicOpen2(&ctx->MsQuic)))
    {
        ogs_error("MsQuicOpen2 failed: 0x%x\n", Status);
        return Status;
    }

    // 2. Open Registration
    QUIC_REGISTRATION_CONFIG RegConfig = {app_name, QUIC_EXECUTION_PROFILE_LOW_LATENCY};
    if (QUIC_FAILED(Status = ctx->MsQuic->RegistrationOpen(&RegConfig, &ServerCtx->Registration)))
    {
        ogs_error("Registration open failed: 0x%x\n", Status);
        return Status;
    }

    // 3. Setup ALPN and Settings
    QUIC_BUFFER AlpnBuffer = {(uint32_t)strlen(alpn), (uint8_t *)alpn};
    QUIC_SETTINGS Settings = {0};

    Settings.PeerBidiStreamCount = 100;
    Settings.PeerUnidiStreamCount = 100;
    Settings.DatagramReceiveEnabled = 1;
    Settings.IdleTimeoutMs = 0;
    Settings.KeepAliveIntervalMs = 25000; // Ping every 25 seconds

    // Note: In C, we use '1' instead of 'true' for bitfields
    Settings.IsSet.PeerBidiStreamCount = 1;
    Settings.IsSet.PeerUnidiStreamCount = 1;
    Settings.IsSet.DatagramReceiveEnabled = 1;
    Settings.IsSet.IdleTimeoutMs = 1;
    Settings.IsSet.KeepAliveIntervalMs = 1;

    Settings.ServerResumptionLevel = QUIC_SERVER_RESUME_AND_ZERORTT;
    Settings.IsSet.ServerResumptionLevel = 1;

    // 4. Open Configuration (Passing ServerCtx instead of 'this')
    if (QUIC_FAILED(Status = ctx->MsQuic->ConfigurationOpen(
                        ServerCtx->Registration, &AlpnBuffer, 1, &Settings, sizeof(Settings),
                        ServerCtx, &ServerCtx->Configuration)))
    {
        ogs_error("Configuration open failed: 0x%x\n", Status);
        return Status;
    }

    // 5. Load Credentials (Certificates)
    QUIC_CREDENTIAL_CONFIG CredConfig = {0};
    const char *key_path = upf_self()->quic_key_path;
    const char *cert_path = upf_self()->quic_cert_path;
    QUIC_CERTIFICATE_FILE CertFile = {key_path, cert_path};

    CredConfig.Type = QUIC_CREDENTIAL_TYPE_CERTIFICATE_FILE;
    CredConfig.CertificateFile = &CertFile;
    CredConfig.Flags = QUIC_CREDENTIAL_FLAG_NONE;

    ogs_info("[quic] Loading certificate from: %s and %s\n", cert_path, key_path);

    if (QUIC_FAILED(Status = ctx->MsQuic->ConfigurationLoadCredential(ServerCtx->Configuration, &CredConfig)))
    {
        ogs_error("[quic] Configuration load failed with status: 0x%x\n", Status);
        return Status;
    }
    ogs_info("[quic] Configuration loaded successfully\n");

    // 6. Open Listener
    if (QUIC_FAILED(Status = ctx->MsQuic->ListenerOpen(
                        ServerCtx->Registration, ListnerCallback, ServerCtx, &ServerCtx->Listener)))
    {
        ogs_error("Listener open failed: 0x%x\n", Status);
        return Status;
    }

    QUIC_ADDR Address = {0};
    struct sockaddr_in *AddrV4 = (struct sockaddr_in *)&Address;
    AddrV4->sin_family = AF_INET;
    AddrV4->sin_port = htons(port);

    if (inet_pton(AF_INET, bind_address, &AddrV4->sin_addr) != 1)
    {
        ogs_error("[QUIC N3] Invalid IPv4 bind address in YAML: %s", bind_address);
        return QUIC_STATUS_INVALID_PARAMETER; // Exit safely!
    }

    if (QUIC_FAILED(Status = ctx->MsQuic->ListenerStart(ServerCtx->Listener, &AlpnBuffer, 1, &Address)))
    {
        ogs_error("Listener start failed: 0x%x\n", Status);
        return Status;
    }

    return QUIC_STATUS_SUCCESS;
}

void StopQuicServer(ogs_quic_context_t *ServerCtx)
{
    ogs_quic_context_t *ctx = ogs_quic_self();
    // Check if the API table exists
    if (ctx->MsQuic != NULL)
    {
        // 1. Stop and Close the Listener
        if (ServerCtx->Listener != NULL)
        {
            ctx->MsQuic->ListenerStop(ServerCtx->Listener);
            ctx->MsQuic->ListenerClose(ServerCtx->Listener);
            ServerCtx->Listener = NULL;
        }

        // 2. Close the active Connection (if one exists)
        if (ServerCtx->Connection != NULL)
        {
            ctx->MsQuic->ConnectionClose(ServerCtx->Connection);
            ServerCtx->Connection = NULL;
        }

        // 3. Close the Configuration
        if (ServerCtx->Configuration != NULL)
        {
            ctx->MsQuic->ConfigurationClose(ServerCtx->Configuration);
            ServerCtx->Configuration = NULL;
        }

        // 4. Close the Registration
        if (ServerCtx->Registration != NULL)
        {
            ctx->MsQuic->RegistrationClose(ServerCtx->Registration);
            ServerCtx->Registration = NULL;
        }

        // 5. Close the msquic library
        MsQuicClose(ctx->MsQuic);
        ctx->MsQuic = NULL;

        ogs_info("[quic] Server resources cleanly destroyed.\n");
    }
}

void quic_server_send_downlink(uint32_t teid, uint8_t *packet_data, uint16_t packet_len)
{
    ogs_quic_context_t *ctx = ogs_quic_self();

    if (!ctx || !ctx->MsQuic || !ctx->active_client_connection)
    {
        ogs_warn("QUIC Downlink dropped: No active client connected.");
        return;
    }

    if (!packet_data || packet_len == 0 || packet_len > 2048)
    {
        ogs_warn("QUIC Downlink dropped: Invalid packet data.");
        return;
    }

    const uint16_t header_len = sizeof(uint32_t);
    const uint16_t total_len = header_len + packet_len;

    quic_send_context_t *send_buffer = (quic_send_context_t *)malloc(sizeof(quic_send_context_t));
    if (!send_buffer)
    {
        ogs_error("QUIC Downlink dropped: Out of memory (malloc failed).");
        return;
    }

    // 1. Write the TEID into the first 4 bytes (in Network Byte Order)
    uint32_t network_teid = htonl(teid);
    memcpy(send_buffer->data_space, &network_teid, header_len);
    memcpy(send_buffer->data_space + header_len, packet_data, packet_len);

    send_buffer->buffer.Length = total_len;
    send_buffer->buffer.Buffer = send_buffer->data_space;

    QUIC_STATUS Status = ctx->MsQuic->DatagramSend(
        ctx->active_client_connection,
        &send_buffer->buffer,
        1,
        QUIC_SEND_FLAG_NONE,
        send_buffer);

    if (QUIC_FAILED(Status))
    {
        ogs_error("[QUIC DOWNLINK] DatagramSend failed! 0x%x", Status);
        free(send_buffer);
    }
    else
    {
        ogs_debug("QUIC Downlink Sent: TEID=0x%x, Len=%u", teid, packet_len);
    }
}

void quic_server_handle_uplink(const QUIC_BUFFER *buffer)
{
    if (buffer->Length < 4)
    {
        ogs_warn("QUIC Uplink dropped: Datagram too small.");
        return;
    }

    // 2. Slice off the TEID (The first 4 bytes)
    uint32_t network_teid;
    memcpy(&network_teid, buffer->Buffer, sizeof(uint32_t));
    uint32_t teid = ntohl(network_teid); // Convert back to normal CPU architecture!

    // 3. Find the actual IP Packet
    const uint8_t *ip_packet = buffer->Buffer + 4; // Skip the 4-byte TEID
    uint32_t ip_packet_len = buffer->Length - 4;   // Subtract the TEID size from the total length

    // 4. Create an Open5GS container with headroom reserved for GTP-U header
    //    ogs_pfcp_up_handle_pdr always calls ogs_gtp2_encapsulate_header which
    //    uses ogs_pkbuf_push to prepend a GTP-U header. Without headroom that
    //    call fatally asserts. OGS_TUN_MAX_HEADROOM (16 bytes) is sufficient.
    ogs_pkbuf_t *pkbuf = ogs_pkbuf_alloc(NULL, OGS_TUN_MAX_HEADROOM + ip_packet_len);
    if (!pkbuf)
    {
        ogs_error("QUIC Uplink: Failed to allocate Open5GS pkbuf!");
        return;
    }
    ogs_pkbuf_reserve(pkbuf, OGS_TUN_MAX_HEADROOM);

    // 5. Copy the IP packet into the Open5GS container
    // ogs_pkbuf_put_data safely copies the bytes and sets pkbuf->len
    ogs_pkbuf_put_data(pkbuf, ip_packet, ip_packet_len);

    ogs_info("QUIC Uplink Received: TEID=0x%x, Len=%u", teid, ip_packet_len);

    // 6. Push it onto the Open5GS conveyor belt
    upf_n3_route_uplink(teid, pkbuf);
}

// Server Connection Callback
QUIC_STATUS QUIC_API ConnectionCallback(HQUIC Conn, void *Context, QUIC_CONNECTION_EVENT *Event)
{
    ogs_quic_context_t *ctx = ogs_quic_self();
    switch (Event->Type)
    {
    case QUIC_CONNECTION_EVENT_CONNECTED:
        if (Event->CONNECTED.SessionResumed)
        {
            ogs_debug("[quic] Connection established! (RESUMED 0-RTT/1-RTT)\n");
        }
        else
        {
            ogs_debug("[quic] Connection established! (FULL HANDSHAKE)\n");
            ogs_debug("[quic] Sending resumption ticket...\n");
            QUIC_STATUS status = ctx->MsQuic->ConnectionSendResumptionTicket(Conn, QUIC_SEND_RESUMPTION_FLAG_NONE, 0, NULL);
            ogs_info("%d", status);
        }
        ctx->active_client_connection = Conn;
        break;
    case QUIC_CONNECTION_EVENT_SHUTDOWN_INITIATED_BY_TRANSPORT:
        ogs_error("[quic] Connection shutdown by transport. Error Code: 0x%llx (%" PRIu64 ")\n",
                  (unsigned long long)Event->SHUTDOWN_INITIATED_BY_TRANSPORT.ErrorCode,
                  Event->SHUTDOWN_INITIATED_BY_TRANSPORT.ErrorCode);
        break;
    case QUIC_CONNECTION_EVENT_SHUTDOWN_INITIATED_BY_PEER:
        ogs_info("[quic] Terminated by peer.\n");
        break;
    case QUIC_CONNECTION_EVENT_SHUTDOWN_COMPLETE:
        ctx->active_client_connection = NULL; // Prevent other threads from using it
        ctx->MsQuic->ConnectionClose(Conn);   // Now safely destroy it
        ogs_info("[quic] Connection Closed.\n");
        break;
    case QUIC_CONNECTION_EVENT_LOCAL_ADDRESS_CHANGED:
        ogs_info("[quic] Local address Changed.\n");
        break;
    case QUIC_CONNECTION_EVENT_PEER_ADDRESS_CHANGED:
        ogs_info("[quic] peer address changed.\n");
        break;
    case QUIC_CONNECTION_EVENT_PEER_STREAM_STARTED:
        ogs_info("[quic] Stream Started by Peer.\n");
        break;
    case QUIC_CONNECTION_EVENT_STREAMS_AVAILABLE:
        ogs_info("[quic] Streams Available.\n");
        break;
    case QUIC_CONNECTION_EVENT_PEER_NEEDS_STREAMS:
        ogs_info("[quic] Streams Need to be available.");
        break;
    case QUIC_CONNECTION_EVENT_IDEAL_PROCESSOR_CHANGED:
        ogs_info("[quic] Processor Changed Ideal Processor.");
        break;
    case QUIC_CONNECTION_EVENT_DATAGRAM_STATE_CHANGED:
    {
        ogs_info("[quic] Datagram State Changed. Max Send Length: %u",
                 Event->DATAGRAM_STATE_CHANGED.MaxSendLength);
        break;
    }
    case QUIC_CONNECTION_EVENT_DATAGRAM_RECEIVED:
    {
        const QUIC_BUFFER *Buffer = Event->DATAGRAM_RECEIVED.Buffer;
        ogs_info("MsQuic Server: DATAGRAM RECEIVED! Length: %d", Event->DATAGRAM_RECEIVED.Buffer->Length);
        quic_server_handle_uplink(Buffer);
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
            if (Event->DATAGRAM_SEND_STATE_CHANGED.ClientContext)
            {
                free(Event->DATAGRAM_SEND_STATE_CHANGED.ClientContext);
                Event->DATAGRAM_SEND_STATE_CHANGED.ClientContext = NULL;
            }
        }

        break;
    }
    case QUIC_CONNECTION_EVENT_RESUMED:
        ogs_info("[quic] Resumed.");
        break;
    case QUIC_CONNECTION_EVENT_RESUMPTION_TICKET_RECEIVED:
        ogs_info("[quic] Resumption Ticket Received.");
        break;
    case QUIC_CONNECTION_EVENT_PEER_CERTIFICATE_RECEIVED:
        ogs_info("[quic] Peer Certificate Received.");
        break;
    }
    return QUIC_STATUS_SUCCESS;
}

// Server Listener Callback
QUIC_STATUS QUIC_API ListnerCallback(HQUIC Listener, void *Context, QUIC_LISTENER_EVENT *Event)
{
    ogs_quic_context_t *ctx = ogs_quic_self();
    switch (Event->Type)
    {
    case QUIC_LISTENER_EVENT_NEW_CONNECTION:
    {
        ogs_info("[quic] New Connection from Client\n");
        ctx->MsQuic->SetCallbackHandler(Event->NEW_CONNECTION.Connection, (void *)ConnectionCallback, Context);
        const QUIC_STATUS status = ctx->MsQuic->ConnectionSetConfiguration(Event->NEW_CONNECTION.Connection, ctx->Configuration);
        if (QUIC_FAILED(status))
        {
            ogs_error("[quic] ConnectionSetConfiguration failed: 0x%x\n", status);
        }
        else
        {
            ogs_info("[quic] Connection configured successfully.\n");
        }
        return status;
    }
    case QUIC_LISTENER_EVENT_STOP_COMPLETE:
        ogs_info("[quic] Stopped listening for connections.");
        break;

    case QUIC_LISTENER_EVENT_DOS_MODE_CHANGED:
        ogs_info("[quic] Dos mode changed.\n");
        break;
    }

    return QUIC_STATUS_SUCCESS;
}
