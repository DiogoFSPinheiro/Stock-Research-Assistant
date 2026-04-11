from __future__ import annotations

import unittest

from xtb_trading_bot.config import MarketDataConfig, UniverseConfig
from xtb_trading_bot.domain import AssetClass
from xtb_trading_bot.market_data import AlphaVantageMarketDataProvider, SyntheticMarketDataProvider


class MarketDataProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.universe = UniverseConfig(
            allowed_fx=("EURUSD",),
            allowed_stocks=("AAPL",),
            context_symbols=("SPX500",),
            allowed_timeframes=("H4", "D1"),
        )

    def test_synthetic_provider_lists_only_supported_tradables_and_context(self) -> None:
        provider = SyntheticMarketDataProvider(self.universe)

        instruments = provider.list_instruments()

        symbols = {instrument.symbol for instrument in instruments}
        self.assertIn("EURUSD", symbols)
        self.assertIn("AAPL", symbols)
        self.assertIn("SPX500", symbols)
        self.assertIn("OIL_FUT", symbols)

    def test_alpha_vantage_daily_stock_parse(self) -> None:
        def fake_get(url: str, timeout: int) -> dict:
            self.assertIn("TIME_SERIES_DAILY_ADJUSTED", url)
            return {
                "Time Series (Daily)": {
                    "2026-04-10": {
                        "1. open": "100.0",
                        "2. high": "110.0",
                        "3. low": "99.0",
                        "4. close": "108.0",
                        "6. volume": "12345",
                    },
                    "2026-04-11": {
                        "1. open": "108.0",
                        "2. high": "112.0",
                        "3. low": "107.0",
                        "4. close": "111.0",
                        "6. volume": "22345",
                    },
                }
            }

        provider = AlphaVantageMarketDataProvider(
            MarketDataConfig("alpha_vantage", "demo", "https://www.alphavantage.co/query", 20),
            self.universe,
            http_get_json=fake_get,
        )

        candles = provider.get_candles("AAPL", "D1", 2)

        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[-1].close, 111.0)
        self.assertEqual(provider.list_instruments()[0].asset_class, AssetClass.FX)


if __name__ == "__main__":
    unittest.main()
