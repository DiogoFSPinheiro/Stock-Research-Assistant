from __future__ import annotations

from typing import Protocol

from .domain import (
    ApprovalDecision,
    ContextSnapshot,
    Instrument,
    PerformanceSnapshot,
    PositionSnapshot,
    Signal,
    OrderProposal,
    StockFundamentals,
)


class MarketDataProvider(Protocol):
    def list_instruments(self) -> list[Instrument]:
        ...

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> list:
        ...

    def get_quote(self, symbol: str) -> float:
        ...

    def get_context(self, symbols: list[str]) -> list[ContextSnapshot]:
        ...

    def list_positions(self) -> list[PositionSnapshot]:
        ...

    def get_stock_fundamentals(self, symbol: str) -> StockFundamentals:
        ...


class ApprovalService(Protocol):
    def publish_signal(self, signal: Signal, proposal: OrderProposal) -> None:
        ...

    def get_pending_decisions(self) -> list[ApprovalDecision]:
        ...


class StateStore(Protocol):
    def record_signal(self, signal: Signal) -> None:
        ...

    def record_proposal(self, proposal: OrderProposal) -> None:
        ...

    def record_decision(self, decision: ApprovalDecision) -> None:
        ...

    def has_recent_signal(self, symbol: str, timeframe: str, side: str) -> bool:
        ...

    def list_pending_proposals(self) -> list[OrderProposal]:
        ...

    def mark_expired(self, proposal_id: str) -> None:
        ...

    def get_performance(self) -> PerformanceSnapshot:
        ...
