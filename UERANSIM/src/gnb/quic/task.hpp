//
// This file is a part of UERANSIM project.
// Copyright (c) 2023 ALİ GÜNGÖR.
//
// https://github.com/aligungr/UERANSIM/
// See README, LICENSE, and CONTRIBUTING files for licensing details.
//

#pragma once

#include <gnb/types.hpp>

#include <memory>
#include <msquic.h>
#include <utils/logger.hpp>
#include <utils/nts.hpp>
#include <vector>

namespace nr::gnb
{

class QuicTask : public NtsTask
{
  private:
    TaskBase *m_base;
    std::unique_ptr<Logger> m_logger;

    const QUIC_API_TABLE *m_msQuicApi{};
    HQUIC m_registration{};
    HQUIC m_configuration{};
    HQUIC m_connection{};
    std::vector<uint8_t> m_savedResumptionTicket{};

    friend class GnbCmdHandler;

  public:
    explicit QuicTask(TaskBase *base);
    ~QuicTask() override = default;

  protected:
    void onStart() override;
    void onLoop() override;
    void onQuit() override;

  private:
    void connect();
    void send(const uint8_t *data, size_t length);
    static QUIC_STATUS QUIC_API connectionCallback(HQUIC conn, void *context, QUIC_CONNECTION_EVENT *event);
};

} // namespace nr::gnb
