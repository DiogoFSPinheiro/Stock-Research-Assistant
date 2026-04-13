from __future__ import annotations

import logging
from logging import Logger
import threading
import time

from .config import AppConfig, ConfigError
from .instruments import InstrumentFilter
from .market_data import MarketDataError, build_market_data_provider
from .orchestrator import TradingBot
from .risk import RiskEngine
from .storage import JsonStateStore
from .strategy import UndervaluedStockEngine
from .telegram_service import TelegramApiError, TelegramApprovalService


class TerminalStopController:
    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._read_commands, name="terminal-stop-listener", daemon=True)
        self._thread.start()

    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    def stop(self) -> None:
        self._stop_event.set()

    def _read_commands(self) -> None:
        while not self._stop_event.is_set():
            try:
                raw = input()
            except EOFError:
                return
            except KeyboardInterrupt:
                self._stop_event.set()
                return
            command = raw.strip().lower()
            if command in {"stop", "quit", "exit"}:
                self._stop_event.set()
                return


def configure_logging(level: str) -> Logger:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    return logging.getLogger("xtb_trading_bot")


def build_application(config: AppConfig | None = None) -> TradingBot:
    config = config or AppConfig.from_env()
    config.validate()
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
        strategy=UndervaluedStockEngine(state_store),
        risk=RiskEngine(config.risk),
        state_store=state_store,
        logger=logger,
    )


def main() -> int:
    try:
        bot = build_application()
    except ConfigError as exc:
        logger = configure_logging("INFO")
        logger.error("Configuration error: %s", exc)
        return 2

    if hasattr(bot.approvals, "initialize"):
        try:
            bot.approvals.initialize()
        except Exception as exc:  # pragma: no cover - defensive startup guard
            bot.logger.error("Telegram initialization failed: %s", exc)
            return 3

    bot.logger.info(
        "Starting undervalued stock picker with provider=%s, symbols=%s stocks, poll=%ss",
        bot.config.market_data.provider,
        len(bot.config.universe.allowed_stocks),
        bot.config.poll_seconds,
    )
    bot.logger.info("Type `stop`, `quit`, or `exit` then press Enter to stop the bot cleanly.")
    next_scan_at = time.monotonic()
    tip_poll_interval = min(5, max(bot.config.telegram.polling_timeout_seconds, 1))
    stop_controller = TerminalStopController()
    stop_controller.start()
    try:
        while True:
            try:
                if stop_controller.should_stop():
                    bot.logger.info("Signal bot stopped by user.")
                    return 0
                generated = 0
                ran_scheduled_scan = False
                if hasattr(bot.approvals, "poll_commands"):
                    commands = bot.approvals.poll_commands()
                elif hasattr(bot.approvals, "poll_tip_requests"):
                    commands = bot.approvals.poll_tip_requests()
                else:
                    commands = []
                polled = len(commands)
                if not commands and time.monotonic() >= next_scan_at:
                    generated += bot.scan()
                    next_scan_at = time.monotonic() + bot.config.poll_seconds
                    ran_scheduled_scan = True
                for command in commands:
                    kind = getattr(command, "kind", "tip")
                    if kind == "tip":
                        generated += bot.send_tip(
                            chat_id=getattr(command, "chat_id", None),
                            allow_repeat=True,
                            notify_when_empty=True,
                        )
                        continue
                    if kind == "tip_for_symbol":
                        generated += bot.send_tip(
                            chat_id=getattr(command, "chat_id", None),
                            allow_repeat=True,
                            notify_when_empty=True,
                            symbol=getattr(command, "symbol", None),
                        )
                        continue
                    if kind == "top_tips":
                        generated += bot.send_top_tips(
                            chat_id=getattr(command, "chat_id", None),
                            limit=getattr(command, "limit", None) or 5,
                        )
                        continue
                    if kind == "add_stock":
                        try:
                            added, symbol, total = bot.add_stock(getattr(command, "symbol", "") or "")
                            if hasattr(bot.approvals, "publish_text"):
                                message = (
                                    f"Added {symbol} to your stock universe. Total stocks: {total}."
                                    if added
                                    else f"{symbol} is already in your stock universe. Total stocks: {total}."
                                )
                                bot.approvals.publish_text(message, chat_id=getattr(command, "chat_id", None))
                        except ConfigError as exc:
                            if hasattr(bot.approvals, "publish_text"):
                                bot.approvals.publish_text(
                                    f"Unable to add stock: {exc}",
                                    chat_id=getattr(command, "chat_id", None),
                                )
                        continue
                    if kind == "stock_analysis":
                        try:
                            generated += bot.analyze_stock(
                                getattr(command, "symbol", "") or "",
                                chat_id=getattr(command, "chat_id", None),
                            )
                        except ConfigError as exc:
                            if hasattr(bot.approvals, "publish_text"):
                                bot.approvals.publish_text(
                                    f"Unable to analyze stock: {exc}",
                                    chat_id=getattr(command, "chat_id", None),
                                )
                        continue
                    generated += bot.send_tip(
                        chat_id=getattr(command, "chat_id", None),
                        allow_repeat=True,
                        notify_when_empty=True,
                    )
                processed = bot.process_approvals()
                if ran_scheduled_scan or polled or processed or generated:
                    bot.logger.info("Cycle finished. Generated=%s polled=%s processed=%s", generated, polled, processed)
            except KeyboardInterrupt:
                stop_controller.stop()
                bot.logger.info("Signal bot stopped by user.")
                return 0
            except (MarketDataError, TelegramApiError, TimeoutError, OSError) as exc:
                bot.logger.warning("Cycle failed due to runtime error: %s", exc)
            time.sleep(tip_poll_interval)
    except KeyboardInterrupt:
        stop_controller.stop()
        bot.logger.info("Signal bot stopped by user.")
        return 0
    return 0
