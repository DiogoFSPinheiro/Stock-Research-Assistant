from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, median

from .domain import CompanyResearchReport, StockFundamentals
from .valuation import compute_fair_value


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _score(value: float | None, low: float, high: float, inverse: bool = False) -> float:
    if value is None:
        return 0.5
    if high <= low:
        return 0.5
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
class ValuationEstimate:
    name: str
    value: float
    confidence: float
    summary: str


def _weighted_average(estimates: list[ValuationEstimate]) -> float | None:
    total_weight = sum(max(0.0, item.confidence) for item in estimates)
    if total_weight <= 0:
        return None
    return sum(item.value * max(0.0, item.confidence) for item in estimates) / total_weight


def estimate_fcf_value(fundamentals: StockFundamentals) -> float | None:
    if (
        fundamentals.current_price <= 0
        or fundamentals.free_cash_flow_yield is None
        or fundamentals.free_cash_flow_yield <= 0
    ):
        return None
    growth = _clamp(mean([value for value in (fundamentals.revenue_growth, fundamentals.earnings_growth) if value is not None] or [0.03]), 0.0, 0.12)
    target_yield = max(0.045, 0.060 - (growth * 0.08))
    quality_bonus = 1 + (_score(fundamentals.return_on_equity, 0.08, 0.24) * 0.08)
    leverage_penalty = 1 - max(0.0, ((fundamentals.net_debt_to_ebit or 1.5) - 2.0) * 0.04)
    return round(fundamentals.current_price * (fundamentals.free_cash_flow_yield / target_yield) * quality_bonus * max(0.75, leverage_penalty), 2)


def estimate_dcf_value(fundamentals: StockFundamentals) -> float | None:
    if (
        fundamentals.current_price <= 0
        or fundamentals.shares_outstanding is None
        or fundamentals.shares_outstanding <= 0
    ):
        return None
    if fundamentals.free_cash_flow is not None and fundamentals.free_cash_flow > 0:
        base_fcf = fundamentals.free_cash_flow
    elif (
        fundamentals.market_cap is not None
        and fundamentals.free_cash_flow_yield is not None
        and fundamentals.free_cash_flow_yield > 0
    ):
        base_fcf = fundamentals.market_cap * fundamentals.free_cash_flow_yield
    else:
        return None
    growth = _clamp(mean([value for value in (fundamentals.revenue_growth, fundamentals.earnings_growth) if value is not None] or [0.03]), 0.0, 0.10)
    discount_rate = 0.10 + max(0.0, ((fundamentals.net_debt_to_ebit or 1.5) - 2.0) * 0.01)
    terminal_growth = min(0.03, growth * 0.4)
    projected_value = 0.0
    fcf = base_fcf
    for year in range(1, 6):
        fcf *= 1 + growth
        projected_value += fcf / ((1 + discount_rate) ** year)
    terminal_fcf = fcf * (1 + terminal_growth)
    terminal_value = terminal_fcf / max(0.04, discount_rate - terminal_growth)
    projected_value += terminal_value / ((1 + discount_rate) ** 5)
    per_share = projected_value / fundamentals.shares_outstanding
    return round(per_share, 2) if per_share > 0 else None


def estimate_peer_value(fundamentals: StockFundamentals, peers: list[StockFundamentals]) -> ValuationEstimate | None:
    if fundamentals.current_price <= 0 or not peers:
        return None
    peer_forward_pe = [peer.forward_pe for peer in peers if peer.forward_pe is not None and peer.forward_pe > 0]
    peer_fcf = [peer.free_cash_flow_yield for peer in peers if peer.free_cash_flow_yield is not None and peer.free_cash_flow_yield > 0]
    candidates: list[float] = []
    if fundamentals.forward_pe is not None and fundamentals.forward_pe > 0 and peer_forward_pe:
        candidates.append(fundamentals.current_price * (median(peer_forward_pe) / fundamentals.forward_pe))
    if fundamentals.free_cash_flow_yield is not None and fundamentals.free_cash_flow_yield > 0 and peer_fcf:
        candidates.append(fundamentals.current_price * (fundamentals.free_cash_flow_yield / median(peer_fcf)))
    if not candidates:
        return None
    value = _clamp(mean(candidates), fundamentals.current_price * 0.25, fundamentals.current_price * 2.5)
    confidence = min(0.70, 0.25 + len(peers) * 0.07)
    return ValuationEstimate(
        name="Peer valuation",
        value=round(value, 2),
        confidence=round(confidence, 2),
        summary=f"Peer-implied value from {len(peers)} sector peer(s).",
    )


