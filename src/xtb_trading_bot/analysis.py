from __future__ import annotations

from statistics import mean, median

from .domain import CompanyResearchReport, StockFundamentals
from .valuation import compute_fair_value


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _score(value: float | None, low: float, high: float, inverse: bool = False) -> float:
    if value is None:
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
        or fundamentals.market_cap is None
        or fundamentals.shares_outstanding is None
        or fundamentals.shares_outstanding <= 0
        or fundamentals.free_cash_flow_yield is None
        or fundamentals.free_cash_flow_yield <= 0
    ):
        return None
    base_fcf = fundamentals.market_cap * fundamentals.free_cash_flow_yield
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
    fcf_value = estimate_fcf_value(fundamentals)
    dcf_value = estimate_dcf_value(fundamentals)
    intrinsic_candidates = [value for value in (fcf_value, dcf_value, valuation.fair_value, fundamentals.target_mean_price) if value is not None]
    intrinsic_value = round(mean(intrinsic_candidates), 2) if intrinsic_candidates else None
    margin_of_safety = None if intrinsic_value is None else round((intrinsic_value - fundamentals.current_price) / fundamentals.current_price, 6)
    benchmark_summary, benchmark_score = _peer_summary(fundamentals, peers)
    options_sentiment = _options_sentiment_from_ratio(put_call_ratio)

    recommendation_score = mean(
        [
            _score(margin_of_safety, -0.05, 0.25),
            valuation.quality_score,
            benchmark_score,
            0.65 if options_sentiment == "Bullish" else 0.5 if options_sentiment == "Neutral" else 0.3,
        ]
    )
    if intrinsic_value is None:
        recommendation = "HOLD"
    elif (margin_of_safety or 0.0) >= 0.15 and recommendation_score >= 0.62:
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
    )
