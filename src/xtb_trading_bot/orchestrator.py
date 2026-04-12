from __future__ import annotations

from dataclasses import dataclass, replace
import logging

from .config import AppConfig, append_stock_to_universe, refresh_stock_universe
from .domain import ApprovalStatus, AssetClass, SignalSide
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
        added, normalized = append_stock_to_universe(self.config.universe.stock_universe_path, symbol)
        total = self.refresh_universe()
        return added, normalized, total

    def _select_best_candidate(self, allow_repeat: bool = False) -> tuple[object, object] | None:
        self.refresh_universe()
        instruments = self.market_data.list_instruments()
        tradable = [
            instrument
            for instrument in self.instrument_filter.filter_tradable(instruments)
            if instrument.asset_class == AssetClass.STOCK
        ]
        context_instruments = self.instrument_filter.filter_context(instruments)
        context = self.market_data.get_context([item.symbol for item in context_instruments])
        positions = self.market_data.list_positions()
        performance = self.state_store.get_performance()

        candidates: list[tuple[object, object]] = []
        for instrument in tradable:
            for timeframe in self.config.universe.allowed_timeframes:
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

        if not candidates:
            return None

        return max(candidates, key=lambda item: item[0].confidence)

    def send_tip(
        self,
        chat_id: str | int | None = None,
        allow_repeat: bool = False,
        notify_when_empty: bool = False,
    ) -> int:
        candidate = self._select_best_candidate(allow_repeat=allow_repeat)
        if candidate is None:
            if notify_when_empty and hasattr(self.approvals, "publish_text"):
                try:
                    self.approvals.publish_text("No strong stock tip right now. Try again a bit later.", chat_id=chat_id)
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

    def scan(self) -> int:
        return self.send_tip()

    def process_approvals(self) -> int:
        processed = 0
        for decision in self.approvals.get_pending_decisions():
            self.state_store.record_decision(decision)
            if decision.status == ApprovalStatus.EXPIRED:
                self.state_store.mark_expired(decision.proposal_id)
            processed += 1
        return processed
