from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from xtb_trading_bot.domain import AssetClass, Candle, ContextSnapshot, Instrument, SignalSide
from xtb_trading_bot.storage import JsonStateStore
from xtb_trading_bot.strategy import TrendSignalEngine


def build_candles(start: float, step: float, count: int) -> list[Candle]:
    candles: list[Candle] = []
    base_time = datetime.now(timezone.utc) - timedelta(hours=count * 4)
    for index in range(count):
        close = start + index * step
        candles.append(
            Candle(
                timestamp=base_time + timedelta(hours=index * 4),
                open=close - 0.2,
                high=close + 0.3,
                low=close - 0.3,
                close=close,
                volume=1000 + index,
            )
        )
    return candles


class TrendSignalEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state_path = Path(".test-artifacts") / "strategy-state.json"
        self.state_path.parent.mkdir(exist_ok=True)
        if self.state_path.exists():
            self.state_path.unlink()
        self.store = JsonStateStore(self.state_path)
        self.engine = TrendSignalEngine(self.store)
        self.instrument = Instrument("EURUSD", AssetClass.FX, "GLOBAL", True)

    def tearDown(self) -> None:
        if self.state_path.exists():
            self.state_path.unlink()

    def test_generates_buy_when_trend_and_context_align(self) -> None:
        signal = self.engine.evaluate(
            self.instrument,
            "H4",
            build_candles(1.0, 0.02, 60),
            [ContextSnapshot("SPX500", AssetClass.INDEX, trend_score=0.3, risk_on=True)],
        )
        self.assertEqual(signal.side, SignalSide.BUY)
        self.assertIsNotNone(signal.stop_loss)

    def test_returns_no_trade_in_flat_market(self) -> None:
        signal = self.engine.evaluate(
            self.instrument,
            "H4",
            build_candles(1.0, 0.0001, 60),
            [ContextSnapshot("SPX500", AssetClass.INDEX, trend_score=0.1, risk_on=True)],
        )
        self.assertEqual(signal.side, SignalSide.NO_TRADE)

    def test_suppresses_duplicate_signal(self) -> None:
        candles = build_candles(1.0, 0.02, 60)
        first = self.engine.evaluate(
            self.instrument,
            "H4",
            candles,
            [ContextSnapshot("SPX500", AssetClass.INDEX, trend_score=0.3, risk_on=True)],
        )
        self.store.record_signal(first)
        second = self.engine.evaluate(
            self.instrument,
            "H4",
            candles,
            [ContextSnapshot("SPX500", AssetClass.INDEX, trend_score=0.3, risk_on=True)],
        )
        self.assertEqual(second.side, SignalSide.NO_TRADE)


if __name__ == "__main__":
    unittest.main()
