from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from typing import Callable

from .config import AppConfig, ConfigError, append_stock_to_universe, refresh_stock_universe
from .domain import ApprovalStatus, AssetClass, SignalSide, StockAnalysisReport
from .instruments import InstrumentFilter
from .interfaces import ApprovalService, MarketDataProvider, StateStore
from .market_data import MarketDataError
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
        ranked = sorted(candidates, key=self._candidate_rank, reverse=True)
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
        self.logger.info("Building TOP QUALITY-VALUE IDEAS shortlist with limit=%s", limit)
        candidates = self.list_top_candidates(limit=limit, allow_repeat=True)
        if not candidates:
            if self._stop_requested():
                self.logger.info("Stop requested. Shortlist generation aborted.")
                return 0
            try:
                self.approvals.publish_text("TOP QUALITY-VALUE IDEAS\nNo stocks passed the quality-value screen right now.", chat_id=chat_id)
            except TelegramApiError as exc:
                self.logger.warning("Telegram publish failed for top tips response: %s", exc)
                return 0
            return 0

        header = "TOP QUALITY-VALUE IDEAS"
        if len(candidates) < limit:
            header = f"{header} ({len(candidates)} of {limit} passed the screen)"
        lines = [header, ""]
        for index, (signal, _proposal) in enumerate(candidates, start=1):
            lines.extend(
                [
                    f"{index}. {signal.symbol}",
                    f"Best horizon: {self._format_horizon(signal)}",
                    f"Entry price: {self._format_price(getattr(signal, 'entry', None))}",
                    f"Fair value: {self._format_price(getattr(signal, 'fair_value', None))}",
                    f"Margin of safety: {self._format_pct(getattr(signal, 'margin_of_safety', None))}",
                    f"Quality score: {self._format_score(getattr(signal, 'quality_score', None))}",
                    f"Timing score: {self._format_score(getattr(signal, 'timing_score', None))}",
                    f"Expected return: {self._format_pct(signal.expected_return)}",
                    f"Confidence: {signal.confidence:.0%}",
                    f"Probability up: {self._format_pct(signal.probability_positive)}",
                    f"Adjusted score/day: {self._format_pct(signal.normalized_score)}",
                    f"Risks: {self._format_risks(getattr(signal, 'risk_flags', ())) }",
                    f"Why: {signal.rationale}",
                    "",
                ]
            )
        try:
            self.approvals.publish_text("\n".join(lines).strip(), chat_id=chat_id)
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
        added = False
        auto_add_applied = report.recommendation == "BUY"
        if auto_add_applied:
            added, normalized_symbol, total = self.add_stock(normalized_symbol)
        else:
            total = self.refresh_universe()

        if hasattr(self.approvals, "publish_text"):
            try:
                self.approvals.publish_text(
                    self._format_stock_analysis(report, added=added, total=total, auto_add_applied=auto_add_applied),
                    chat_id=chat_id,
                )
            except TelegramApiError as exc:
                self.logger.warning("Telegram publish failed for stock analysis %s: %s", normalized_symbol, exc)
                return 0
        return 1

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

    def _format_horizon(self, signal: object) -> str:
        horizon_days = getattr(signal, "horizon_days", None)
        if horizon_days is not None:
            if horizon_days == 1:
                return "1 day"
            if horizon_days < 21:
                return f"{horizon_days} days"
            if horizon_days < 126:
                return "1 month"
            return "6 months"
        return getattr(signal, "timeframe", "n/a")

    def _format_pct(self, value: float | None) -> str:
        return "n/a" if value is None else f"{value:.1%}"

    def _format_price(self, value: float | None) -> str:
        return "n/a" if value is None else f"{value:.2f}"

    def _format_score(self, value: float | None) -> str:
        return "n/a" if value is None else f"{value:.2f}"

    def _format_risks(self, flags: tuple[str, ...] | list[str]) -> str:
        return "none flagged" if not flags else ", ".join(str(flag) for flag in flags)

    def _format_stock_analysis(self, report: StockAnalysisReport, added: bool, total: int, auto_add_applied: bool) -> str:
        if auto_add_applied and added:
            universe_line = f"Universe: added to watchlist ({total} stocks)"
        elif auto_add_applied:
            universe_line = f"Universe: already watching ({total} stocks)"
        else:
            universe_line = f"Universe: unchanged (auto-add only on BUY, {total} stocks)"
        options_line = (
            f"{report.options_sentiment} ({report.options_put_call_ratio:.2f} put/call)"
            if report.options_put_call_ratio is not None
            else report.options_sentiment
        )
        margin_line = self._format_pct(report.margin_of_safety)
        return "\n".join(
            [
                f"ANALISE {report.symbol}: {(report.company_name or report.symbol)} ({report.symbol})",
                f"Price: {report.current_price:.2f}",
                f"FCF model: {self._format_price(report.fcf_value)}",
                f"DCF model: {self._format_price(report.dcf_value)}",
                f"Intrinsic value: {self._format_price(report.intrinsic_value)}",
                f"Analyst target: {self._format_price(report.analyst_target)}",
                f"Margin of safety: {margin_line}",
                f"Quality model: {self._format_score(report.quality_score)}",
                f"Options traders: {options_line}",
                f"Benchmark: {report.benchmark_summary}",
                f"Risk: {report.key_risk}",
                f"Decision: {report.recommendation}",
                universe_line,
            ]
        )
