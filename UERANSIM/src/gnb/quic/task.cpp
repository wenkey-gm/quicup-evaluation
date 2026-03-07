#include "task.hpp"
#include "gnb/nts.hpp"
#include "utils/network.hpp"
#include "utils/octet_string.hpp"
#include <cstddef>
#include <msquic.h>

using namespace std;

namespace nr::gnb
{

struct SendContext;

class SendContextPool
{

unique_ptr<uint8_t[]> m_rawMemory;
vector<void*> m_pool;
mutex m_mutex;
size_t m_capacity;
size_t m_blockSize;

SendContextPool();

public:
    static SendContextPool& getInstance()
    {
        static SendContextPool instance;
        return instance;
    }

    void* allocate(size_t size)
    {
        if (size != m_blockSize) return ::operator new(size);

        lock_guard<mutex> lock(m_mutex);
        if (m_pool.empty()) {
            return ::operator new(size);
        }

        void* ptr = m_pool.back();
        m_pool.pop_back();
        return ptr;
    }

    void deallocate(void* ptr)
    {
        auto* rawPtr = static_cast<uint8_t*>(ptr);
        uint8_t* blockStart = m_rawMemory.get();
        uint8_t* blockEnd = blockStart + (m_capacity * m_blockSize);

        if (rawPtr >= blockStart && rawPtr < blockEnd) {
            lock_guard<mutex> lock(m_mutex);
            m_pool.push_back(ptr);
        } else {
            ::operator delete(ptr);
        }
    }
};

struct SendContext{
    QUIC_BUFFER quicBuffer{};
    OctetString payload{};

    SendContext(NmGnbGtpToQuic* data) : payload(std::move(data->data))
    {
        quicBuffer.Length = static_cast<uint32_t>(payload.length());
        quicBuffer.Buffer = const_cast<uint8_t*>(payload.data());
    }

    void* operator new(size_t size) {
        return SendContextPool::getInstance().allocate(size);
    }

