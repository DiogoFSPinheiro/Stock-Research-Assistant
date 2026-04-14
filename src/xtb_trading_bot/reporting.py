from __future__ import annotations

from .domain import CompanyResearchReport, Signal


def format_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def format_price(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def format_score(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def format_company_label(symbol: str, company_name: str | None) -> str:
    if company_name:
        return f"{company_name} ({symbol})"
    return symbol


def format_horizon(signal: Signal) -> str:
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


def summarize_idea(signal: Signal) -> list[str]:
    company_label = format_company_label(getattr(signal, "symbol", "n/a"), getattr(signal, "company_name", None))
    thesis = getattr(signal, "rationale", "No thesis available.")
    primary_risk = "none flagged" if not getattr(signal, "risk_flags", ()) else str(getattr(signal, "risk_flags", ())[0])
    return [
        company_label,
        f"Best horizon: {format_horizon(signal)}",
        f"Fair value: {format_price(getattr(signal, 'fair_value', None))}",
        f"Margin of safety: {format_pct(getattr(signal, 'margin_of_safety', None))}",
        f"Quality score: {format_score(getattr(signal, 'quality_score', None))}",
        f"Why now: {thesis}",
        f"Main risk: {primary_risk}",
    ]


def render_shortlist(candidates: list[tuple[object, object]], requested_limit: int) -> str:
    if not candidates:
        return "TOP RESEARCH IDEAS\nNo companies passed the research screen right now."

    header = "TOP RESEARCH IDEAS"
    if len(candidates) < requested_limit:
        header = f"{header} ({len(candidates)} of {requested_limit} ideas passed the screen)"

    lines = [header, ""]
    for index, (signal, _proposal) in enumerate(candidates, start=1):
        lines.append(f"{index}. {summarize_idea(signal)[0]}")
        lines.extend(summarize_idea(signal)[1:])
        lines.append("")
    return "\n".join(lines).strip()


def render_company_report(report: CompanyResearchReport) -> str:
    company_label = format_company_label(report.symbol, report.company_name)
    catalysts = "; ".join(report.catalysts) if report.catalysts else "No clear catalysts available."
    return "\n".join(
        [
            f"RESEARCH REPORT: {company_label}",
            f"Current price: {report.current_price:.2f}",
            f"Intrinsic value: {format_price(report.intrinsic_value)}",
            f"Fair value (quality adjusted): {format_price(report.fcf_value or report.dcf_value or report.intrinsic_value)}",
            f"Analyst target: {format_price(report.analyst_target)}",
            f"Margin of safety: {format_pct(report.margin_of_safety)}",
            f"Business quality: {format_score(report.quality_score)}",
            f"Peer context: {report.benchmark_summary}",
            f"Stance: {report.recommendation}",
            f"Thesis: {report.thesis}",
            f"Catalysts: {catalysts}",
            f"What could go wrong: {report.what_could_go_wrong}",
            f"Watchlist status: {report.watchlist_status}",
            f"Suggested action: {report.watchlist_action}",
        ]
    )
