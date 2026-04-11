from __future__ import annotations

from dataclasses import dataclass
import logging

from .config import AppConfig
from .domain import ApprovalStatus, SignalSide
from .instruments import InstrumentFilter
from .interfaces import ApprovalService, MarketDataProvider, StateStore
from .risk import RiskEngine, RiskError
from .strategy import TrendSignalEngine


@dataclass
class TradingBot:
    config: AppConfig
    market_data: MarketDataProvider
    approvals: ApprovalService
    instrument_filter: InstrumentFilter
    strategy: TrendSignalEngine
    risk: RiskEngine
    state_store: StateStore
    logger: logging.Logger

    def scan(self) -> int:
        instruments = self.market_data.list_instruments()
        tradable = self.instrument_filter.filter_tradable(instruments)
        context_instruments = self.instrument_filter.filter_context(instruments)
        context = self.market_data.get_context([item.symbol for item in context_instruments])
        positions = self.market_data.list_positions()
        performance = self.state_store.get_performance()

        generated = 0
        for instrument in tradable:
            for timeframe in self.config.universe.allowed_timeframes:
                candles = self.market_data.get_candles(instrument.symbol, timeframe, 60)
                signal = self.strategy.evaluate(instrument, timeframe, candles, context)
                self.state_store.record_signal(signal)
                if signal.side == SignalSide.NO_TRADE:
                    continue
                try:
                    proposal = self.risk.build_proposal(signal, positions, performance)
                except RiskError as exc:
                    self.logger.info("Risk rejected %s %s: %s", instrument.symbol, timeframe, exc)
                    continue
                self.state_store.record_proposal(proposal)
                self.approvals.publish_signal(signal, proposal)
                generated += 1
        return generated

    def process_approvals(self) -> int:
        processed = 0
        for decision in self.approvals.get_pending_decisions():
            self.state_store.record_decision(decision)
            if decision.status == ApprovalStatus.EXPIRED:
                self.state_store.mark_expired(decision.proposal_id)
            processed += 1
        return processed