def _data_quality(
    fundamentals: StockFundamentals,
    peers: list[StockFundamentals],
    put_call_ratio: float | None,
) -> tuple[float, str]:
    checks: tuple[tuple[bool, float], ...] = (
        (fundamentals.current_price > 0, 1.0),
        (fundamentals.market_cap is not None, 0.6),
        (fundamentals.shares_outstanding is not None, 0.6),
        (fundamentals.sector is not None, 0.4),
        (fundamentals.earnings_yield is not None, 1.0),
        (fundamentals.free_cash_flow_yield is not None, 1.0),
        (fundamentals.target_mean_price is not None, 0.4),
        (fundamentals.profit_margin is not None, 0.7),
        (fundamentals.operating_margin is not None, 0.7),
        (fundamentals.return_on_equity is not None, 0.8),
        (fundamentals.revenue_growth is not None, 0.7),
        (fundamentals.earnings_growth is not None, 0.7),
        (fundamentals.fcf_margin is not None, 0.6),
        (fundamentals.debt_to_equity is not None, 0.6),
        (fundamentals.net_debt_to_ebit is not None, 0.7),
        (fundamentals.price_to_book is not None, 0.4),
        (fundamentals.free_cash_flow is not None, 0.7),
        (fundamentals.enterprise_value is not None, 0.4),
        (len(peers) >= 3, 0.8),
        (put_call_ratio is not None, 0.2),
    )
    total = sum(weight for _available, weight in checks)
    available = sum(weight for available, weight in checks if available)
    score = available / total if total else 0.0
    label = "high" if score >= 0.75 else "medium" if score >= 0.55 else "low"
    summary = f"{label.title()} data quality ({score:.0%}); {len(peers)} peer(s); options data {'available' if put_call_ratio is not None else 'unavailable'}."
    return round(score, 4), summary


def _valuation_estimates(
    fundamentals: StockFundamentals,
    peers: list[StockFundamentals],
) -> list[ValuationEstimate]:
    valuation = compute_fair_value(fundamentals)
    estimates: list[ValuationEstimate] = []
    fcf_value = estimate_fcf_value(fundamentals)
    if fcf_value is not None:
        estimates.append(ValuationEstimate("FCF yield", fcf_value, 0.65, "Free-cash-flow yield normalized for growth and leverage."))
    dcf_value = estimate_dcf_value(fundamentals)
    if dcf_value is not None:
        dcf_confidence = 0.75 if fundamentals.free_cash_flow is not None else 0.45
        estimates.append(ValuationEstimate("DCF", dcf_value, dcf_confidence, "Five-year cash-flow model with conservative terminal growth."))
    if valuation.fair_value is not None:
        estimates.append(ValuationEstimate("Quality-adjusted fair value", valuation.fair_value, 0.75, valuation.primary_reason))
    peer_value = estimate_peer_value(fundamentals, peers)
    if peer_value is not None:
        estimates.append(peer_value)
    if fundamentals.target_mean_price is not None and fundamentals.target_mean_price > 0:
        estimates.append(ValuationEstimate("Analyst target", fundamentals.target_mean_price, 0.25, "External analyst consensus; useful, but low-control input."))
    return estimates


def _valuation_range(base_value: float | None, estimates: list[ValuationEstimate], confidence: float) -> tuple[float | None, float | None, float | None]:
    if base_value is None:
        return None, None, None
    values = [item.value for item in estimates]
    spread = max(0.08, 0.30 * (1 - confidence))
    low = min(values) if values else base_value * (1 - spread)
    high = max(values) if values else base_value * (1 + spread)
    low = min(low, base_value * (1 - spread))
    high = max(high, base_value * (1 + spread))
    return round(max(0.0, low), 2), round(base_value, 2), round(high, 2)


