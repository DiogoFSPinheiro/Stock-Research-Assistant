from __future__ import annotations

import unittest

from xtb_trading_bot.analysis import build_stock_analysis_report
from xtb_trading_bot.domain import StockFundamentals


def fundamentals(symbol: str = "MSFT", **overrides: object) -> StockFundamentals:
    payload = {
        "symbol": symbol,
        "company_name": symbol,
        "current_price": 100.0,
        "market_cap": 1_000_000_000.0,
        "shares_outstanding": 10_000_000.0,
        "sector": "Technology",
        "trailing_pe": 20.0,
        "forward_pe": 18.0,
        "price_to_book": 4.0,
        "peg_ratio": 1.2,
        "profit_margin": 0.22,
        "operating_margin": 0.25,
        "return_on_equity": 0.20,
        "revenue_growth": 0.10,
        "earnings_growth": 0.12,
        "debt_to_equity": 40.0,
        "earnings_yield": 0.05,
        "free_cash_flow_yield": 0.06,
        "fcf_margin": 0.18,
        "net_debt_to_ebit": 1.0,
        "target_mean_price": 118.0,
    }
    payload.update(overrides)
    return StockFundamentals(**payload)


class StockAnalysisReportTests(unittest.TestCase):
    def test_builds_buy_report_for_strong_undervalued_stock(self) -> None:
        peers = [
            fundamentals(symbol="AAPL", forward_pe=24.0, free_cash_flow_yield=0.03, return_on_equity=0.18),
            fundamentals(symbol="GOOGL", forward_pe=22.0, free_cash_flow_yield=0.04, return_on_equity=0.17),
        ]

        report = build_stock_analysis_report("MSFT", fundamentals(), peers, put_call_ratio=0.8)

        self.assertEqual(report.recommendation, "BUY")
        self.assertEqual(report.company_name, "MSFT")
        self.assertGreater(report.intrinsic_value or 0.0, report.current_price)
        self.assertIn("Cheaper than", report.benchmark_summary)

    def test_builds_sell_report_for_overvalued_stock(self) -> None:
        report = build_stock_analysis_report(
            "AAPL",
            fundamentals(
                symbol="AAPL",
                current_price=140.0,
                forward_pe=35.0,
                free_cash_flow_yield=0.02,
                revenue_growth=0.01,
                earnings_growth=0.01,
                target_mean_price=130.0,
            ),
            [],
            put_call_ratio=1.3,
        )

        self.assertEqual(report.recommendation, "SELL")


if __name__ == "__main__":
    unittest.main()
