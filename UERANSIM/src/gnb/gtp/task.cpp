//
// This file is a part of UERANSIM project.
// Copyright (c) 2023 ALİ GÜNGÖR.
//
// https://github.com/aligungr/UERANSIM/
// See README, LICENSE, and CONTRIBUTING files for licensing details.
//

#include "task.hpp"
#include "gnb/quic/task.hpp"
#include "utils/network.hpp"

#include <algorithm>
#include <gnb/gtp/proto.hpp>
#include <gnb/rls/task.hpp>
#include <utils/constants.hpp>
#include <utils/libc_error.hpp>

#include <asn/ngap/ASN_NGAP_QosFlowSetupRequestItem.h>

namespace nr::gnb
{

GtpTask::GtpTask(TaskBase *base)
    : m_base{base}, m_udpServer{}, m_ueContexts{}, m_rateLimiter(std::make_unique<RateLimiter>()), m_pduSessions{},
      m_sessionTree{}
{
    m_logger = m_base->logBase->makeUniqueLogger("gtp");
}

void GtpTask::onStart()
{
    if (m_base->config->transportMode == GTPU)
    {
        try
        {
            m_udpServer = new udp::UdpServerTask(m_base->config->gtpIp, cons::GtpPort, this);
            m_udpServer->start();
        }
        catch (const LibError &e)
        {
            m_logger->err("GTP/UDP task could not be created. %s", e.what());
        }
    }
}

void GtpTask::onQuit()
{
    if (m_udpServer)
    {
        m_udpServer->quit();
        delete m_udpServer;
        m_udpServer = nullptr;
    }

    m_ueContexts.clear();
}

void GtpTask::onLoop()
{
    auto msg = take();
    if (!msg)
        return;

    switch (msg->msgType)
    {
    case NtsMessageType::GNB_NGAP_TO_GTP: {
        auto &w = dynamic_cast<NmGnbNgapToGtp &>(*msg);
        switch (w.present)
        {
        case NmGnbNgapToGtp::UE_CONTEXT_UPDATE: {
            handleUeContextUpdate(*w.update);
            break;
        }
        case NmGnbNgapToGtp::UE_CONTEXT_RELEASE: {
            handleUeContextDelete(w.ueId);
            break;
        }
        case NmGnbNgapToGtp::SESSION_CREATE: {
            handleSessionCreate(w.resource);
            break;
        }
        case NmGnbNgapToGtp::SESSION_RELEASE: {
            handleSessionRelease(w.ueId, w.psi);
            break;
        }
        }
        break;
    }
    case NtsMessageType::GNB_RLS_TO_GTP: {
        auto &w = dynamic_cast<NmGnbRlsToGtp &>(*msg);
        switch (w.present)
        {
        case NmGnbRlsToGtp::DATA_PDU_DELIVERY:
            handleUplinkData(w.ueId, w.psi, std::move(w.pdu));
            break;
        }
        break;
    }
    case NtsMessageType::UDP_SERVER_RECEIVE:
        handleUdpReceive(dynamic_cast<udp::NwUdpServerReceive &>(*msg));
        break;
    case NtsMessageType::GNB_QUIC_TO_GTP:
        handleQuicReceive(dynamic_cast<NmGnbQuicToGtp &>(*msg));
        break;
    default:
        m_logger->unhandledNts(*msg);
        break;
    }
}

void GtpTask::handleUeContextUpdate(const GtpUeContextUpdate &msg)
{
    if (!m_ueContexts.count(msg.ueId))
        m_ueContexts[msg.ueId] = std::make_unique<GtpUeContext>(msg.ueId);

    auto &ue = m_ueContexts[msg.ueId];
    ue->ueAmbr = msg.ueAmbr;

    updateAmbrForUe(ue->ueId);
}

void GtpTask::handleSessionCreate(PduSessionResource *session)
{
    if (!m_ueContexts.count(session->ueId))
    {
        m_logger->err("PDU session resource could not be created, UE context with ID[%d] not found", session->ueId);
        return;
    }

    uint64_t sessionInd = MakeSessionResInd(session->ueId, session->psi);
    m_pduSessions[sessionInd] = std::unique_ptr<PduSessionResource>(session);

    m_sessionTree.insert(sessionInd, session->downTunnel.teid);

    updateAmbrForUe(session->ueId);
    updateAmbrForSession(sessionInd);
}

void GtpTask::handleSessionRelease(int ueId, int psi)
{
    if (!m_ueContexts.count(ueId))
    {
        m_logger->err("PDU session resource could not be released, UE context with ID[%d] not found", ueId);
        return;
    }

    uint64_t sessionInd = MakeSessionResInd(ueId, psi);

    // Remove all session information from rate limiter
    m_rateLimiter->updateSessionUplinkLimit(sessionInd, 0);
    m_rateLimiter->updateUeDownlinkLimit(ueId, 0);

    // And remove from PDU session table
    if (m_pduSessions.count(sessionInd))
    {
        uint32_t teid = m_pduSessions[sessionInd]->downTunnel.teid;
        m_pduSessions.erase(sessionInd);

        // And remove from the tree
        m_sessionTree.remove(sessionInd, teid);
    }
}

void GtpTask::handleUeContextDelete(int ueId)
{
    // Find PDU sessions of the UE
    std::vector<uint64_t> sessions{};
    m_sessionTree.enumerateByUe(ueId, sessions);

    for (auto &session : sessions)
    {
        // Remove all session information from rate limiter
        m_rateLimiter->updateSessionUplinkLimit(session, 0);
        m_rateLimiter->updateUeDownlinkLimit(ueId, 0);

        // And remove from PDU session table
        uint32_t teid = m_pduSessions[session]->downTunnel.teid;
        m_pduSessions.erase(session);

        // And remove from the tree
        m_sessionTree.remove(session, teid);
    }

    // Remove all user information from rate limiter
    m_rateLimiter->updateUeUplinkLimit(ueId, 0);
    m_rateLimiter->updateUeDownlinkLimit(ueId, 0);

    // Remove UE context
    m_ueContexts.erase(ueId);
}

void GtpTask::handleUplinkData(int ueId, int psi, OctetString &&pdu)
{
    const uint8_t *data = pdu.data();

    // ignore non IPv4 packets
    if ((data[0] >> 4 & 0xF) != 4)
        return;

    uint64_t sessionInd = MakeSessionResInd(ueId, psi);

    if (!m_pduSessions.count(sessionInd))
    {
        m_logger->err("Uplink data failure, PDU session not found. UE[%d] PSI[%d]", ueId, psi);
        return;
    }

    auto &pduSession = m_pduSessions[sessionInd];

    if (m_rateLimiter->allowUplinkPacket(sessionInd, static_cast<int64_t>(pdu.length())))
    {
        if (m_base->config->transportMode == QUIC)
        {
            OctetString quicPdu;
            quicPdu.appendOctet4(pduSession->upTunnel.teid);
            quicPdu.append(pdu);

            auto w = std::make_unique<NmGnbGtpToQuic>();
            w->ip = InetAddress(pduSession->upTunnel.address, cons::QuicPort);
            w->data = std::move(quicPdu);
            m_base->quicTask->push(std::move(w));
        }
        else
        {
            gtp::GtpMessage gtpMsg{};
            gtpMsg.payload = std::move(pdu);
            gtpMsg.msgType = gtp::GtpMessage::MT_G_PDU;
            gtpMsg.teid = pduSession->upTunnel.teid;

            auto ul = std::make_unique<gtp::UlPduSessionInformation>();
            // TODO: currently using first QSI
            ul->qfi = static_cast<int>(pduSession->qosFlows->list.array[0]->qosFlowIdentifier);

            auto cont = std::make_unique<gtp::PduSessionContainerExtHeader>();
            cont->pduSessionInformation = std::move(ul);
            gtpMsg.extHeaders.push_back(std::move(cont));

            OctetString gtpPdu;
            if (!gtp::EncodeGtpMessage(gtpMsg, gtpPdu))
            {
                m_logger->err("Uplink data failure, GTP encoding failed");
                return;
            }

            m_udpServer->send(InetAddress(pduSession->upTunnel.address, cons::GtpPort), gtpPdu);
        }
    }
}

void GtpTask::handleQuicReceive(const NmGnbQuicToGtp &msg)
{
    if (msg.data.length() < 5)
    {
        m_logger->err("QUIC downlink datagram too short (%d bytes), dropping", msg.data.length());
        return;
    }

    OctetView buffer{msg.data};
    uint32_t teid = buffer.read4UI();
    OctetString payload = OctetString::FromArray(msg.data.data() + 4, static_cast<size_t>(msg.data.length() - 4));

    auto sessionInd = m_sessionTree.findByDownTeid(teid);
    if (sessionInd == 0)
    {
        m_logger->err("TEID %u not found on QUIC downlink", teid);
        return;
    }

    if (m_rateLimiter->allowDownlinkPacket(sessionInd, payload.length()))
    {
        auto w = std::make_unique<NmGnbGtpToRls>(NmGnbGtpToRls::DATA_PDU_DELIVERY); // TODO: Rename NmGnbGtpToRls to NmGnbGtpQuicToRls
        w->ueId = GetUeId(sessionInd);
        w->psi = GetPsi(sessionInd);
        w->pdu = std::move(payload);
        m_base->rlsTask->push(std::move(w));
    }
}

void GtpTask::handleUdpReceive(const udp::NwUdpServerReceive &msg)
{
    OctetView buffer{msg.packet};
    auto gtp = gtp::DecodeGtpMessage(buffer);

    switch (gtp->msgType)
    {
    case gtp::GtpMessage::MT_G_PDU: {
        auto sessionInd = m_sessionTree.findByDownTeid(gtp->teid);
        if (sessionInd == 0)
        {
            m_logger->err("TEID %d not found on GTP-U Downlink", gtp->teid);
            return;
        }

        if (m_rateLimiter->allowDownlinkPacket(sessionInd, gtp->payload.length()))
        {
            auto w = std::make_unique<NmGnbGtpToRls>(NmGnbGtpToRls::DATA_PDU_DELIVERY);
            w->ueId = GetUeId(sessionInd);
            w->psi = GetPsi(sessionInd);
            w->pdu = std::move(gtp->payload);
            m_base->rlsTask->push(std::move(w));
        }
        return;
    }
    case gtp::GtpMessage::MT_ECHO_REQUEST: {
        gtp::GtpMessage gtpResponse{};
        gtpResponse.msgType = gtp::GtpMessage::MT_ECHO_RESPONSE;
        gtpResponse.seq = gtp->seq;
        gtpResponse.payload = OctetString::FromOctet2({14, 0});

        OctetString gtpPdu;
        if (gtp::EncodeGtpMessage(gtpResponse, gtpPdu))
            m_udpServer->send(msg.fromAddress, gtpPdu);
        else
            m_logger->err("Uplink data failure, GTP encoding failed");
        return;
    }
    default: {
        m_logger->err("Unhandled GTP-U message type: %d", gtp->msgType);
        return;
    }
    }
}

void GtpTask::updateAmbrForUe(int ueId)
{
    if (!m_ueContexts.count(ueId))
        return;

    auto &ue = m_ueContexts[ueId];
    m_rateLimiter->updateUeUplinkLimit(ueId, ue->ueAmbr.ulAmbr);
    m_rateLimiter->updateUeDownlinkLimit(ueId, ue->ueAmbr.dlAmbr);
}

void GtpTask::updateAmbrForSession(uint64_t pduSession)
{
    if (!m_pduSessions.count(pduSession))
        return;

    auto &sess = m_pduSessions[pduSession];
    m_rateLimiter->updateSessionUplinkLimit(pduSession, sess->sessionAmbr.ulAmbr);
    m_rateLimiter->updateSessionDownlinkLimit(pduSession, sess->sessionAmbr.dlAmbr);
}

} // namespace nr::gnb
