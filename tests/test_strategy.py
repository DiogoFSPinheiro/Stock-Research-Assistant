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
        current_price=price,
        market_cap=2.0e12,
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
            build_candles(100.0, 0.8, 80),
            [ContextSnapshot("SPY", AssetClass.INDEX, trend_score=0.2, risk_on=True)],
            fundamentals(164.0),
        )
        self.assertEqual(signal.side, SignalSide.BUY)
        self.assertIsNotNone(signal.entry)
        self.assertIsNotNone(signal.take_profit)

    def test_returns_no_trade_when_valuation_is_weak(self) -> None:
        weak = StockFundamentals(
            symbol="MSFT",
            current_price=164.0,
            market_cap=2.0e12,
            trailing_pe=42.0,
            forward_pe=38.0,
            price_to_book=12.0,
            peg_ratio=3.0,
            profit_margin=0.05,
            operating_margin=0.08,
            return_on_equity=0.04,
            revenue_growth=0.01,
            earnings_growth=0.01,
            debt_to_equity=260.0,
            target_mean_price=166.0,
        )
        signal = self.engine.evaluate(
            self.instrument,
            "D1",
            build_candles(100.0, 0.1, 80),
            [ContextSnapshot("SPY", AssetClass.INDEX, trend_score=0.05, risk_on=True)],
            weak,
        )
        self.assertEqual(signal.side, SignalSide.NO_TRADE)

    def test_suppresses_duplicate_stock_pick(self) -> None:
        candles = build_candles(100.0, 0.8, 80)
        first = self.engine.evaluate(
            self.instrument,
            "D1",
            candles,
            [ContextSnapshot("SPY", AssetClass.INDEX, trend_score=0.2, risk_on=True)],
            fundamentals(164.0),
        )
        self.store.record_signal(first)
        second = self.engine.evaluate(
            self.instrument,
            "D1",
            candles,
            [ContextSnapshot("SPY", AssetClass.INDEX, trend_score=0.2, risk_on=True)],
            fundamentals(164.0),
        )
        self.assertEqual(second.side, SignalSide.NO_TRADE)


if __name__ == "__main__":
    unittest.main()
