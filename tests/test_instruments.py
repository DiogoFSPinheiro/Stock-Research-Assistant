from __future__ import annotations

from pathlib import Path
import unittest

from stock_research_assistant.config import UniverseConfig
from stock_research_assistant.domain import AssetClass, Instrument
from stock_research_assistant.instruments import InstrumentFilter


class InstrumentFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.filter = InstrumentFilter(
            UniverseConfig(
                allowed_fx=("EURUSD",),
                allowed_stocks=("AAPL",),
                stock_universe_path=Path("config/stock_universe.txt"),
                context_symbols=("SPX500",),
                allowed_timeframes=("H4", "D1"),
            )
        )

    def test_accepts_allowed_fx_and_stock(self) -> None:
        self.assertTrue(self.filter.can_trade(Instrument("EURUSD", AssetClass.FX, "GLOBAL", True)))
        self.assertTrue(self.filter.can_trade(Instrument("AAPL", AssetClass.STOCK, "US", True)))

    def test_rejects_banned_classes(self) -> None:
        self.assertFalse(self.filter.can_trade(Instrument("OIL_FUT", AssetClass.FUTURE, "GLOBAL", True)))
        self.assertFalse(self.filter.can_trade(Instrument("SWAPX", AssetClass.SWAP, "GLOBAL", True)))

    def test_context_is_observed_but_not_tradable(self) -> None:
        instrument = Instrument("SPX500", AssetClass.INDEX, "GLOBAL", False)
        self.assertTrue(self.filter.can_analyze(instrument))
        self.assertFalse(self.filter.can_trade(instrument))


if __name__ == "__main__":
    unittest.main()
