from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AssetClass(str, Enum):
    FX = "fx"
    STOCK = "stock"
    INDEX = "index"
    COMMODITY = "commodity"
    CRYPTO = "crypto"
    FUTURE = "future"
    INSURANCE = "insurance"
    SWAP = "swap"
    OTHER = "other"


class SignalSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NO_TRADE = "NO_TRADE"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(frozen=True)
class Instrument:
    symbol: str
    asset_class: AssetClass
    market: str
    tradable: bool


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Signal:
    signal_id: str
    symbol: str
    asset_class: AssetClass
    side: SignalSide
    timeframe: str
    confidence: float
    rationale: str
    entry: float | None
    stop_loss: float | None
    take_profit: float | None
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class OrderProposal:
    proposal_id: str
    signal_id: str
    symbol: str
    side: SignalSide
    quantity: float
    entry: float
    stop_loss: float
    take_profit: float
    estimated_risk_amount: float
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class ApprovalDecision:
    proposal_id: str
    status: ApprovalStatus
    actor: str
    decided_at: datetime = field(default_factory=utc_now)
    note: str = ""


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    side: SignalSide
    quantity: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    residual_risk: float


@dataclass(frozen=True)
class ContextSnapshot:
    symbol: str
    asset_class: AssetClass
    trend_score: float
    risk_on: bool


@dataclass(frozen=True)
class PerformanceSnapshot:
    daily_pnl: float
    weekly_pnl: float


@dataclass(frozen=True)
class StockFundamentals:
    symbol: str
    current_price: float
    market_cap: float | None
    trailing_pe: float | None
    forward_pe: float | None
    price_to_book: float | None
    peg_ratio: float | None
    profit_margin: float | None
    operating_margin: float | None
    return_on_equity: float | None
    revenue_growth: float | None
    earnings_growth: float | None
    debt_to_equity: float | None
    target_mean_price: float | None
