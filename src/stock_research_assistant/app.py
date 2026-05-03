from __future__ import annotations

import logging
from logging import Logger
from datetime import datetime
import threading
import time
from zoneinfo import ZoneInfo

from .config import AppConfig, ConfigError
from .instruments import InstrumentFilter
from .market_data import MarketDataError, build_market_data_provider
from .orchestrator import TradingBot
from .reporting import render_error, render_portfolio_started, render_research_cycle_started, render_watchlist_update
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

    def wait(self, timeout: float) -> bool:
        return self._stop_event.wait(timeout)

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
    return logging.getLogger("stock_research_assistant")


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


def notify_scheduled_scan_start(bot: TradingBot) -> None:
    message = "Top research ideas cycle started. Please wait before sending more requests."
    bot.logger.info(message)
    if hasattr(bot.approvals, "publish_text"):
        try:
            bot.approvals.publish_text(render_research_cycle_started())
        except TelegramApiError as exc:
            bot.logger.warning("Telegram publish failed for cycle start notice: %s", exc)


def notify_bot_stopping(logger: Logger) -> None:
    logger.info("Stopping bot...")


def notify_portfolio_report_start(bot: TradingBot) -> None:
    message = "PORTFOLIO daily report started. Please wait before sending more requests."
    bot.logger.info(message)
    if hasattr(bot.approvals, "publish_text"):
        try:
            bot.approvals.publish_text(render_portfolio_started())
        except TelegramApiError as exc:
            bot.logger.warning("Telegram publish failed for portfolio start notice: %s", exc)


def log_received_command(logger: Logger, command: object) -> None:
    kind = getattr(command, "kind", "tip")
    text = " ".join(str(getattr(command, "text", "")).split()) or kind
    if kind == "top_tips":
        limit = getattr(command, "limit", None) or 5
        logger.info("Received Telegram command: %s. Building research shortlist with limit=%s.", text, limit)
        return
    if kind == "stock_analysis":
        symbol = getattr(command, "symbol", None) or "unknown"
        logger.info("Received Telegram command: %s. Starting company research for %s.", text, symbol)
        return
    if kind == "tip_for_symbol":
        symbol = getattr(command, "symbol", None) or "unknown"
        logger.info("Received Telegram command: %s. Starting quick research check for %s.", text, symbol)
        return
    if kind == "watch_stock":
        symbol = getattr(command, "symbol", None) or "unknown"
        logger.info("Received Telegram command: %s. Adding %s to the watchlist.", text, symbol)
        return
    if kind == "watchlist":
        logger.info("Received Telegram command: %s. Building watchlist view.", text)
        return
    if kind == "compare":
        symbols = ", ".join(getattr(command, "symbols", ()) or ())
        logger.info("Received Telegram command: %s. Comparing %s.", text, symbols)
        return
    if kind == "discover":
        logger.info(
            "Received Telegram command: %s. Discovering market ideas with mode=%s limit=%s.",
            text,
            getattr(command, "mode", None) or "default",
            getattr(command, "limit", None) or 5,
        )
        return
    if kind == "portfolio":
        logger.info("Received Telegram command: %s. Building portfolio daily performance report.", text)
        return
    if kind in {"portfolio_add", "portfolio_update", "portfolio_remove"}:
        symbol = getattr(command, "symbol", None) or "unknown"
        logger.info("Received Telegram command: %s. Updating portfolio for %s.", text, symbol)
        return
    if kind in {"alert_add", "alerts", "alert_remove"}:
        logger.info("Received Telegram command: %s. Managing research alerts.", text)
        return
    if kind == "help":
        logger.info("Received Telegram command: %s. Showing help message.", text)
        return
    logger.info("Received Telegram command: %s.", text)


def should_run_scheduled_scan(bot: TradingBot, now: datetime | None = None) -> bool:
    now = now or datetime.now().astimezone()
    if now.weekday() >= 5:
        return False
    scheduled_today = now.replace(
        hour=bot.config.auto_scan_hour,
        minute=bot.config.auto_scan_minute,
        second=0,
        microsecond=0,
    )
    if now < scheduled_today:
        return False
    today_key = now.date().isoformat()
    return bot.state_store.get_last_scheduled_scan_on() != today_key


