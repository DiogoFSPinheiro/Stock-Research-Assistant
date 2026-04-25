from __future__ import annotations

import unittest

from stock_research_assistant.domain import StockFundamentals
from stock_research_assistant.valuation import compute_fair_value


def fundamentals(**overrides: object) -> StockFundamentals:
    payload = {
        "symbol": "MSFT",
        "company_name": "Microsoft Corporation",
        "current_price": 100.0,
        "market_cap": 2.0e12,
        "shares_outstanding": 7.4e9,
        "sector": "Technology",
        "trailing_pe": 20.0,
        "forward_pe": 18.0,
        "price_to_book": 4.0,
        "peg_ratio": 1.2,
        "profit_margin": 0.22,
        "operating_margin": 0.26,
        "return_on_equity": 0.21,
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


class FairValueTests(unittest.TestCase):
    def test_conservative_growth_caps_fair_value(self) -> None:
        normal = compute_fair_value(fundamentals(revenue_growth=0.08, earnings_growth=0.10))
        aggressive = compute_fair_value(fundamentals(revenue_growth=0.30, earnings_growth=0.35))

        self.assertIsNotNone(normal.fair_value)
        self.assertIsNotNone(aggressive.fair_value)
        self.assertLess((aggressive.fair_value or 0) - (normal.fair_value or 0), 40.0)

    def test_margin_of_safety_is_positive_when_fair_value_exceeds_price(self) -> None:
        analysis = compute_fair_value(fundamentals())

        self.assertGreater(analysis.margin_of_safety or 0.0, 0.0)

    def test_weak_balance_sheet_reduces_fair_value(self) -> None:
        strong = compute_fair_value(fundamentals(debt_to_equity=25.0, net_debt_to_ebit=0.8))
        weak = compute_fair_value(fundamentals(debt_to_equity=190.0, net_debt_to_ebit=4.5))

        self.assertGreater(strong.fair_value or 0.0, weak.fair_value or 0.0)
        self.assertIn("high_net_debt", weak.risk_flags)

    def test_missing_optional_fields_is_stable(self) -> None:
        analysis = compute_fair_value(
            fundamentals(
                target_mean_price=None,
                free_cash_flow_yield=None,
                fcf_margin=None,
                sector=None,
            )
        )

        self.assertIsNotNone(analysis.fair_value)
        self.assertGreaterEqual(analysis.quality_score, 0.0)


if __name__ == "__main__":
    unittest.main()
