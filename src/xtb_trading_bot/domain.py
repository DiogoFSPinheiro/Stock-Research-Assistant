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
    expected_return: float | None = None
    adjusted_return: float | None = None
    normalized_score: float | None = None
    uncertainty: float | None = None
    probability_positive: float | None = None
    horizon_days: int | None = None
    fair_value: float | None = None
    margin_of_safety: float | None = None
    quality_score: float | None = None
    timing_score: float | None = None
    risk_flags: tuple[str, ...] = ()
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
    company_name: str | None
    current_price: float
    market_cap: float | None
    shares_outstanding: float | None
    sector: str | None
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
    earnings_yield: float | None
    free_cash_flow_yield: float | None
    fcf_margin: float | None
    net_debt_to_ebit: float | None
    target_mean_price: float | None


@dataclass(frozen=True)
class StockAnalysisReport:
    symbol: str
    company_name: str | None
    current_price: float
    fcf_value: float | None
    dcf_value: float | None
    intrinsic_value: float | None
    analyst_target: float | None
    margin_of_safety: float | None
    quality_score: float
    options_sentiment: str
    options_put_call_ratio: float | None
    benchmark_summary: str
    benchmark_score: float
    recommendation: str
    key_risk: str
