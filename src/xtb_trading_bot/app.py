from __future__ import annotations

import logging
from logging import Logger

from .config import AppConfig
from .instruments import InstrumentFilter
from .orchestrator import TradingBot
from .risk import RiskEngine
from .storage import JsonStateStore
from .strategy import TrendSignalEngine
from .telegram_service import TelegramApprovalService
from .xtb_client import XtbClient


def configure_logging(level: str) -> Logger:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return logging.getLogger("xtb_trading_bot")


def build_application(config: AppConfig | None = None) -> TradingBot:
    config = config or AppConfig.from_env()
    logger = configure_logging(config.log_level)
    market_data = XtbClient(
        config.xtb,
        instruments_seed=[*config.universe.allowed_fx, *config.universe.allowed_stocks],
        context_seed=list(config.universe.context_symbols),
    )
    state_store = JsonStateStore(config.storage_path)
    approvals = TelegramApprovalService(config.telegram, config.risk.signal_expiry_minutes)
    approvals.positions_provider = market_data.list_positions
    return TradingBot(
        config=config,
        market_data=market_data,
        execution=market_data,
        approvals=approvals,
        instrument_filter=InstrumentFilter(config.universe),
        strategy=TrendSignalEngine(state_store),
        risk=RiskEngine(config.risk),
        state_store=state_store,
        logger=logger,
    )


def main() -> int:
    bot = build_application()
    if not bot.market_data.login():
        bot.logger.warning("XTB credentials are not configured. Running in dry mode.")
    generated = bot.scan()
    processed = bot.process_approvals()
    bot.logger.info("Scan finished. Generated=%s processed=%s", generated, processed)
    return 0
