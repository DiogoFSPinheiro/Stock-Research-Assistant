from __future__ import annotations

from html import escape

from .domain import CompanyResearchReport, Signal


SEPARATOR = "━━━━━━━━━━━━━━━━━━━━"


def html_escape(value: object) -> str:
    return escape(str(value), quote=False)


def format_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def format_signed_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1%}"


def format_price(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def format_score(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def format_company_label(symbol: str, company_name: str | None) -> str:
    if company_name:
        return f"{company_name} ({symbol})"
    return symbol


def format_display_company(symbol: str, company_name: str | None) -> str:
    if company_name:
        return f"{symbol} - {company_name}"
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
        f"Margin of safety: {format_signed_pct(getattr(signal, 'margin_of_safety', None))}",
        f"Quality score: {format_score(getattr(signal, 'quality_score', None))}",
        f"Why now: {thesis}",
        f"Main risk: {primary_risk}",
    ]


def render_shortlist(candidates: list[tuple[object, object]], requested_limit: int) -> str:
    if not candidates:
        return render_no_strong_setup()

    header = "🏆 <b>Top Research Ideas</b>"
    if len(candidates) < requested_limit:
        header = f"{header}\n{len(candidates)} of {requested_limit} ideas passed the screen"

    lines = [header, SEPARATOR, ""]
    for index, (signal, _proposal) in enumerate(candidates, start=1):
        summary = summarize_idea(signal)
        lines.append(f"<b>{index}. {html_escape(summary[0])}</b>")
        lines.append(f"🎯 {html_escape(summary[1])}")
        lines.append(f"💵 {html_escape(summary[2])}")
        lines.append(f"🛡️ {html_escape(summary[3])}")
        lines.append(f"⭐ {html_escape(summary[4])}")
        lines.append(f"🧠 {html_escape(summary[5])}")
        lines.append(f"⚠️ {html_escape(summary[6])}")
        lines.append("")
    return "\n".join(lines).strip()


def render_company_report(report: CompanyResearchReport) -> str:
    company_label = format_display_company(report.symbol, report.company_name)
    catalysts = "; ".join(report.catalysts) if report.catalysts else "No clear catalysts available."
    model_lines = list(report.model_breakdown) if report.model_breakdown else ["No valuation model breakdown available."]
    return "\n".join(
        [
            f"📊 <b>{html_escape(company_label)}</b>",
            SEPARATOR,
            "",
            "🎯 <b>Stance</b>",
            html_escape(report.recommendation),
            "",
            "💵 <b>Valuation</b>",
            f"Current price: {html_escape(format_price(report.current_price))}",
            f"Intrinsic value: {html_escape(format_price(report.intrinsic_value))}",
            f"Valuation range: {html_escape(format_price(report.valuation_low))} / {html_escape(format_price(report.valuation_base))} / {html_escape(format_price(report.valuation_high))}",
            f"Quality-adjusted fair value: {html_escape(format_price(report.quality_adjusted_value))}",
            f"Analyst target: {html_escape(format_price(report.analyst_target))}",
            f"Margin of safety: {html_escape(format_signed_pct(report.margin_of_safety))}",
            f"Model confidence: {html_escape(format_score(report.valuation_confidence))}",
            "",
            "📈 <b>Business Quality</b>",
            f"Quality score: {html_escape(format_score(report.quality_score))}",
            f"Peer context: {html_escape(report.benchmark_summary)}",
            f"Data quality: {html_escape(report.data_quality_summary)}",
            f"Investment score: {html_escape(format_score(report.investment_score))}",
            "",
            "Models",
            *[html_escape(line) for line in model_lines[:5]],
            "",
            "🧠 <b>Thesis</b>",
            html_escape(report.thesis),
            "",
            "🚀 <b>Catalysts</b>",
            html_escape(catalysts),
            "",
            "⚠️ <b>What Could Go Wrong</b>",
            html_escape(report.what_could_go_wrong),
            "",
            "👀 <b>Watchlist</b>",
            f"Status: {html_escape(report.watchlist_status)}",
            f"Suggested action: {html_escape(report.watchlist_action)}",
        ]
    )


def render_no_strong_setup(symbol: str | None = None) -> str:
    lines = ["🔎 <b>No Strong Setup Found</b>", SEPARATOR, ""]
    if symbol:
        lines.extend(
            [
                f"No strong setup found for {html_escape(symbol.upper())} right now.",
                "",
                "The company may still be worth watching, but the current valuation, timing, or risk profile is not attractive enough.",
            ]
        )
    else:
        lines.extend(
            [
                "No companies passed the research screen right now.",
                "",
                "This usually means valuation, quality, timing, or risk filters are not aligned enough for a high-conviction idea.",
                "Try again later as prices and fundamentals update.",
            ]
        )
    return "\n".join(lines)


def render_watchlist_update(symbol: str, total: int, added: bool) -> str:
    normalized_symbol = symbol.upper()
    if added:
        return "\n".join(
            [
                "👀 <b>Watchlist Updated</b>",
                SEPARATOR,
                "",
                f"{html_escape(normalized_symbol)} added to the research universe.",
                "",
                f"Total tracked stocks: {total}",
                "You can now run:",
                f"/analyze {html_escape(normalized_symbol)}",
                f"/tip {html_escape(normalized_symbol)}",
            ]
        )
    return "\n".join(
        [
            "👀 <b>Watchlist</b>",
            SEPARATOR,
            "",
            f"{html_escape(normalized_symbol)} is already on the research universe.",
            "",
            f"Total tracked stocks: {total}",
        ]
    )


def render_error(title: str, detail: object) -> str:
    return "\n".join(
        [
            f"⚠️ <b>{html_escape(title)}</b>",
            SEPARATOR,
            "",
            html_escape(detail),
        ]
    )


def render_research_cycle_started() -> str:
    return "\n".join(
        [
            "🔎 <b>Research cycle started</b>",
            SEPARATOR,
            "",
            "Building the current top research ideas.",
            "Please wait before sending more requests.",
        ]
    )


def render_portfolio_started() -> str:
    return "\n".join(
        [
            "📁 <b>Portfolio report started</b>",
            SEPARATOR,
            "",
            "Building today's portfolio performance report.",
            "Please wait before sending more requests.",
        ]
    )