def _peer_summary(fundamentals: StockFundamentals, peers: list[StockFundamentals]) -> tuple[str, float]:
    if not peers:
        return "No close peer benchmark available", 0.5
    peer_forward_pe = [peer.forward_pe for peer in peers if peer.forward_pe is not None]
    peer_roe = [peer.return_on_equity for peer in peers if peer.return_on_equity is not None]
    peer_growth = [peer.revenue_growth for peer in peers if peer.revenue_growth is not None]
    peer_fcf = [peer.free_cash_flow_yield for peer in peers if peer.free_cash_flow_yield is not None]
    value_score = mean(
        [
            _score(fundamentals.forward_pe, 10, median(peer_forward_pe) if peer_forward_pe else 24, inverse=True),
            _score(fundamentals.free_cash_flow_yield, median(peer_fcf) if peer_fcf else 0.03, 0.08),
            _score(fundamentals.return_on_equity, median(peer_roe) if peer_roe else 0.10, 0.25),
            _score(fundamentals.revenue_growth, median(peer_growth) if peer_growth else 0.03, 0.15),
        ]
    )
    cheaper = 0
    for peer in peers:
        if peer.forward_pe is not None and fundamentals.forward_pe is not None and fundamentals.forward_pe <= peer.forward_pe:
            cheaper += 1
    summary = f"Cheaper than {cheaper}/{len(peers)} peers with similar sector quality"
    return summary, round(value_score, 4)


def _options_sentiment_from_ratio(put_call_ratio: float | None) -> str:
    if put_call_ratio is None:
        return "Neutral"
    if put_call_ratio <= 0.85:
        return "Bullish"
    if put_call_ratio >= 1.15:
        return "Bearish"
    return "Neutral"


def _build_thesis(
    fundamentals: StockFundamentals,
    margin_of_safety: float | None,
    benchmark_score: float,
    valuation_reason: str,
) -> str:
    company_label = fundamentals.company_name or fundamentals.symbol
    if margin_of_safety is None:
        return f"{company_label} needs more complete valuation inputs before a clear thesis can be formed."
    if margin_of_safety >= 0.2 and benchmark_score >= 0.55:
        return f"{company_label} screens as an undervalued quality business with supportive peer-relative fundamentals."
    if margin_of_safety >= 0.1:
        return f"{company_label} looks modestly undervalued, supported mainly by {valuation_reason.lower()}."
    if margin_of_safety <= -0.1:
        return f"{company_label} looks fully valued to expensive versus the current quality and growth profile."
    return f"{company_label} is close to fair value, so the case depends on execution improving from here."


def _build_catalysts(
    fundamentals: StockFundamentals,
    margin_of_safety: float | None,
    options_sentiment: str,
) -> tuple[str, ...]:
    catalysts: list[str] = []
    if fundamentals.revenue_growth is not None and fundamentals.revenue_growth > 0.08:
        catalysts.append("Revenue growth remains strong enough to support multiple expansion.")
    if fundamentals.earnings_growth is not None and fundamentals.earnings_growth > 0.1:
        catalysts.append("Earnings growth is healthy, which could unlock upside if it holds.")
    if fundamentals.free_cash_flow_yield is not None and fundamentals.free_cash_flow_yield > 0.05:
        catalysts.append("Free-cash-flow yield is attractive relative to a typical quality compounder.")
    if margin_of_safety is not None and margin_of_safety > 0.15:
        catalysts.append("A healthy margin of safety gives room for sentiment to improve.")
    if options_sentiment == "Bullish":
        catalysts.append("Options positioning is leaning constructive near-term.")
    if not catalysts:
        catalysts.append("No strong catalyst stands out from the available dataset.")
    return tuple(catalysts[:3])


def _build_watchlist_action(recommendation: str, margin_of_safety: float | None) -> str:
    if recommendation == "BUY":
        return "Add to watchlist now"
    if margin_of_safety is not None and margin_of_safety >= 0.05:
        return "Keep on watchlist"
    if recommendation == "SELL":
        return "Do not add to watchlist"
    return "Review on the next earnings update"


