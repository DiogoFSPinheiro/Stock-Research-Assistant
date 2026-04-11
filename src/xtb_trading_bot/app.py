from __future__ import annotations

import logging
from logging import Logger
import time

from .config import AppConfig
from .instruments import InstrumentFilter
from .market_data import build_market_data_provider
from .orchestrator import TradingBot
from .risk import RiskEngine
from .storage import JsonStateStore
from .strategy import TrendSignalEngine
from .telegram_service import TelegramApprovalService


def configure_logging(level: str) -> Logger:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return logging.getLogger("xtb_trading_bot")


def build_application(config: AppConfig | None = None) -> TradingBot:
    config = config or AppConfig.from_env()
    logger = configure_logging(config.log_level)
    market_data = build_market_data_provider(config.market_data, config.universe)
    state_store = JsonStateStore(config.storage_path)
    approvals = TelegramApprovalService(config.telegram, config.risk.signal_expiry_minutes)
    approvals.positions_provider = market_data.list_positions
    return TradingBot(
        config=config,
        market_data=market_data,
        approvals=approvals,
        instrument_filter=InstrumentFilter(config.universe),
        strategy=TrendSignalEngine(state_store),
        risk=RiskEngine(config.risk),
        state_store=state_store,
        logger=logger,
    )


def main() -> int:
    bot = build_application()
    if hasattr(bot.approvals, "initialize"):
        bot.approvals.initialize()
    while True:
        generated = bot.scan()
        if hasattr(bot.approvals, "poll_updates"):
            polled = len(bot.approvals.poll_updates(reply=True))
        elif hasattr(bot.approvals, "poll_once"):
            polled = bot.approvals.poll_once()
        else:
            polled = 0
        processed = bot.process_approvals()
        bot.logger.info("Cycle finished. Generated=%s polled=%s processed=%s", generated, polled, processed)
        time.sleep(bot.config.poll_seconds)
    return 0
