from __future__ import annotations

from html import escape

from .domain import CompanyResearchReport, PortfolioHolding, Signal


SEPARATOR = "━━━━━━━━━━━━━━━━━━━━"


def html_escape(value: object) -> str:
    return escape(str(value), quote=False)


def format_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def format_signed_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1%}"


def format_price(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def format_signed_price(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):,.2f}"


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


def _data_quality_label(report: CompanyResearchReport) -> str:
    if report.data_quality_score >= 0.75:
        return "High"
    if report.data_quality_score >= 0.55:
        return "Medium"
    return "Low"


def render_shortlist(reports: list[CompanyResearchReport], requested_limit: int) -> str:
    if not reports:
        return render_no_strong_setup()

    header = "🏆 <b>Top Research Ideas</b>"
    if len(reports) < requested_limit:
        header = f"{header}\n{len(reports)} of {requested_limit} ideas passed the screen"

    lines = [header, SEPARATOR, ""]
    for index, report in enumerate(reports, start=1):
        lines.append(f"<b>{index}. {html_escape(format_company_label(report.symbol, report.company_name))}</b>")
        lines.append(f"🎯 Stance: {html_escape(report.recommendation)}")
        lines.append(f"🧮 Investment score: {html_escape(format_score(report.investment_score))}")
        lines.append(f"💵 Intrinsic value: {html_escape(format_price(report.intrinsic_value))}")
        lines.append(f"🛡️ Margin of safety: {html_escape(format_signed_pct(report.margin_of_safety))}")
        lines.append(f"⭐ Quality score: {html_escape(format_score(report.quality_score))}")
        lines.append(f"📊 Data quality: {html_escape(_data_quality_label(report))} ({report.data_quality_score:.0%})")
        lines.append(f"🧠 Why now: {html_escape(report.thesis)}")
        lines.append(f"⚠️ Main risk: {html_escape(report.key_risk)}")
        lines.append("")
    return "\n".join(lines).strip()


def render_watchlist(reports: list[CompanyResearchReport], total_symbols: int) -> str:
    if not reports:
        return "\n".join(
            [
                "<b>Research Watchlist</b>",
                SEPARATOR,
                "",
                "No watchlist symbols could be analyzed right now.",
            ]
        )
    lines = ["<b>Research Watchlist</b>", SEPARATOR, f"{len(reports)} of {total_symbols} tracked stocks analyzed.", ""]
    for index, report in enumerate(reports, start=1):
        lines.extend(
            [
                f"<b>{index}. {html_escape(format_company_label(report.symbol, report.company_name))}</b>",
                f"Stance: {html_escape(report.recommendation)}",
                f"Investment score: {html_escape(format_score(report.investment_score))}",
                f"Margin of safety: {html_escape(format_signed_pct(report.margin_of_safety))}",
                f"Data quality: {html_escape(_data_quality_label(report))} ({report.data_quality_score:.0%})",
                f"Main risk: {html_escape(report.key_risk)}",
                "",
            ]
        )
    return "\n".join(lines).strip()


def render_compare(reports: list[CompanyResearchReport], requested_symbols: tuple[str, ...]) -> str:
    if len(requested_symbols) < 2:
        return render_error("Compare", "Usage: /compare AAPL MSFT")
    if not reports:
        return render_error("Compare", "No requested symbols could be analyzed right now.")
    ranked = sorted(
        reports,
        key=lambda report: (
            report.investment_score,
            report.margin_of_safety if report.margin_of_safety is not None else float("-inf"),
            report.data_quality_score,
        ),
        reverse=True,
    )
    winner = ranked[0]
    lines = [
        "<b>Stock Compare</b>",
        SEPARATOR,
        f"Best fit: {html_escape(format_company_label(winner.symbol, winner.company_name))}",
        "",
    ]
    for report in ranked:
        lines.extend(
            [
                f"<b>{html_escape(format_company_label(report.symbol, report.company_name))}</b>",
                f"Stance: {html_escape(report.recommendation)} | Score: {html_escape(format_score(report.investment_score))}",
                f"Price/Fair value: {html_escape(format_price(report.current_price))} / {html_escape(format_price(report.intrinsic_value))}",
                f"Margin of safety: {html_escape(format_signed_pct(report.margin_of_safety))}",
                f"Quality/Data: {html_escape(format_score(report.quality_score))} / {report.data_quality_score:.0%}",
                f"Risk: {html_escape(report.key_risk)}",
                "",
            ]
        )
    missing = [symbol for symbol in requested_symbols if symbol not in {report.symbol for report in reports}]
    if missing:
        lines.append(f"Unavailable: {html_escape(', '.join(missing))}")
    return "\n".join(lines).strip()


