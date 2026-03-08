//
// This file is a part of UERANSIM project.
// Copyright (c) 2023 ALİ GÜNGÖR.
//
// https://github.com/aligungr/UERANSIM/
// See README, LICENSE, and CONTRIBUTING files for licensing details.
//

#pragma once

#include "utils/network.hpp"
#include <gnb/types.hpp>

#include <memory>
#include <msquic.h>
#include <utils/logger.hpp>
#include <gnb/gtp/task.hpp>
#include <gnb/nts.hpp>
#include <utils/constants.hpp>

namespace nr::gnb
{

class QuicTask : public NtsTask
{
  private:
    TaskBase *m_base;
    QUIC_STATUS status{};
    bool m_isQuitting = false;
    std::unique_ptr<Logger> m_logger;

    const QUIC_API_TABLE *m_msQuicApi{};
    HQUIC m_registration{};
    HQUIC m_configuration{};
    HQUIC m_connection{};
    uint8_t m_savedResumptionTicket[4096]{};
    uint32_t m_savedResumptionTicketLength{0};
    bool m_isConnection{false};

    friend class GnbCmdHandler;

  public:
    explicit QuicTask(TaskBase *base);
    ~QuicTask() override = default;

  protected:
    void onStart() override;
    void onLoop() override;
    void onQuit() override;

  private:
    void connect(const InetAddress &to);
    void send(NmGnbGtpToQuic* w);
    static QUIC_STATUS QUIC_API connectionCallback(HQUIC conn, void *context, QUIC_CONNECTION_EVENT *event);
};

} // namespace nr::gnb