    void operator delete(void* ptr) {
        SendContextPool::getInstance().deallocate(ptr);
    }
};


SendContextPool::SendContextPool()
: m_capacity(4096), m_blockSize(sizeof(SendContext))
{
    m_rawMemory = std::make_unique<uint8_t[]>(m_capacity * m_blockSize);
    m_pool.reserve(m_capacity);

    for (size_t i = 0; i < m_capacity; ++i) {
        m_pool.push_back(&m_rawMemory[i * m_blockSize]);
    }
}


QuicTask::QuicTask(TaskBase *base) : m_base{base}
{
    m_logger = m_base->logBase->makeUniqueLogger("quic");
}


void QuicTask::onStart()
{
    const char *alpn = "n3-quic";
    const char *appName = "n3-quic";

    if (QUIC_FAILED(status=MsQuicOpen2(&m_msQuicApi)))
    {
        m_logger->err("MsQuicOpen2 failed: 0x%x\n", status);
        return;
    }

    QUIC_BUFFER alpnBuffer = {static_cast<uint32_t>(strlen(alpn)),
                              reinterpret_cast<uint8_t *>(const_cast<char *>(alpn))};
    QUIC_REGISTRATION_CONFIG regConfig = {appName, QUIC_EXECUTION_PROFILE_LOW_LATENCY};

    if (QUIC_FAILED(status=m_msQuicApi->RegistrationOpen(&regConfig, &m_registration)))
    {
        m_logger->err("RegistrationOpen failed: 0x%x\n", status);
        return;
    }

    QUIC_SETTINGS settings{};
    settings.IdleTimeoutMs = 0;
    settings.KeepAliveIntervalMs = 25000;
    settings.DatagramReceiveEnabled = true;


    settings.IsSet.IdleTimeoutMs = true;
    settings.IsSet.KeepAliveIntervalMs = true;
    settings.IsSet.DatagramReceiveEnabled = true;

    if (QUIC_FAILED(status = m_msQuicApi->ConfigurationOpen(m_registration, &alpnBuffer, 1, &settings, sizeof(settings), nullptr,
                                                   &m_configuration)))
    {
        m_logger->err("ConfigurationOpen failed: 0x%x\n", status);
        return;
    }

    QUIC_CREDENTIAL_CONFIG credConfig{};
    credConfig.Type = QUIC_CREDENTIAL_TYPE_NONE;
    credConfig.Flags = QUIC_CREDENTIAL_FLAG_CLIENT | QUIC_CREDENTIAL_FLAG_NO_CERTIFICATE_VALIDATION;

    if (QUIC_FAILED(status = m_msQuicApi->ConfigurationLoadCredential(m_configuration, &credConfig)))
    {
        m_logger->err("Configuration LoadCredential failed  0x%x\n", status);
        return;
    }
}

void QuicTask::connect(const InetAddress &to){
    if (QUIC_FAILED(status = m_msQuicApi->ConnectionOpen(m_registration, connectionCallback, this, &m_connection)))
    {
        m_logger->err("ConnectionOpen failed, 0x%x\n", status);
        return;
    }

    if (m_savedResumptionTicketLength > 0)
    {
        if (QUIC_FAILED(status = m_msQuicApi->SetParam(m_connection, QUIC_PARAM_CONN_RESUMPTION_TICKET,
                                                   m_savedResumptionTicketLength,
                                                   m_savedResumptionTicket)))
            m_logger->err("Failed to set resumption ticket: 0x%x", status);
    }

    if (QUIC_FAILED(status = m_msQuicApi->ConnectionStart(m_connection, m_configuration, QUIC_ADDRESS_FAMILY_INET,
                                                 to.toString().c_str(), to.getPort())))
    {
        m_logger->err("ConnectionStart failed, 0x%x\n", status);
        return;
    }
}

void QuicTask::onLoop()
{
    auto msg = take();
    if (!msg)
        return;

    switch (msg->msgType)
    {
    case NtsMessageType::GNB_GTP_TO_QUIC: {
        send(&dynamic_cast<NmGnbGtpToQuic &>(*msg));
        break;
    }
    default:
        m_logger->unhandledNts(*msg);
        break;
    }
}

void QuicTask::onQuit()
{
    m_isQuitting = true;

    if (m_connection){
        m_msQuicApi->ConnectionShutdown(m_connection, QUIC_CONNECTION_SHUTDOWN_FLAG_NONE, 0);
    }
    if (m_configuration){
        m_msQuicApi->ConfigurationClose(m_configuration);
        m_configuration = nullptr;
    }
    if (m_registration){
        m_msQuicApi->RegistrationClose(m_registration);
        m_registration = nullptr;
    }
    if (m_msQuicApi){
        MsQuicClose(m_msQuicApi);
        m_msQuicApi=nullptr;
    }
}

void QuicTask::send(NmGnbGtpToQuic* w)
{
    if (!m_isConnection)
    {
        m_isConnection = true;
        connect(w->ip);
        return;
    }

    auto* ctx = new SendContext(w);

    if (QUIC_FAILED(status=m_msQuicApi->DatagramSend(m_connection, &ctx->quicBuffer, 1, QUIC_SEND_FLAG_NONE, ctx)))
    {
        m_logger->err("DatagramSend failed 0x%x\n", status);
        delete ctx;
        return;
    }
}

QUIC_STATUS QUIC_API QuicTask::connectionCallback(HQUIC conn, void *context, QUIC_CONNECTION_EVENT *event)
{
    auto *ctx = static_cast<QuicTask *>(context);

    switch (event->Type)
    {
    case QUIC_CONNECTION_EVENT_CONNECTED:
        if (event->CONNECTED.SessionResumed)
            ctx->m_logger->debug("QUIC connection resumed (0-RTT)");
        else
            ctx->m_logger->debug("new QUIC connection established");
        break;

    case QUIC_CONNECTION_EVENT_SHUTDOWN_INITIATED_BY_TRANSPORT:
        ctx->m_logger->debug("QUIC shutdown by transport, status: 0x%x", event->SHUTDOWN_INITIATED_BY_TRANSPORT.Status);
        break;

    case QUIC_CONNECTION_EVENT_SHUTDOWN_INITIATED_BY_PEER:
        ctx->m_logger->debug("QUIC shutdown by peer");
        break;

    case QUIC_CONNECTION_EVENT_SHUTDOWN_COMPLETE:
        ctx->m_logger->debug("QUIC shutdown complete");
        if (ctx->m_connection)
        {
            ctx->m_msQuicApi->ConnectionClose(ctx->m_connection);
            ctx->m_connection = nullptr;
        }
        ctx->m_isConnection = false;
        break;

    case QUIC_CONNECTION_EVENT_DATAGRAM_STATE_CHANGED:
        ctx->m_logger->debug("QUIC datagram state changed, max send length: %u",
                             event->DATAGRAM_STATE_CHANGED.MaxSendLength);
        break;

    case QUIC_CONNECTION_EVENT_DATAGRAM_RECEIVED: {
        const QUIC_BUFFER *buf = event->DATAGRAM_RECEIVED.Buffer;
        auto w = std::make_unique<NmGnbQuicToGtp>();
        w->data = OctetString::FromArray(buf->Buffer, buf->Length);
        ctx->m_base->gtpTask->push(std::move(w));
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
            }
        }
        break;
    }

    case QUIC_CONNECTION_EVENT_RESUMPTION_TICKET_RECEIVED: {
        uint32_t len = event->RESUMPTION_TICKET_RECEIVED.ResumptionTicketLength;
        const uint8_t *data = event->RESUMPTION_TICKET_RECEIVED.ResumptionTicket;
        if (len > sizeof(ctx->m_savedResumptionTicket)) {
                ctx->m_logger->err("QUIC resumption ticket too large! (%u bytes)", len);
                break;
        }
        std::memcpy(ctx->m_savedResumptionTicket, data, len);
        ctx->m_savedResumptionTicketLength = len;
        break;
    }
    default:
        break;
    }

    return QUIC_STATUS_SUCCESS;
}

} // namespace nr::gnb
