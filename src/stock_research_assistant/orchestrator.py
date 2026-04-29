from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Callable

from .config import (
    AppConfig,
    ConfigError,
    append_stock_to_universe,
    load_portfolio_holdings,
    load_portfolio_symbols,
    refresh_stock_universe,
    remove_portfolio_holding,
    upsert_portfolio_holding,
)
from .domain import ApprovalStatus, AssetClass, Candle, CompanyResearchReport, PortfolioHolding, SignalSide
from .instruments import InstrumentFilter
from .interfaces import ApprovalService, MarketDataProvider, StateStore
from .market_data import MarketDataError
from .reporting import (
    SEPARATOR,
    format_pct,
    format_price,
    format_signed_price,
    format_signed_pct,
    html_escape,
    render_alert_removed,
    render_alert_triggered,
    render_alert_update,
    render_alerts,
    render_company_report,
    render_compare,
    render_error,
    render_no_strong_setup,
    render_portfolio_update,
    render_portfolio_usage,
    render_quick_research_report,
    render_shortlist,
    render_watchlist,
)
from .risk import RiskEngine, RiskError
from .strategy import UndervaluedStockEngine
from .telegram_service import TelegramApiError


@dataclass
class TradingBot:
    config: AppConfig
    market_data: MarketDataProvider
    approvals: ApprovalService
    instrument_filter: InstrumentFilter
    strategy: UndervaluedStockEngine
    risk: RiskEngine
    state_store: StateStore
    logger: logging.Logger
    should_stop: Callable[[], bool] | None = None

    def _stop_requested(self) -> bool:
        return bool(self.should_stop and self.should_stop())

    def refresh_universe(self) -> int:
        refreshed = refresh_stock_universe(self.config.universe)
        if refreshed == self.config.universe:
            return len(self.config.universe.allowed_stocks)
        self.config = replace(self.config, universe=refreshed)
        self.instrument_filter.config = refreshed
        if hasattr(self.market_data, "universe"):
            self.market_data.universe = refreshed
        return len(refreshed.allowed_stocks)

    def add_stock(self, symbol: str) -> tuple[bool, str, int]:
        validated_symbol = symbol.strip().strip('"').strip("'").upper()
        if not validated_symbol:
            raise ConfigError("Stock symbol cannot be empty.")
        try:
            self.market_data.get_candles(validated_symbol, "D1", 5)
        except MarketDataError as exc:
            raise ConfigError(
                f"{validated_symbol} could not be validated with the current market data provider."
            ) from exc
        added, normalized = append_stock_to_universe(self.config.universe.stock_universe_path, symbol)
        total = self.refresh_universe()
        return added, normalized, total

    def watch_stock(self, symbol: str) -> tuple[bool, str, int]:
        return self.add_stock(symbol)

    def _collect_candidates(
        self,
        allow_repeat: bool = False,
        symbol_filter: Callable[[str], bool] | None = None,
    ) -> list[tuple[object, object]]:
        if self._stop_requested():
            self.logger.info("Stop requested. Aborting candidate collection before start.")
            return []
        self.refresh_universe()
        instruments = self.market_data.list_instruments()
        tradable = [
            instrument
            for instrument in self.instrument_filter.filter_tradable(instruments)
            if instrument.asset_class == AssetClass.STOCK
            and (symbol_filter is None or symbol_filter(instrument.symbol))
        ]
        context_instruments = self.instrument_filter.filter_context(instruments)
        context = self.market_data.get_context([item.symbol for item in context_instruments])
        positions = self.market_data.list_positions()
        performance = self.state_store.get_performance()
        candidates: list[tuple[object, object]] = []
        for instrument in tradable:
            if self._stop_requested():
                self.logger.info("Stop requested. Aborting candidate collection.")
                return []
            for timeframe in self.config.universe.allowed_timeframes:
                if self._stop_requested():
                    self.logger.info("Stop requested. Aborting candidate collection.")
                    return []
                try:
                    candles = self.market_data.get_candles(instrument.symbol, timeframe, 60)
                    fundamentals = self.market_data.get_stock_fundamentals(instrument.symbol)
                except MarketDataError as exc:
                    self.logger.warning("Market data unavailable for %s %s: %s", instrument.symbol, timeframe, exc)
                    continue
                signal = self.strategy.evaluate(
                    instrument,
                    timeframe,
                    candles,
                    context,
                    fundamentals,
                    allow_repeat=allow_repeat,
                )
                self.state_store.record_signal(signal)
                if signal.side == SignalSide.NO_TRADE:
                    continue
                try:
                    proposal = self.risk.build_proposal(signal, positions, performance)
                except RiskError as exc:
                    self.logger.info("Risk rejected %s %s: %s", instrument.symbol, timeframe, exc)
                    continue
                candidates.append((signal, proposal))

        return candidates

    def _candidate_rank(self, item: tuple[object, object]) -> tuple[float, float, float]:
        signal, _proposal = item
        return (
            getattr(signal, "margin_of_safety", None) or float("-inf"),
            getattr(signal, "quality_score", None) or float("-inf"),
            getattr(signal, "normalized_score", None) or float("-inf"),
        )

    def _collect_research_reports(
        self,
        symbol_filter: Callable[[str], bool] | None = None,
    ) -> list[CompanyResearchReport]:
        if self._stop_requested():
            self.logger.info("Stop requested. Aborting research report collection before start.")
            return []
        self.refresh_universe()
        universe_symbols = list(self.config.universe.allowed_stocks)
        reports: list[CompanyResearchReport] = []
        for symbol in universe_symbols:
            if self._stop_requested():
                self.logger.info("Stop requested. Aborting research report collection.")
                return []
            if symbol_filter is not None and not symbol_filter(symbol):
                continue
            try:
                report = self.market_data.get_stock_analysis(symbol, universe_symbols)
            except MarketDataError as exc:
                self.logger.warning("Research data unavailable for %s: %s", symbol, exc)
                continue
            reports.append(self._with_watchlist_status(report))
        return reports

    def _with_watchlist_status(self, report: CompanyResearchReport) -> CompanyResearchReport:
        is_watched = report.symbol in self.config.universe.allowed_stocks
        return replace(
            report,
            watchlist_status="Already on watchlist" if is_watched else "Not on watchlist",
        )

    def _passes_research_screen(self, report: CompanyResearchReport) -> bool:
        if report.recommendation == "SELL":
            return False
        if report.intrinsic_value is None or report.margin_of_safety is None:
            return False
        if report.data_quality_score < 0.45:
            return False
        return report.investment_score >= 0.50 or report.margin_of_safety >= 0.08

    def _research_rank(self, report: CompanyResearchReport) -> tuple[float, float, float, float, float]:
        return (
            report.investment_score,
            report.margin_of_safety if report.margin_of_safety is not None else float("-inf"),
            report.valuation_confidence,
            report.data_quality_score,
            report.quality_score,
        )

    def _select_best_candidate(
        self,
        allow_repeat: bool = False,
        symbol: str | None = None,
    ) -> CompanyResearchReport | None:
        normalized_symbol = symbol.strip().upper() if symbol else None
        if normalized_symbol:
            try:
                report = self.market_data.get_stock_analysis(normalized_symbol, list(self.config.universe.allowed_stocks))
            except MarketDataError:
                return None
            return self._with_watchlist_status(report)
        candidates = self.list_top_candidates(allow_repeat=allow_repeat)
        if not candidates:
            return None
        return candidates[0]

    def list_top_candidates(self, limit: int = 5, allow_repeat: bool = True) -> list[CompanyResearchReport]:
        reports = [
            report
            for report in self._collect_research_reports()
            if self._passes_research_screen(report)
        ]
        ranked = sorted(reports, key=self._research_rank, reverse=True)
        return ranked[:limit]

    def list_watchlist_reports(self) -> list[CompanyResearchReport]:
        reports = self._collect_research_reports()
        return sorted(reports, key=self._research_rank, reverse=True)

    def send_tip(
        self,
        chat_id: str | int | None = None,
        allow_repeat: bool = False,
        notify_when_empty: bool = False,
        symbol: str | None = None,
    ) -> int:
        self.logger.info("Starting tip request for %s", symbol.upper() if symbol else "best candidate")
        report = self._select_best_candidate(allow_repeat=allow_repeat, symbol=symbol)
        if report is None:
            if self._stop_requested():
                self.logger.info("Stop requested. Tip request aborted.")
                return 0
            if notify_when_empty and hasattr(self.approvals, "publish_text"):
                try:
                    message = (
                        render_no_strong_setup(symbol)
                        if symbol
                        else render_no_strong_setup()
                    )
                    self.approvals.publish_text(message, chat_id=chat_id)
                except TelegramApiError as exc:
                    self.logger.warning("Telegram publish failed for empty tip response: %s", exc)
            return 0

        if not hasattr(self.approvals, "publish_text"):
            return 0
        try:
            self.approvals.publish_text(
                render_quick_research_report(report, best_idea=symbol is None),
                chat_id=chat_id,
            )
        except TelegramApiError as exc:
            self.logger.warning("Telegram publish failed for quick research %s: %s", report.symbol, exc)
            return 0
        return 1

    def send_top_tips(self, chat_id: str | int | None = None, limit: int = 5) -> int:
        if not hasattr(self.approvals, "publish_text"):
            return 0
        self.logger.info("Building TOP RESEARCH IDEAS shortlist with limit=%s", limit)
        candidates = self.list_top_candidates(limit=limit, allow_repeat=True)
        if not candidates:
            if self._stop_requested():
                self.logger.info("Stop requested. Shortlist generation aborted.")
                return 0
            try:
                self.approvals.publish_text(render_no_strong_setup(), chat_id=chat_id)
            except TelegramApiError as exc:
                self.logger.warning("Telegram publish failed for top tips response: %s", exc)
                return 0
            return 0
        try:
            self.approvals.publish_text(render_shortlist(candidates, limit), chat_id=chat_id)
        except TelegramApiError as exc:
            self.logger.warning("Telegram publish failed for top tips response: %s", exc)
            return 0
        return len(candidates)

    def send_watchlist(self, chat_id: str | int | None = None) -> int:
        if not hasattr(self.approvals, "publish_text"):
            return 0
        self.logger.info("Building research watchlist view")
        total = self.refresh_universe()
        reports = self.list_watchlist_reports()
        try:
            self.approvals.publish_text(render_watchlist(reports, total), chat_id=chat_id)
        except TelegramApiError as exc:
            self.logger.warning("Telegram publish failed for watchlist response: %s", exc)
            return 0
        return len(reports)

    def compare_stocks(self, symbols: tuple[str, ...], chat_id: str | int | None = None) -> int:
        if not hasattr(self.approvals, "publish_text"):
            return 0
        normalized_symbols = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))[:5]
        if len(normalized_symbols) < 2:
            self.approvals.publish_text(render_compare([], normalized_symbols), chat_id=chat_id)
            return 0
        self.logger.info("Comparing stocks: %s", ", ".join(normalized_symbols))
        self.refresh_universe()
        reports: list[CompanyResearchReport] = []
        for symbol in normalized_symbols:
            try:
                report = self.market_data.get_stock_analysis(symbol, list(self.config.universe.allowed_stocks))
            except MarketDataError as exc:
                self.logger.warning("Research data unavailable for compare symbol %s: %s", symbol, exc)
                continue
            reports.append(self._with_watchlist_status(report))
        try:
            self.approvals.publish_text(render_compare(reports, normalized_symbols), chat_id=chat_id)
        except TelegramApiError as exc:
            self.logger.warning("Telegram publish failed for compare response: %s", exc)
            return 0
        return len(reports)

    def analyze_stock(self, symbol: str, chat_id: str | int | None = None) -> int:
        normalized_symbol = symbol.strip().strip('"').strip("'").upper()
        if not normalized_symbol:
            raise ConfigError("Stock symbol cannot be empty.")
        self.logger.info("Starting detailed analysis for %s", normalized_symbol)
        self.refresh_universe()
        try:
            report = self.market_data.get_stock_analysis(normalized_symbol, list(self.config.universe.allowed_stocks))
        except MarketDataError as exc:
            raise ConfigError(f"{normalized_symbol} could not be analyzed with the current market data provider.") from exc
        total = self.refresh_universe()
        is_watched = normalized_symbol in self.config.universe.allowed_stocks
        report = replace(
            report,
            watchlist_status="Already on watchlist" if is_watched else "Not on watchlist",
        )

        if hasattr(self.approvals, "publish_text"):
            try:
                self.approvals.publish_text(render_company_report(report), chat_id=chat_id)
            except TelegramApiError as exc:
                self.logger.warning("Telegram publish failed for stock analysis %s: %s", normalized_symbol, exc)
                return 0
        return 1

    def send_portfolio_report(self, chat_id: str | int | None = None) -> int:
        if not hasattr(self.approvals, "publish_text"):
            return 0
        self.logger.info("Building portfolio daily performance report")
        holdings = self.portfolio_holdings()
        if not holdings:
            try:
                self.approvals.publish_text(
                    render_error("Portfolio Daily Report", "No portfolio symbols configured right now."),
                    chat_id=chat_id,
                )
            except TelegramApiError as exc:
                self.logger.warning("Telegram publish failed for portfolio response: %s", exc)
            return 0

        lines = ["📁 <b>Portfolio Daily Report</b>", SEPARATOR, ""]
        generated = 0
        up_count = 0
        down_count = 0
        neutral_count = 0
        daily_total_pnl = 0.0
        has_daily_total = False
        total_unrealized_pnl = 0.0
        has_unrealized_total = False
        detail_lines: list[str] = []
        for holding in holdings:
            symbol = holding.symbol
            try:
                candles = self.market_data.get_candles(symbol, "D1", 22)
                if len(candles) < 2:
                    raise MarketDataError(f"Not enough daily candles for {symbol}.")
                previous_close = candles[-2].close
                current_price = candles[-1].close
                move_pct = ((current_price - previous_close) / previous_close) if previous_close else None
                fundamentals = self.market_data.get_stock_fundamentals(symbol)
            except MarketDataError as exc:
                self.logger.warning("Portfolio data unavailable for %s: %s", symbol, exc)
                neutral_count += 1
                detail_lines.extend(
                    [
                        f"<b>{html_escape(symbol)}</b>",
                        "Daily move: n/a",
                        "Possible reason: market data unavailable",
                        "",
                    ]
                )
                continue
            company_name = fundamentals.company_name or symbol
            if move_pct is None:
                neutral_count += 1
            elif move_pct > 0:
                up_count += 1
            elif move_pct < 0:
                down_count += 1
            else:
                neutral_count += 1
            detail_lines.extend(
                [
                    f"<b>{html_escape(company_name)} - {html_escape(symbol)}</b>",
                    f"Current price: {html_escape(format_price(current_price))}",
                    f"Previous close: {html_escape(format_price(previous_close))}",
                    f"Daily move: {html_escape(format_signed_pct(move_pct))}",
                ]
            )
            if holding.quantity is not None:
                daily_pnl = (current_price - previous_close) * holding.quantity
                daily_total_pnl += daily_pnl
                has_daily_total = True
                detail_lines.append(f"Quantity: {holding.quantity:g}")
                detail_lines.append(f"Estimated daily P/L: {html_escape(format_signed_price(daily_pnl))}")
            if holding.quantity is not None and holding.average_cost is not None:
                unrealized_pnl = (current_price - holding.average_cost) * holding.quantity
                unrealized_pct = (current_price - holding.average_cost) / holding.average_cost if holding.average_cost else None
                total_unrealized_pnl += unrealized_pnl
                has_unrealized_total = True
                detail_lines.append(f"Average cost: {html_escape(format_price(holding.average_cost))}")
                detail_lines.append(
                    f"P/L since buy: {html_escape(format_signed_price(unrealized_pnl))} ({html_escape(format_signed_pct(unrealized_pct))})"
                )
            detail_lines.extend(
                [
                    f"Possible reason: {html_escape(self._portfolio_reason(fundamentals, candles, move_pct))}",
                    "",
                ]
            )
            generated += 1
        lines.extend([f"🟢 Up: {up_count}  | 🔴 Down: {down_count}  | ⚪ Neutral: {neutral_count}", ""])
        if has_daily_total:
            lines.append(f"Estimated daily portfolio P/L: {html_escape(format_signed_price(daily_total_pnl))}")
        if has_unrealized_total:
            lines.append(f"Portfolio P/L since buy: {html_escape(format_signed_price(total_unrealized_pnl))}")
        if has_daily_total or has_unrealized_total:
            lines.append("")
        lines.extend(detail_lines)

        try:
            self.approvals.publish_text("\n".join(lines).strip(), chat_id=chat_id)
        except TelegramApiError as exc:
            self.logger.warning("Telegram publish failed for portfolio response: %s", exc)
            return 0
        return generated

    def add_portfolio_holding(
        self,
        symbol: str,
        quantity: float,
        average_cost: float,
        chat_id: str | int | None = None,
    ) -> int:
        return self._upsert_portfolio_holding("added", symbol, quantity, average_cost, chat_id)

    def update_portfolio_holding(
        self,
        symbol: str,
        quantity: float,
        average_cost: float,
        chat_id: str | int | None = None,
    ) -> int:
        return self._upsert_portfolio_holding("updated", symbol, quantity, average_cost, chat_id)

    def _upsert_portfolio_holding(
        self,
        action: str,
        symbol: str,
        quantity: float,
        average_cost: float,
        chat_id: str | int | None,
    ) -> int:
        if not hasattr(self.approvals, "publish_text"):
            return 0
        normalized_symbol = symbol.strip().strip('"').strip("'").upper()
        try:
            self.market_data.get_candles(normalized_symbol, "D1", 5)
            added, holding, total = upsert_portfolio_holding(
                self._portfolio_path(),
                normalized_symbol,
                quantity,
                average_cost,
            )
            changed = True if action == "updated" else added
            self.approvals.publish_text(render_portfolio_update(action, holding, total, changed), chat_id=chat_id)
        except (ConfigError, MarketDataError) as exc:
            self.approvals.publish_text(render_error("Unable to update portfolio", exc), chat_id=chat_id)
            return 0
        except TelegramApiError as exc:
            self.logger.warning("Telegram publish failed for portfolio update: %s", exc)
            return 0
        return 1

    def remove_portfolio_holding(self, symbol: str, chat_id: str | int | None = None) -> int:
        if not hasattr(self.approvals, "publish_text"):
            return 0
        try:
            removed, normalized, total = remove_portfolio_holding(self._portfolio_path(), symbol)
            self.approvals.publish_text(
                render_portfolio_update("removed", normalized, total, changed=removed),
                chat_id=chat_id,
            )
        except ConfigError as exc:
            self.approvals.publish_text(render_error("Unable to update portfolio", exc), chat_id=chat_id)
            return 0
        except TelegramApiError as exc:
            self.logger.warning("Telegram publish failed for portfolio remove: %s", exc)
            return 0
        return 1 if removed else 0

    def send_portfolio_usage(self, chat_id: str | int | None = None) -> int:
        if not hasattr(self.approvals, "publish_text"):
            return 0
        self.approvals.publish_text(render_portfolio_usage(), chat_id=chat_id)
        return 0

    def add_alert(self, symbol: str, threshold: float, chat_id: str | int | None = None) -> int:
        if not hasattr(self.approvals, "publish_text"):
            return 0
        normalized_symbol = symbol.strip().strip('"').strip("'").upper()
        if not normalized_symbol or threshold <= 0:
            self.approvals.publish_text(render_error("Alert command", "Usage: /alert MSFT 15%"), chat_id=chat_id)
            return 0
        try:
            self.market_data.get_stock_analysis(normalized_symbol, list(self.config.universe.allowed_stocks))
            added, alert = self.state_store.upsert_alert(normalized_symbol, threshold)
            self.approvals.publish_text(render_alert_update(alert["symbol"], alert["threshold"], added), chat_id=chat_id)
        except MarketDataError as exc:
            self.approvals.publish_text(render_error("Unable to create alert", exc), chat_id=chat_id)
            return 0
        except TelegramApiError as exc:
            self.logger.warning("Telegram publish failed for alert update: %s", exc)
            return 0
        return 1

    def send_alerts(self, chat_id: str | int | None = None) -> int:
        if not hasattr(self.approvals, "publish_text"):
            return 0
        alerts = self.state_store.list_alerts()
        self.approvals.publish_text(render_alerts(alerts), chat_id=chat_id)
        return len(alerts)

    def remove_alert(self, symbol: str, chat_id: str | int | None = None) -> int:
        if not hasattr(self.approvals, "publish_text"):
            return 0
        removed, normalized, total = self.state_store.remove_alert(symbol)
        self.approvals.publish_text(render_alert_removed(normalized, removed, total), chat_id=chat_id)
        return 1 if removed else 0

    def send_alert_usage(self, chat_id: str | int | None = None) -> int:
        if not hasattr(self.approvals, "publish_text"):
            return 0
        self.approvals.publish_text(render_error("Alert command", "Usage: /alert MSFT 15%"), chat_id=chat_id)
        return 0

    def process_alerts(self) -> int:
        if not hasattr(self.approvals, "publish_text"):
            return 0
        alerts = self.state_store.list_alerts()
        if not alerts:
            return 0
        generated = 0
        universe_symbols = list(self.config.universe.allowed_stocks)
        now = datetime.now(timezone.utc)
        for alert in alerts:
            if self._alert_triggered_today(alert, now):
                continue
            symbol = str(alert["symbol"])
            threshold = float(alert["threshold"])
            try:
                report = self.market_data.get_stock_analysis(symbol, universe_symbols)
            except MarketDataError as exc:
                self.logger.warning("Alert data unavailable for %s: %s", symbol, exc)
                continue
            if report.margin_of_safety is None or report.margin_of_safety < threshold:
                continue
            try:
                self.approvals.publish_text(render_alert_triggered(report, threshold))
            except TelegramApiError as exc:
                self.logger.warning("Telegram publish failed for alert %s: %s", symbol, exc)
                continue
            self.state_store.mark_alert_triggered(symbol, now)
            generated += 1
        return generated

    def _alert_triggered_today(self, alert: dict, now: datetime) -> bool:
        last_triggered_at = alert.get("last_triggered_at")
        if not isinstance(last_triggered_at, str) or not last_triggered_at:
            return False
        try:
            triggered = datetime.fromisoformat(last_triggered_at)
        except ValueError:
            return False
        if triggered.tzinfo is None:
            triggered = triggered.replace(tzinfo=timezone.utc)
        return triggered.astimezone(timezone.utc).date() == now.date()

    def send_help(self, chat_id: str | int | None = None) -> int:
        if not hasattr(self.approvals, "publish_text"):
            return 0
        lines = [
            "🤖 <b>Stock Research Assistant</b>",
            SEPARATOR,
            "",
            "<b>Research</b>",
            "/top 5 or top 5",
            "Get the current top research shortlist.",
            "",
            "/analyze MSFT",
            "Build a full company research report.",
            "",
            "/tip or /tip MSFT",
            "Run a quick single-idea screen.",
            "",
            "<b>Watchlist</b>",
            "/watch NVDA",
            "Add a company to your research universe.",
            "",
            "/watchlist",
            "Show the current research watchlist.",
            "",
            "/compare AAPL MSFT",
            "Compare two to five companies.",
            "",
            "<b>Portfolio</b>",
            "portfolio or /portfolio",
            "Show the daily performance of the stocks in config/portfolio.txt.",
            "",
            "/portfolio add MSFT 10 320.50",
            "Add or replace a portfolio holding with quantity and average cost.",
            "",
            "/portfolio remove MSFT",
            "Remove a holding from the portfolio.",
            "",
            "<b>Alerts</b>",
            "/alert MSFT 15%",
            "Alert when margin of safety reaches the chosen threshold.",
            "",
            "/alerts or /unalert MSFT",
            "List or remove research alerts.",
            "",
            "<b>Aliases</b>",
            "analise MSFT or /analise MSFT",
            "Compatibility alias for /analyze during the transition.",
            "",
            "<b>Help</b>",
            "help or /help",
            "Show this command list.",
        ]
        try:
            self.approvals.publish_text("\n".join(lines), chat_id=chat_id)
        except TelegramApiError as exc:
            self.logger.warning("Telegram publish failed for help response: %s", exc)
            return 0
        return 1

    def portfolio_symbols(self) -> tuple[str, ...]:
        return load_portfolio_symbols(self._portfolio_path())

    def portfolio_holdings(self) -> tuple[PortfolioHolding, ...]:
        return load_portfolio_holdings(self._portfolio_path())

    def scan(self) -> int:
        if hasattr(self.approvals, "publish_text"):
            return self.send_top_tips(limit=3)
        return self.send_tip()

    def process_approvals(self) -> int:
        processed = 0
        for decision in self.approvals.get_pending_decisions():
            self.state_store.record_decision(decision)
            if decision.status == ApprovalStatus.EXPIRED:
                self.state_store.mark_expired(decision.proposal_id)
            processed += 1
        return processed

    def _portfolio_path(self) -> Path:
        path = getattr(self.config, "portfolio_path", None)
        return Path(path) if path is not None else Path("config/portfolio.txt")

    def _portfolio_reason(self, fundamentals: object, candles: list[Candle], move_pct: float | None) -> str:
        if move_pct is None:
            return "insufficient price history"
        current_candle = candles[-1] if candles else None
        previous_candles = candles[:-1]
        close_position = self._close_position_in_range(current_candle)
        volume_ratio = self._volume_ratio(current_candle, previous_candles[-20:])
        short_trend = self._lookback_move(candles, 5)
        medium_trend = self._lookback_move(candles, 20)
        intraday_move = None
        if current_candle is not None and current_candle.open:
            intraday_move = (current_candle.close - current_candle.open) / current_candle.open

        revenue_growth = getattr(fundamentals, "revenue_growth", None)
        earnings_growth = getattr(fundamentals, "earnings_growth", None)
        debt_to_equity = getattr(fundamentals, "debt_to_equity", None)
        free_cash_flow_yield = getattr(fundamentals, "free_cash_flow_yield", None)
        margin_of_safety = None
        current_price = getattr(fundamentals, "current_price", None)
        target_mean_price = getattr(fundamentals, "target_mean_price", None)
        if current_price and target_mean_price:
            margin_of_safety = (target_mean_price - current_price) / current_price
        if move_pct >= 0.01:
            drivers: list[str] = []
            if volume_ratio is not None and volume_ratio >= 1.5:
                drivers.append(f"heavy volume ({volume_ratio:.1f}x recent average) confirms strong buying")
            elif volume_ratio is not None and volume_ratio >= 1.15:
                drivers.append(f"above-average volume ({volume_ratio:.1f}x) supports the move")
            if close_position is not None and close_position >= 0.75:
                drivers.append("it closed near the day high, a bullish price-action signal")
            elif intraday_move is not None and intraday_move > 0.01:
                drivers.append("buyers lifted it from the open through the session")
            if short_trend is not None and short_trend > 0.02:
                drivers.append(f"it is extending a positive 5-day trend ({format_pct(short_trend)})")
            elif short_trend is not None and short_trend < -0.02:
                drivers.append(f"it may be rebounding after a weak 5-day trend ({format_pct(short_trend)})")
            if revenue_growth is not None and revenue_growth > 0.08:
                drivers.append(f"revenue growth is strong ({format_pct(revenue_growth)})")
            if earnings_growth is not None and earnings_growth > 0.1:
                drivers.append(f"earnings growth is strong ({format_pct(earnings_growth)})")
            if free_cash_flow_yield is not None and free_cash_flow_yield > 0.05:
                drivers.append(f"free-cash-flow yield is supportive ({format_pct(free_cash_flow_yield)})")
            if margin_of_safety is not None and margin_of_safety > 0.15:
                drivers.append(f"analyst target implies upside ({format_pct(margin_of_safety)})")
            if not drivers:
                return "small gain; likely normal buying or broader market sentiment, with no strong company-specific signal in the available data"
            return self._join_reason(drivers)
        if move_pct <= -0.01:
            drivers = []
            if volume_ratio is not None and volume_ratio >= 1.5:
                drivers.append(f"heavy volume ({volume_ratio:.1f}x recent average) points to conviction selling")
            elif volume_ratio is not None and volume_ratio >= 1.15:
                drivers.append(f"above-average volume ({volume_ratio:.1f}x) makes the drop more meaningful")
            if close_position is not None and close_position <= 0.25:
                drivers.append("it closed near the day low, a bearish price-action signal")
            elif intraday_move is not None and intraday_move < -0.01:
                drivers.append("it faded from the open through the session")
            if short_trend is not None and short_trend < -0.02:
                drivers.append(f"it is extending a weak 5-day trend ({format_pct(short_trend)})")
            elif short_trend is not None and short_trend > 0.03:
                drivers.append(f"it may be profit taking after a strong 5-day run ({format_pct(short_trend)})")
            if medium_trend is not None and medium_trend < -0.05:
                drivers.append(f"the 20-day trend is also weak ({format_pct(medium_trend)})")
            if debt_to_equity is not None and debt_to_equity > 100:
                drivers.append(f"leverage is high (debt/equity {debt_to_equity:.0f})")
            if earnings_growth is not None and earnings_growth < 0:
                drivers.append(f"earnings growth is negative ({format_pct(earnings_growth)})")
            if margin_of_safety is not None and margin_of_safety < -0.05:
                drivers.append(f"analyst target implies limited upside ({format_pct(margin_of_safety)})")
            if not drivers:
                return "small drop; likely normal selling or broader market sentiment, with no strong company-specific signal in the available data"
            return self._join_reason(drivers)
        return "normal day-to-day price movement; the move is too small to infer a clear driver from the available data"

    def _close_position_in_range(self, candle: Candle | None) -> float | None:
        if candle is None or candle.high <= candle.low:
            return None
        return (candle.close - candle.low) / (candle.high - candle.low)

    def _volume_ratio(self, candle: Candle | None, previous_candles: list[Candle]) -> float | None:
        if candle is None or candle.volume <= 0:
            return None
        volumes = [item.volume for item in previous_candles if item.volume > 0]
        if not volumes:
            return None
        average_volume = sum(volumes) / len(volumes)
        return None if average_volume <= 0 else candle.volume / average_volume

    def _lookback_move(self, candles: list[Candle], sessions: int) -> float | None:
        if len(candles) <= sessions:
            return None
        base_close = candles[-(sessions + 1)].close
        if not base_close:
            return None
        return (candles[-1].close - base_close) / base_close

    def _join_reason(self, drivers: list[str]) -> str:
        if len(drivers) == 1:
            return drivers[0]
        selected = drivers[:3]
        return "; ".join(selected[:-1]) + f"; and {selected[-1]}"
