from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from .domain import StockFundamentals


def _bounded_score(value: float | None, low: float, high: float, inverse: bool = False) -> float:
    if value is None:
        return 0.0
    if inverse:
        if value <= low:
            return 1.0
        if value >= high:
            return 0.0
        return 1 - ((value - low) / (high - low))
    if value <= low:
        return 0.0
    if value >= high:
        return 1.0
    return (value - low) / (high - low)


@dataclass(frozen=True)
class ValuationAnalysis:
    fair_value: float | None
    base_fair_value: float | None
    margin_of_safety: float | None
    quality_score: float
    balance_sheet_score: float
    timing_score: float
    upside_after_adjustment: float | None
    risk_flags: tuple[str, ...]
    primary_reason: str
    primary_risk: str


def _safe_mean(values: list[float]) -> float:
    return mean(values) if values else 0.0


def compute_fair_value(fundamentals: StockFundamentals) -> ValuationAnalysis:
    price = fundamentals.current_price
    if price <= 0:
        return ValuationAnalysis(None, None, None, 0.0, 0.0, 0.0, None, ("invalid_price",), "Invalid price", "Invalid price")

    risk_flags: list[str] = []
    growth_inputs = [value for value in (fundamentals.revenue_growth, fundamentals.earnings_growth) if value is not None]
    normalized_growth = min(0.12, max(0.0, _safe_mean(growth_inputs))) if growth_inputs else 0.0

    earnings_power = None
    if fundamentals.earnings_yield is not None and fundamentals.earnings_yield > 0:
        target_earnings_yield = max(0.04, 0.055 - normalized_growth * 0.10)
        earnings_power = price * (fundamentals.earnings_yield / target_earnings_yield)

    fcf_power = None
    if fundamentals.free_cash_flow_yield is not None and fundamentals.free_cash_flow_yield > 0:
        target_fcf_yield = max(0.045, 0.060 - normalized_growth * 0.08)
        fcf_power = price * (fundamentals.free_cash_flow_yield / target_fcf_yield)

    fair_value_candidates = [value for value in (earnings_power, fcf_power, fundamentals.target_mean_price) if value is not None and value > 0]
    base_fair_value = _safe_mean(fair_value_candidates) if fair_value_candidates else None

    quality_score = _safe_mean(
        [
            _bounded_score(fundamentals.return_on_equity, 0.08, 0.24),
            _bounded_score(fundamentals.profit_margin, 0.06, 0.24),
            _bounded_score(fundamentals.operating_margin, 0.08, 0.28),
            _bounded_score(fundamentals.revenue_growth, 0.02, 0.14),
            _bounded_score(fundamentals.earnings_growth, 0.02, 0.16),
            _bounded_score(fundamentals.fcf_margin, 0.04, 0.18),
        ]
    )
    balance_sheet_score = _safe_mean(
        [
            _bounded_score(fundamentals.debt_to_equity, 20, 140, inverse=True),
            _bounded_score(fundamentals.net_debt_to_ebit, 0.5, 3.5, inverse=True),
            _bounded_score(fundamentals.price_to_book, 1.0, 8.0, inverse=True),
        ]
    )

    if fundamentals.profit_margin is not None and fundamentals.profit_margin < 0:
        risk_flags.append("negative_margin")
    if fundamentals.operating_margin is not None and fundamentals.operating_margin < 0:
        risk_flags.append("negative_operating_margin")
    if fundamentals.return_on_equity is not None and fundamentals.return_on_equity < 0.06:
        risk_flags.append("weak_roe")
    if fundamentals.earnings_growth is not None and fundamentals.earnings_growth < 0:
        risk_flags.append("shrinking_earnings")
    if fundamentals.fcf_margin is not None and fundamentals.fcf_margin < 0:
        risk_flags.append("negative_fcf_margin")
    if fundamentals.debt_to_equity is not None and fundamentals.debt_to_equity > 180:
        risk_flags.append("high_debt_to_equity")
    if fundamentals.net_debt_to_ebit is not None and fundamentals.net_debt_to_ebit > 4.0:
        risk_flags.append("high_net_debt")

    leverage_penalty = 1.0
    if fundamentals.debt_to_equity is not None:
        leverage_penalty -= max(0.0, min(0.35, (fundamentals.debt_to_equity - 80) / 400))
    if fundamentals.net_debt_to_ebit is not None:
        leverage_penalty -= max(0.0, min(0.25, (fundamentals.net_debt_to_ebit - 2.0) / 8))
    leverage_penalty = max(0.45, leverage_penalty)

    quality_penalty = 1.0 - max(0.0, 0.25 - quality_score) * 1.2
    quality_penalty = max(0.55, min(1.0, quality_penalty))

    fair_value = None if base_fair_value is None else base_fair_value * leverage_penalty * quality_penalty
    margin_of_safety = None if fair_value is None else (fair_value - price) / price
    upside_after_adjustment = margin_of_safety

    if fair_value is None:
        primary_reason = "Missing fair-value inputs"
    elif fcf_power is not None and earnings_power is not None:
        primary_reason = "Cash flow and earnings both imply upside"
    elif fcf_power is not None:
        primary_reason = "Cash flow yield implies upside"
    elif earnings_power is not None:
        primary_reason = "Earnings yield implies upside"
    else:
        primary_reason = "Analyst target implies upside"

    if "high_net_debt" in risk_flags or "high_debt_to_equity" in risk_flags:
        primary_risk = "Leverage could turn cheap into a value trap"
    elif "shrinking_earnings" in risk_flags:
        primary_risk = "Earnings trend is weakening"
    elif "weak_roe" in risk_flags:
        primary_risk = "Business quality is below the target bar"
    else:
        primary_risk = "Fair value depends on growth and margin stability"

    return ValuationAnalysis(
        fair_value=None if fair_value is None else round(fair_value, 4),
        base_fair_value=None if base_fair_value is None else round(base_fair_value, 4),
        margin_of_safety=None if margin_of_safety is None else round(margin_of_safety, 6),
        quality_score=round(quality_score, 4),
        balance_sheet_score=round(balance_sheet_score, 4),
        timing_score=0.0,
        upside_after_adjustment=None if upside_after_adjustment is None else round(upside_after_adjustment, 6),
        risk_flags=tuple(risk_flags),
        primary_reason=primary_reason,
        primary_risk=primary_risk,
    )