def render_portfolio_update(action: str, holding: PortfolioHolding | str, total: int, changed: bool = True) -> str:
    symbol = holding.symbol if isinstance(holding, PortfolioHolding) else holding
    detail = ""
    if isinstance(holding, PortfolioHolding) and holding.quantity is not None and holding.average_cost is not None:
        detail = f"\nQuantity: {holding.quantity:g}\nAverage cost: {html_escape(format_price(holding.average_cost))}"
    status = "updated" if changed else "unchanged"
    return "\n".join(
        [
            "<b>Portfolio Updated</b>",
            SEPARATOR,
            "",
            f"{html_escape(symbol)} {html_escape(action)} ({status}).{detail}",
            "",
            f"Total portfolio stocks: {total}",
        ]
    )


def render_portfolio_usage() -> str:
    return render_error(
        "Portfolio command",
        "Usage: /portfolio add MSFT 10 320.50, /portfolio update MSFT 12 315.00, or /portfolio remove MSFT",
    )


def render_alert_update(symbol: str, threshold: float, added: bool) -> str:
    return "\n".join(
        [
            "<b>Alert Updated</b>",
            SEPARATOR,
            "",
            f"{html_escape(symbol)} alert {'created' if added else 'updated'}.",
            f"Triggers when margin of safety reaches {html_escape(format_pct(threshold))}.",
        ]
    )


def render_alerts(alerts: list[dict]) -> str:
    if not alerts:
        return "\n".join(["<b>Alerts</b>", SEPARATOR, "", "No active alerts."])
    lines = ["<b>Alerts</b>", SEPARATOR, ""]
    for item in alerts:
        lines.append(f"{html_escape(item['symbol'])}: margin of safety >= {html_escape(format_pct(item['threshold']))}")
    return "\n".join(lines)


def render_alert_removed(symbol: str, removed: bool, total: int) -> str:
    status = "removed" if removed else "not found"
    return "\n".join(
        [
            "<b>Alerts</b>",
            SEPARATOR,
            "",
            f"{html_escape(symbol)} alert {status}.",
            f"Active alerts: {total}",
        ]
    )


def render_alert_triggered(report: CompanyResearchReport, threshold: float) -> str:
    return "\n".join(
        [
            "<b>Research Alert Triggered</b>",
            SEPARATOR,
            "",
            f"<b>{html_escape(format_company_label(report.symbol, report.company_name))}</b>",
            f"Margin of safety: {html_escape(format_signed_pct(report.margin_of_safety))}",
            f"Alert threshold: {html_escape(format_pct(threshold))}",
            f"Stance: {html_escape(report.recommendation)}",
            f"Main risk: {html_escape(report.key_risk)}",
        ]
    )


def render_quick_research_report(report: CompanyResearchReport, best_idea: bool = False) -> str:
    company_label = format_display_company(report.symbol, report.company_name)
    company_name = report.company_name or report.symbol
    title = "💡 <b>Best Research Idea Right Now</b>" if best_idea else f"💡 <b>Research Idea | {html_escape(company_name)}</b>"
    model_lines = list(report.model_breakdown)
    best_model = model_lines[0].split(":", 1)[0] if model_lines else "n/a"
    weakest_model = model_lines[-1].split(":", 1)[0] if model_lines else "n/a"
    return "\n".join(
        [
            title,
            SEPARATOR,
            *(["", html_escape(company_label), ""] if best_idea else [""]),
            f"Stance: {html_escape(report.recommendation)}",
            f"Investment score: {html_escape(format_score(report.investment_score))}",
            f"Current price: {html_escape(format_price(report.current_price))}",
            f"Intrinsic value: {html_escape(format_price(report.intrinsic_value))}",
            f"Valuation range: {html_escape(format_price(report.valuation_low))} / {html_escape(format_price(report.valuation_base))} / {html_escape(format_price(report.valuation_high))}",
            f"Margin of safety: {html_escape(format_signed_pct(report.margin_of_safety))}",
            "",
            f"Data quality: {html_escape(_data_quality_label(report))} ({report.data_quality_score:.0%})",
            f"Model confidence: {html_escape(format_score(report.valuation_confidence))}",
            f"Best supporting model: {html_escape(best_model)}",
            f"Weakest model: {html_escape(weakest_model)}",
            "",
            f"Thesis: {html_escape(report.thesis)}",
            f"Main risk: {html_escape(report.key_risk)}",
            "",
            f"Suggested action: {html_escape(report.watchlist_action)}",
        ]
    )


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
            "🧮 Models",
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
