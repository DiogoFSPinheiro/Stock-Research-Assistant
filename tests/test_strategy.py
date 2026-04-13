from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from xtb_trading_bot.domain import AssetClass, Candle, ContextSnapshot, Instrument, SignalSide, StockFundamentals
from xtb_trading_bot.storage import JsonStateStore
from xtb_trading_bot.strategy import UndervaluedStockEngine


def build_candles(start: float, step: float, count: int) -> list[Candle]:
    candles: list[Candle] = []
    base_time = datetime.now(timezone.utc) - timedelta(days=count)
    for index in range(count):
        close = start + index * step
        candles.append(
            Candle(
                timestamp=base_time + timedelta(days=index),
                open=close - 0.6,
                high=close + 1.2,
                low=close - 1.0,
                close=close,
                volume=1_000_000 + index * 1_000,
            )
        )
    return candles


def fundamentals(price: float, target_multiple: float = 1.2) -> StockFundamentals:
    return StockFundamentals(
        symbol="MSFT",
        company_name="Microsoft Corporation",
        current_price=price,
        market_cap=2.0e12,
        shares_outstanding=7.4e9,
        sector="Technology",
        trailing_pe=21.0,
        forward_pe=18.0,
        price_to_book=4.1,
        peg_ratio=1.1,
        profit_margin=0.22,
        operating_margin=0.25,
        return_on_equity=0.20,
        revenue_growth=0.11,
        earnings_growth=0.13,
        debt_to_equity=35.0,
        earnings_yield=1 / 21.0,
        free_cash_flow_yield=0.055,
        fcf_margin=0.18,
        net_debt_to_ebit=1.2,
        target_mean_price=price * target_multiple,
    )


class UndervaluedStockEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state_path = Path(".test-artifacts") / "strategy-state.json"
        self.state_path.parent.mkdir(exist_ok=True)
        if self.state_path.exists():
            self.state_path.unlink()
        self.store = JsonStateStore(self.state_path)
        self.engine = UndervaluedStockEngine(self.store)
        self.instrument = Instrument("MSFT", AssetClass.STOCK, "US", True)

    def tearDown(self) -> None:
        if self.state_path.exists():
            self.state_path.unlink()

    def test_generates_buy_when_value_and_technical_filters_align(self) -> None:
        signal = self.engine.evaluate(
            self.instrument,
            "D1",
            build_candles(100.0, 0.6, 80),
            [ContextSnapshot("SPY", AssetClass.INDEX, trend_score=0.2, risk_on=True)],
            fundamentals(148.0, target_multiple=1.35),
        )
        self.assertEqual(signal.side, SignalSide.BUY)
        self.assertIsNotNone(signal.entry)
        self.assertIsNotNone(signal.take_profit)
        self.assertIsNotNone(signal.fair_value)
        self.assertGreater(signal.margin_of_safety or 0.0, 0.12)

    def test_returns_no_trade_when_quality_is_weak_even_if_cheap(self) -> None:
        weak = StockFundamentals(
            symbol="MSFT",
            company_name="Microsoft Corporation",
            current_price=164.0,
            market_cap=2.0e12,
            shares_outstanding=7.4e9,
            sector="Technology",
            trailing_pe=42.0,
            forward_pe=9.0,
            price_to_book=0.8,
            peg_ratio=0.6,
            profit_margin=0.03,
            operating_margin=0.04,
            return_on_equity=0.03,
            revenue_growth=0.01,
            earnings_growth=-0.02,
            debt_to_equity=90.0,
            earnings_yield=0.11,
            free_cash_flow_yield=0.12,
            fcf_margin=0.01,
            net_debt_to_ebit=3.0,
            target_mean_price=220.0,
        )
        signal = self.engine.evaluate(
            self.instrument,
            "D1",
            build_candles(100.0, 0.2, 80),
            [ContextSnapshot("SPY", AssetClass.INDEX, trend_score=0.05, risk_on=True)],
            weak,
        )
        self.assertEqual(signal.side, SignalSide.NO_TRADE)

    def test_returns_no_trade_when_fair_value_upside_is_too_small(self) -> None:
        limited = StockFundamentals(**{**fundamentals(150.0, target_multiple=1.02).__dict__, "earnings_yield": 0.038, "free_cash_flow_yield": 0.040})
        signal = self.engine.evaluate(
            self.instrument,
            "D1",
            build_candles(100.0, 0.5, 80),
            [ContextSnapshot("SPY", AssetClass.INDEX, trend_score=0.08, risk_on=True)],
            limited,
        )
        self.assertEqual(signal.side, SignalSide.NO_TRADE)

    def test_returns_no_trade_when_core_valuation_inputs_are_missing(self) -> None:
        missing = StockFundamentals(
            symbol="MSFT",
            company_name="Microsoft Corporation",
            current_price=164.0,
            market_cap=2.0e12,
            shares_outstanding=7.4e9,
            sector="Technology",
            trailing_pe=21.0,
            forward_pe=18.0,
            price_to_book=4.1,
            peg_ratio=1.1,
            profit_margin=0.22,
            operating_margin=0.25,
            return_on_equity=0.20,
            revenue_growth=0.11,
            earnings_growth=0.13,
            debt_to_equity=35.0,
            earnings_yield=None,
            free_cash_flow_yield=None,
            fcf_margin=0.18,
            net_debt_to_ebit=1.2,
            target_mean_price=180.0,
        )
        signal = self.engine.evaluate(
            self.instrument,
            "D1",
            build_candles(100.0, 0.6, 80),
            [ContextSnapshot("SPY", AssetClass.INDEX, trend_score=0.1, risk_on=True)],
            missing,
        )
        self.assertEqual(signal.side, SignalSide.NO_TRADE)

    def test_high_leverage_is_rejected(self) -> None:
        risky = fundamentals(148.0, target_multiple=1.35)
        risky = StockFundamentals(**{**risky.__dict__, "net_debt_to_ebit": 5.2, "debt_to_equity": 210.0})
        signal = self.engine.evaluate(
            self.instrument,
            "D1",
            build_candles(100.0, 0.6, 80),
            [ContextSnapshot("SPY", AssetClass.INDEX, trend_score=0.2, risk_on=True)],
            risky,
        )
        self.assertEqual(signal.side, SignalSide.NO_TRADE)

    def test_suppresses_duplicate_stock_pick(self) -> None:
        candles = build_candles(100.0, 0.6, 80)
        first = self.engine.evaluate(
            self.instrument,
            "D1",
            candles,
            [ContextSnapshot("SPY", AssetClass.INDEX, trend_score=0.2, risk_on=True)],
            fundamentals(148.0, target_multiple=1.35),
        )
        self.store.record_signal(first)
        second = self.engine.evaluate(
            self.instrument,
            "D1",
            candles,
            [ContextSnapshot("SPY", AssetClass.INDEX, trend_score=0.2, risk_on=True)],
            fundamentals(148.0, target_multiple=1.35),
        )
        self.assertEqual(second.side, SignalSide.NO_TRADE)


if __name__ == "__main__":
    unittest.main()