def build_stock_analysis_report(
    symbol: str,
    fundamentals: StockFundamentals,
    peers: list[StockFundamentals],
    put_call_ratio: float | None,
) -> CompanyResearchReport:
    valuation = compute_fair_value(fundamentals)
    estimates = _valuation_estimates(fundamentals, peers)
    fcf_value = estimate_fcf_value(fundamentals)
    dcf_value = estimate_dcf_value(fundamentals)
    intrinsic_value = _weighted_average(estimates)
    intrinsic_value = round(intrinsic_value, 2) if intrinsic_value is not None else None
    margin_of_safety = None if intrinsic_value is None else round((intrinsic_value - fundamentals.current_price) / fundamentals.current_price, 6)
    benchmark_summary, benchmark_score = _peer_summary(fundamentals, peers)
    options_sentiment = _options_sentiment_from_ratio(put_call_ratio)
    data_quality_score, data_quality_summary = _data_quality(fundamentals, peers, put_call_ratio)
    valuation_confidence = round(min(0.95, (sum(item.confidence for item in estimates) / 3.0) * data_quality_score), 4)
    valuation_low, valuation_base, valuation_high = _valuation_range(intrinsic_value, estimates, valuation_confidence)

    options_score = 0.65 if options_sentiment == "Bullish" else 0.5 if options_sentiment == "Neutral" else 0.3
    recommendation_score = (
        _score(margin_of_safety, -0.05, 0.25) * 0.35
        + valuation.quality_score * 0.25
        + benchmark_score * 0.15
        + data_quality_score * 0.15
        + valuation_confidence * 0.05
        + options_score * 0.05
    )
    if intrinsic_value is None:
        recommendation = "HOLD"
    elif (margin_of_safety or 0.0) >= 0.15 and recommendation_score >= 0.62 and data_quality_score >= 0.55:
        recommendation = "BUY"
    elif (margin_of_safety or 0.0) <= -0.10 or recommendation_score < 0.42:
        recommendation = "SELL"
    else:
        recommendation = "HOLD"

    if "high_net_debt" in valuation.risk_flags or "high_debt_to_equity" in valuation.risk_flags:
        key_risk = "Debt load is the main risk"
    elif options_sentiment == "Bearish":
        key_risk = "Options traders are leaning bearish"
    elif benchmark_score < 0.45:
        key_risk = "Peers look stronger on value or quality"
    else:
        key_risk = valuation.primary_risk

    thesis = _build_thesis(fundamentals, margin_of_safety, benchmark_score, valuation.primary_reason)
    catalysts = _build_catalysts(fundamentals, margin_of_safety, options_sentiment)
    watchlist_action = _build_watchlist_action(recommendation, margin_of_safety)
    if recommendation == "BUY":
        watchlist_status = "High-priority watchlist candidate"
    elif recommendation == "HOLD":
        watchlist_status = "Watchlist candidate"
    else:
        watchlist_status = "Not a priority watchlist candidate"
    what_could_go_wrong = key_risk

    return CompanyResearchReport(
        symbol=symbol,
        company_name=fundamentals.company_name,
        current_price=round(fundamentals.current_price, 2),
        fcf_value=fcf_value,
        dcf_value=dcf_value,
        intrinsic_value=intrinsic_value,
        analyst_target=fundamentals.target_mean_price,
        margin_of_safety=margin_of_safety,
        quality_score=valuation.quality_score,
        options_sentiment=options_sentiment,
        options_put_call_ratio=None if put_call_ratio is None else round(put_call_ratio, 2),
        benchmark_summary=benchmark_summary,
        benchmark_score=benchmark_score,
        recommendation=recommendation,
        key_risk=key_risk,
        thesis=thesis,
        catalysts=catalysts,
        what_could_go_wrong=what_could_go_wrong,
        watchlist_status=watchlist_status,
        watchlist_action=watchlist_action,
        valuation_low=valuation_low,
        valuation_base=valuation_base,
        valuation_high=valuation_high,
        quality_adjusted_value=valuation.fair_value,
        data_quality_score=data_quality_score,
        data_quality_summary=data_quality_summary,
        model_breakdown=tuple(f"{item.name}: {_fmt(item.value)} ({item.confidence:.0%} confidence)" for item in estimates),
        investment_score=round(recommendation_score, 4),
        valuation_confidence=valuation_confidence,
    )
