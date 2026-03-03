#include "task.hpp"

#include <gnb/gtp/task.hpp>
#include <gnb/nts.hpp>
#include <utils/constants.hpp>

#include <chrono>
#include <cstring>
#include <thread>

namespace nr::gnb
{

struct SendContext
{
    QUIC_BUFFER quicBuffer{};
    std::vector<uint8_t> data{};
};

QuicTask::QuicTask(TaskBase *base) : m_base{base}
{
    m_logger = m_base->logBase->makeUniqueLogger("quic");
}

void QuicTask::onStart()
{
    const char *alpn = "n3-quic";
    const char *appName = "n3-quic";

    if (QUIC_FAILED(MsQuicOpen2(&m_msQuicApi)))
    {
        m_logger->err("MsQuicOpen2 failed");
        return;
    }

    QUIC_BUFFER alpnBuffer = {static_cast<uint32_t>(strlen(alpn)),
                              reinterpret_cast<uint8_t *>(const_cast<char *>(alpn))};
    QUIC_REGISTRATION_CONFIG regConfig = {appName, QUIC_EXECUTION_PROFILE_LOW_LATENCY};

    if (QUIC_FAILED(m_msQuicApi->RegistrationOpen(&regConfig, &m_registration)))
    {
        m_logger->err("RegistrationOpen failed");
        return;
    }

    QUIC_SETTINGS settings = {};
    settings.DatagramReceiveEnabled = 1;
    settings.IsSet.DatagramReceiveEnabled = 1;
    settings.IdleTimeoutMs = 0; // 0 = infinite
    settings.IsSet.IdleTimeoutMs = 1;
    settings.KeepAliveIntervalMs = 25000;
    settings.IsSet.KeepAliveIntervalMs = 1;

    if (QUIC_FAILED(m_msQuicApi->ConfigurationOpen(m_registration, &alpnBuffer, 1, &settings, sizeof(settings), nullptr,
                                                   &m_configuration)))
    {
        m_logger->err("ConfigurationOpen failed");
        return;
    }

    QUIC_CREDENTIAL_CONFIG credConfig = {};
    credConfig.Type = QUIC_CREDENTIAL_TYPE_NONE;
    credConfig.Flags = QUIC_CREDENTIAL_FLAG_CLIENT | QUIC_CREDENTIAL_FLAG_NO_CERTIFICATE_VALIDATION;

    if (QUIC_FAILED(m_msQuicApi->ConfigurationLoadCredential(m_configuration, &credConfig)))
    {
        m_logger->err("ConfigurationLoadCredential failed");
        return;
    }

    connect();
}

void QuicTask::onLoop()
{
    auto msg = take();
    if (!msg)
        return;

    switch (msg->msgType)
    {
    case NtsMessageType::GNB_GTP_TO_QUIC: {
        auto &w = dynamic_cast<NmGnbGtpToQuic &>(*msg);
        send(w.data.data(), w.data.length());
        m_logger->info("Sending QUIC Uplink: %zu, %p", w.data.length(), w.data.data());
        break;
    }
    default:
        m_logger->unhandledNts(*msg);
        break;
    }
}

void QuicTask::onQuit()
{
    if (m_connection)
    {
        m_msQuicApi->ConnectionShutdown(m_connection, QUIC_CONNECTION_SHUTDOWN_FLAG_NONE, 0);
        m_connection = nullptr;
    }
    if (m_configuration)
    {
        m_msQuicApi->ConfigurationClose(m_configuration);
        m_configuration = nullptr;
    }
    if (m_registration)
    {
        m_msQuicApi->RegistrationClose(m_registration);
        m_registration = nullptr;
    }
    if (m_msQuicApi)
    {
        MsQuicClose(m_msQuicApi);
        m_msQuicApi = nullptr;
    }
}

void QuicTask::connect()
{
    m_logger->info("Opening QUIC connection to %s:%d", m_base->config->quicIp.c_str(), cons::QuicPort);

    if (QUIC_FAILED(m_msQuicApi->ConnectionOpen(m_registration, connectionCallback, this, &m_connection)))
    {
        m_logger->err("ConnectionOpen failed");
        return;
    }

    if (!m_savedResumptionTicket.empty())
    {
        m_logger->info("Injecting saved resumption ticket (%zu bytes)", m_savedResumptionTicket.size());
        QUIC_STATUS status = m_msQuicApi->SetParam(m_connection, QUIC_PARAM_CONN_RESUMPTION_TICKET,
                                                   static_cast<uint32_t>(m_savedResumptionTicket.size()),
                                                   m_savedResumptionTicket.data());
        if (QUIC_FAILED(status))
            m_logger->err("Failed to set resumption ticket: 0x%x", status);
    }

    if (QUIC_FAILED(m_msQuicApi->ConnectionStart(m_connection, m_configuration, QUIC_ADDRESS_FAMILY_INET,
                                                 m_base->config->quicIp.c_str(), cons::QuicPort)))
    {
        m_logger->err("ConnectionStart failed");
        return;
    }

    m_logger->info("QUIC connection start initiated");
}

void QuicTask::send(const uint8_t *data, size_t length)
{
    if (!m_connection)
    {
        m_logger->warn("QUIC uplink dropped: not connected yet");
        return;
    }

    m_logger->info("sending data %zu bytes", length);
    auto *ctx = new SendContext();
    ctx->data.assign(data, data + length);
    ctx->quicBuffer.Length = static_cast<uint32_t>(ctx->data.size());
    ctx->quicBuffer.Buffer = ctx->data.data();

    if (QUIC_FAILED(m_msQuicApi->DatagramSend(m_connection, &ctx->quicBuffer, 1, QUIC_SEND_FLAG_NONE, ctx)))
    {
        delete ctx;
        m_logger->err("DatagramSend failed");
    }
}

QUIC_STATUS QUIC_API QuicTask::connectionCallback(HQUIC conn, void *context, QUIC_CONNECTION_EVENT *event)
{
    auto *self = static_cast<QuicTask *>(context);

    switch (event->Type)
    {
    case QUIC_CONNECTION_EVENT_CONNECTED:
        if (event->CONNECTED.SessionResumed)
            self->m_logger->info("QUIC connection resumed (0-RTT)");
        else
            self->m_logger->info("QUIC connection established");
        break;

    case QUIC_CONNECTION_EVENT_SHUTDOWN_INITIATED_BY_TRANSPORT:
        self->m_logger->info("QUIC shutdown by transport, status: 0x%x", event->SHUTDOWN_INITIATED_BY_TRANSPORT.Status);
        break;

    case QUIC_CONNECTION_EVENT_SHUTDOWN_INITIATED_BY_PEER:
        self->m_logger->info("QUIC shutdown by peer");
        break;

    case QUIC_CONNECTION_EVENT_SHUTDOWN_COMPLETE:
        self->m_logger->info("QUIC shutdown complete");
        if (self->m_connection)
        {
            self->m_msQuicApi->ConnectionClose(self->m_connection);
            self->m_connection = nullptr;
        }
        // Reconnect after a short delay so the UPF has time to come back up
        std::thread([self]() {
            std::this_thread::sleep_for(std::chrono::seconds(2));
            self->m_logger->info("QUIC attempting reconnect...");
            self->connect();
        }).detach();
        break;

    case QUIC_CONNECTION_EVENT_DATAGRAM_STATE_CHANGED:
        self->m_logger->info("QUIC datagram state changed, max send length: %u",
                             event->DATAGRAM_STATE_CHANGED.MaxSendLength);
        break;

    case QUIC_CONNECTION_EVENT_DATAGRAM_RECEIVED: {
        const QUIC_BUFFER *buf = event->DATAGRAM_RECEIVED.Buffer;
        auto w = std::make_unique<NmGnbQuicToGtp>();
        w->data = OctetString::FromArray(buf->Buffer, buf->Length);
        self->m_base->gtpTask->push(std::move(w));
        break;
    }

    case QUIC_CONNECTION_EVENT_DATAGRAM_SEND_STATE_CHANGED: {
        auto state = event->DATAGRAM_SEND_STATE_CHANGED.State;
        auto *sentBuffer = static_cast<SendContext *>(event->DATAGRAM_SEND_STATE_CHANGED.ClientContext);
        if (state == QUIC_DATAGRAM_SEND_ACKNOWLEDGED || state == QUIC_DATAGRAM_SEND_ACKNOWLEDGED_SPURIOUS ||
            state == QUIC_DATAGRAM_SEND_CANCELED || state == QUIC_DATAGRAM_SEND_LOST_DISCARDED)
        {
            if (sentBuffer != nullptr)
            {
                delete sentBuffer;
                // Nullify to be absolutely safe
                event->DATAGRAM_SEND_STATE_CHANGED.ClientContext = nullptr;
            }
        }
        break;
    }

    case QUIC_CONNECTION_EVENT_RESUMPTION_TICKET_RECEIVED: {
        uint32_t len = event->RESUMPTION_TICKET_RECEIVED.ResumptionTicketLength;
        const uint8_t *data = event->RESUMPTION_TICKET_RECEIVED.ResumptionTicket;
        self->m_logger->info("QUIC resumption ticket received (%u bytes)", len);
        self->m_savedResumptionTicket.assign(data, data + len);
        break;
    }

    default:
        break;
    }

    return QUIC_STATUS_SUCCESS;
}

} // namespace nr::gnb
