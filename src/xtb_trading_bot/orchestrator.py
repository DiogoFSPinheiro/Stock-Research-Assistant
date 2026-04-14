from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from pathlib import Path
from typing import Callable

from .config import AppConfig, ConfigError, append_stock_to_universe, load_portfolio_symbols, refresh_stock_universe
from .domain import ApprovalStatus, AssetClass, SignalSide
from .instruments import InstrumentFilter
from .interfaces import ApprovalService, MarketDataProvider, StateStore
from .market_data import MarketDataError
from .reporting import format_pct, render_company_report, render_shortlist
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

    def _select_best_candidate(
        self,
        allow_repeat: bool = False,
        symbol: str | None = None,
    ) -> tuple[object, object] | None:
        normalized_symbol = symbol.strip().upper() if symbol else None
        candidates = self._collect_candidates(
            allow_repeat=allow_repeat,
            symbol_filter=(lambda candidate_symbol: candidate_symbol == normalized_symbol) if normalized_symbol else None,
        )
        if not candidates:
            return None
        return max(candidates, key=self._candidate_rank)

    def list_top_candidates(self, limit: int = 5, allow_repeat: bool = True) -> list[tuple[object, object]]:
        candidates = self._collect_candidates(allow_repeat=allow_repeat)
        best_by_symbol: dict[str, tuple[object, object]] = {}
        for candidate in candidates:
            signal, _proposal = candidate
            symbol = getattr(signal, "symbol", None)
            if not symbol:
                continue
            current_best = best_by_symbol.get(symbol)
            if current_best is None or self._candidate_rank(candidate) > self._candidate_rank(current_best):
                best_by_symbol[symbol] = candidate
        ranked = sorted(best_by_symbol.values(), key=self._candidate_rank, reverse=True)
        return ranked[:limit]

    def send_tip(
        self,
        chat_id: str | int | None = None,
        allow_repeat: bool = False,
        notify_when_empty: bool = False,
        symbol: str | None = None,
    ) -> int:
        self.logger.info("Starting tip request for %s", symbol.upper() if symbol else "best candidate")
        candidate = self._select_best_candidate(allow_repeat=allow_repeat, symbol=symbol)
        if candidate is None:
            if self._stop_requested():
                self.logger.info("Stop requested. Tip request aborted.")
                return 0
            if notify_when_empty and hasattr(self.approvals, "publish_text"):
                try:
                    message = (
                        f"No strong setup found for {symbol.upper()} right now. Try again a bit later."
                        if symbol
                        else "No strong stock tip right now. Try again a bit later."
                    )
                    self.approvals.publish_text(message, chat_id=chat_id)
                except TelegramApiError as exc:
                    self.logger.warning("Telegram publish failed for empty tip response: %s", exc)
            return 0

        signal, proposal = candidate
        self.state_store.record_proposal(proposal)
        try:
            if chat_id is None:
                self.approvals.publish_signal(signal, proposal)
            else:
                try:
                    self.approvals.publish_signal(signal, proposal, chat_id=chat_id)
                except TypeError:
                    self.approvals.publish_signal(signal, proposal)
        except TelegramApiError as exc:
            self.logger.warning("Telegram publish failed for %s %s: %s", signal.symbol, signal.timeframe, exc)
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
                self.approvals.publish_text("TOP RESEARCH IDEAS\nNo companies passed the research screen right now.", chat_id=chat_id)
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
        symbols = self.portfolio_symbols()
        if not symbols:
            try:
                self.approvals.publish_text(
                    "PORTFOLIO\nNo portfolio symbols configured right now.",
                    chat_id=chat_id,
                )
            except TelegramApiError as exc:
                self.logger.warning("Telegram publish failed for portfolio response: %s", exc)
            return 0

        lines = ["PORTFOLIO"]
        generated = 0
        up_count = 0
        down_count = 0
        neutral_count = 0
        detail_lines: list[str] = []
        for symbol in symbols:
            try:
                candles = self.market_data.get_candles(symbol, "D1", 2)
                if len(candles) < 2:
                    raise MarketDataError(f"Not enough daily candles for {symbol}.")
                previous_close = candles[-2].close
                current_price = candles[-1].close
                move_pct = ((current_price - previous_close) / previous_close) if previous_close else None
                fundamentals = self.market_data.get_stock_fundamentals(symbol)
            except MarketDataError as exc:
                self.logger.warning("Portfolio data unavailable for %s: %s", symbol, exc)
                neutral_count += 1
                detail_lines.extend([f"{symbol}", "Daily move: n/a", "Possible reason: market data unavailable", ""])
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
                    f"{company_name} ({symbol})",
                    f"Current price: {current_price:.2f}",
                    f"Previous close: {previous_close:.2f}",
                    f"Daily move: {format_pct(move_pct)}",
                    f"Possible reason: {self._portfolio_reason(fundamentals, move_pct)}",
                    "",
                ]
            )
            generated += 1
        lines.extend([f"Up: {up_count} , Down: {down_count} , Neutral: {neutral_count}", ""])
        lines.extend(detail_lines)

        try:
            self.approvals.publish_text("\n".join(lines).strip(), chat_id=chat_id)
        except TelegramApiError as exc:
            self.logger.warning("Telegram publish failed for portfolio response: %s", exc)
            return 0
        return generated

    def send_help(self, chat_id: str | int | None = None) -> int:
        if not hasattr(self.approvals, "publish_text"):
            return 0
        lines = [
            "HELP",
            "",
            "/top 5 or top 5",
            "Get the current top research shortlist.",
            "",
            "/analyze MSFT",
            "Build a full company research report.",
            "",
            "/watch NVDA",
            "Add a company to your research watchlist.",
            "",
            "/tip or /tip MSFT",
            "Compatibility alias for a quick single-idea screen.",
            "",
            "portfolio or /portfolio",
            "Show the daily performance of the stocks in config/portfolio.txt.",
            "",
            "analise MSFT or /analise MSFT",
            "Compatibility alias for /analyze during the transition.",
            "",
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

    def _portfolio_reason(self, fundamentals: object, move_pct: float | None) -> str:
        if move_pct is None:
            return "insufficient price history"
        revenue_growth = getattr(fundamentals, "revenue_growth", None)
        earnings_growth = getattr(fundamentals, "earnings_growth", None)
        debt_to_equity = getattr(fundamentals, "debt_to_equity", None)
        margin_of_safety = None
        current_price = getattr(fundamentals, "current_price", None)
        target_mean_price = getattr(fundamentals, "target_mean_price", None)
        if current_price and target_mean_price:
            margin_of_safety = (target_mean_price - current_price) / current_price
        if move_pct >= 0.03:
            if revenue_growth is not None and revenue_growth > 0.08:
                return "strong growth profile likely helped sentiment"
            if margin_of_safety is not None and margin_of_safety > 0.15:
                return "valuation upside may be attracting buyers"
            return "positive market sentiment likely supported the move"
        if move_pct <= -0.03:
            if debt_to_equity is not None and debt_to_equity > 100:
                return "higher leverage may be pressuring sentiment"
            if earnings_growth is not None and earnings_growth < 0:
                return "weaker earnings profile may be weighing on shares"
            return "profit taking or weaker market sentiment may explain the drop"
        return "normal day-to-day price movement"