def should_run_portfolio_report(bot: TradingBot, now: datetime | None = None) -> bool:
    now = now or datetime.now().astimezone()
    if not getattr(bot, "portfolio_symbols", lambda: ())():
        return False
    ny_now = now.astimezone(ZoneInfo("America/New_York"))
    if ny_now.weekday() >= 5:
        return False
    market_close = ny_now.replace(hour=16, minute=0, second=0, microsecond=0)
    if ny_now < market_close:
        return False
    report_day = ny_now.date().isoformat()
    return bot.state_store.get_last_portfolio_report_on() != report_day


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
        "Starting research assistant with provider=%s, symbols=%s watchlist names, auto_scan=%02d:%02d Mon-Fri",
        bot.config.market_data.provider,
        len(bot.config.universe.allowed_stocks),
        bot.config.auto_scan_hour,
        bot.config.auto_scan_minute,
    )
    bot.logger.info("Type `stop`, `quit`, or `exit` then press Enter to stop the bot cleanly.")
    tip_poll_interval = min(5, max(bot.config.telegram.polling_timeout_seconds, 1))
    stop_controller = TerminalStopController()
    stop_controller.start()
    bot.should_stop = stop_controller.should_stop
    try:
        while True:
            try:
                if stop_controller.should_stop():
                    notify_bot_stopping(bot.logger)
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
                if stop_controller.should_stop():
                    notify_bot_stopping(bot.logger)
                    bot.logger.info("Signal bot stopped by user.")
                    return 0
                polled = len(commands)
                if not commands and should_run_scheduled_scan(bot):
                    notify_scheduled_scan_start(bot)
                    generated += bot.scan()
                    if stop_controller.should_stop():
                        notify_bot_stopping(bot.logger)
                        bot.logger.info("Signal bot stopped by user.")
                        return 0
                    bot.state_store.mark_scheduled_scan_on(datetime.now().astimezone().date().isoformat())
                    ran_scheduled_scan = True
                elif not commands and should_run_portfolio_report(bot):
                    notify_portfolio_report_start(bot)
                    generated += bot.send_portfolio_report()
                    if stop_controller.should_stop():
                        notify_bot_stopping(bot.logger)
                        bot.logger.info("Signal bot stopped by user.")
                        return 0
                    report_day = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
                    bot.state_store.mark_portfolio_report_on(report_day)
                for command in commands:
                    log_received_command(bot.logger, command)
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
                    if kind == "watch_stock":
                        try:
                            added, symbol, total = bot.watch_stock(getattr(command, "symbol", "") or "")
                            if hasattr(bot.approvals, "publish_text"):
                                message = render_watchlist_update(symbol, total, added)
                                bot.approvals.publish_text(message, chat_id=getattr(command, "chat_id", None))
                        except ConfigError as exc:
                            if hasattr(bot.approvals, "publish_text"):
                                bot.approvals.publish_text(
                                    render_error("Unable to update watchlist", exc),
                                    chat_id=getattr(command, "chat_id", None),
                                )
                        continue
                    if kind == "watchlist":
                        generated += bot.send_watchlist(chat_id=getattr(command, "chat_id", None))
                        continue
                    if kind == "compare":
                        generated += bot.compare_stocks(
                            tuple(getattr(command, "symbols", ()) or ()),
                            chat_id=getattr(command, "chat_id", None),
                        )
                        continue
                    if kind == "compare_invalid":
                        generated += bot.compare_stocks((), chat_id=getattr(command, "chat_id", None))
                        continue
                    if kind == "discover":
                        generated += bot.discover_stocks(
                            chat_id=getattr(command, "chat_id", None),
                            limit=getattr(command, "limit", None) or 5,
                            mode=getattr(command, "mode", None),
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
                                    render_error("Unable to analyze stock", exc),
                                    chat_id=getattr(command, "chat_id", None),
                                )
                        continue
                    if kind == "portfolio":
                        generated += bot.send_portfolio_report(
                            chat_id=getattr(command, "chat_id", None),
                        )
                        continue
                    if kind == "portfolio_add":
                        generated += bot.add_portfolio_holding(
                            getattr(command, "symbol", "") or "",
                            float(getattr(command, "quantity", 0.0) or 0.0),
                            float(getattr(command, "average_cost", 0.0) or 0.0),
                            chat_id=getattr(command, "chat_id", None),
                        )
                        continue
                    if kind == "portfolio_update":
                        generated += bot.update_portfolio_holding(
                            getattr(command, "symbol", "") or "",
                            float(getattr(command, "quantity", 0.0) or 0.0),
                            float(getattr(command, "average_cost", 0.0) or 0.0),
                            chat_id=getattr(command, "chat_id", None),
                        )
                        continue
                    if kind == "portfolio_remove":
                        generated += bot.remove_portfolio_holding(
                            getattr(command, "symbol", "") or "",
                            chat_id=getattr(command, "chat_id", None),
                        )
                        continue
                    if kind == "portfolio_invalid":
                        generated += bot.send_portfolio_usage(chat_id=getattr(command, "chat_id", None))
                        continue
                    if kind == "alert_add":
                        generated += bot.add_alert(
                            getattr(command, "symbol", "") or "",
                            float(getattr(command, "threshold", 0.0) or 0.0),
                            chat_id=getattr(command, "chat_id", None),
                        )
                        continue
                    if kind == "alerts":
                        generated += bot.send_alerts(chat_id=getattr(command, "chat_id", None))
                        continue
                    if kind == "alert_remove":
                        generated += bot.remove_alert(
                            getattr(command, "symbol", "") or "",
                            chat_id=getattr(command, "chat_id", None),
                        )
                        continue
                    if kind == "alert_invalid":
                        generated += bot.send_alert_usage(chat_id=getattr(command, "chat_id", None))
                        continue
                    if kind == "help":
                        generated += bot.send_help(
                            chat_id=getattr(command, "chat_id", None),
                        )
                        continue
                    generated += bot.send_tip(
                        chat_id=getattr(command, "chat_id", None),
                        allow_repeat=True,
                        notify_when_empty=True,
                    )
                generated += bot.process_alerts()
                processed = bot.process_approvals()
                if ran_scheduled_scan or polled or processed or generated:
                    bot.logger.info("Cycle finished. Generated=%s polled=%s processed=%s", generated, polled, processed)
            except KeyboardInterrupt:
                stop_controller.stop()
                notify_bot_stopping(bot.logger)
                bot.logger.info("Signal bot stopped by user.")
                return 0
            except (MarketDataError, TelegramApiError, TimeoutError, OSError) as exc:
                bot.logger.warning("Cycle failed due to runtime error: %s", exc)
            if stop_controller.wait(tip_poll_interval):
                notify_bot_stopping(bot.logger)
                bot.logger.info("Signal bot stopped by user.")
                return 0
    except KeyboardInterrupt:
        stop_controller.stop()
        notify_bot_stopping(bot.logger)
        bot.logger.info("Signal bot stopped by user.")
        return 0
    return 0
