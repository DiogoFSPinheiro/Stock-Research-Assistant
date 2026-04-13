from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest

from xtb_trading_bot.config import MarketDataConfig, UniverseConfig
from xtb_trading_bot.domain import AssetClass
from xtb_trading_bot.market_data import MarketDataError, SyntheticMarketDataProvider, YFinanceMarketDataProvider


class MarketDataProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.universe = UniverseConfig(
            allowed_fx=("EURUSD",),
            allowed_stocks=("AAPL",),
            stock_universe_path=Path("config/stock_universe.txt"),
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

    def test_yfinance_daily_parse_for_fx_symbol(self) -> None:
        class FakeHistory:
            def __init__(self, rows):
                self.rows = rows
                self.empty = False

            def tail(self, count: int):
                return FakeHistory(self.rows[-count:])

            def iterrows(self):
                for timestamp, values in self.rows:
                    yield timestamp, values

        class FakeTicker:
            def __init__(self, symbol: str) -> None:
                self.symbol = symbol
                self.history_calls: list[tuple[str, str, bool]] = []

            def history(self, period: str, interval: str, auto_adjust: bool = False):
                self.history_calls.append((period, interval, auto_adjust))
                return FakeHistory(
                    [
                        (
                            datetime.fromisoformat("2026-04-10T00:00:00+00:00"),
                            {"Open": 1.10, "High": 1.12, "Low": 1.09, "Close": 1.11, "Volume": 1000},
                        ),
                        (
                            datetime.fromisoformat("2026-04-11T00:00:00+00:00"),
                            {"Open": 1.11, "High": 1.13, "Low": 1.10, "Close": 1.12, "Volume": 1200},
                        ),
                    ]
                )

        class FakeTickerFactory:
            def __init__(self) -> None:
                self.last_ticker: FakeTicker | None = None

            def __call__(self, symbol: str) -> FakeTicker:
                self.last_ticker = FakeTicker(symbol)
                return self.last_ticker

        factory = FakeTickerFactory()
        provider = YFinanceMarketDataProvider(
            MarketDataConfig("yfinance", "", "", 20),
            self.universe,
            ticker_factory=factory,
        )

        candles = provider.get_candles("EURUSD", "D1", 2)

        self.assertEqual(len(candles), 2)
        self.assertEqual([candle.close for candle in candles], [1.11, 1.12])
        self.assertEqual(candles[0].timestamp.tzinfo, timezone.utc)
        self.assertLess(candles[0].timestamp, candles[1].timestamp)
        self.assertIsNotNone(factory.last_ticker)
        self.assertEqual(factory.last_ticker.symbol, "EURUSD=X")
        self.assertEqual(factory.last_ticker.history_calls, [("1y", "1d", False)])
        self.assertIn(AssetClass.FX, {instrument.asset_class for instrument in provider.list_instruments()})

    def test_yfinance_preserves_exchange_suffix_symbols(self) -> None:
        class FakeHistory:
            empty = False

            def __init__(self) -> None:
                self.rows = [
                    (
                        datetime.fromisoformat("2026-04-11T00:00:00+00:00"),
                        {"Open": 3.9, "High": 4.1, "Low": 3.8, "Close": 4.0, "Volume": 1000},
                    )
                ]

            def tail(self, count: int):
                return self

            def iterrows(self):
                for row in self.rows:
                    yield row

        captured: list[str] = []
        provider = YFinanceMarketDataProvider(
            MarketDataConfig("yfinance", "", "", 20),
            self.universe,
            ticker_factory=lambda symbol: captured.append(symbol) or type("T", (), {"history": lambda self, **kwargs: FakeHistory()})(),
        )

        provider.get_candles("EDP.LS", "D1", 1)

        self.assertEqual(captured, ["EDP.LS"])

    def test_yfinance_maps_share_class_symbols_to_dash(self) -> None:
        class FakeHistory:
            empty = False

            def __init__(self) -> None:
                self.rows = [
                    (
                        datetime.fromisoformat("2026-04-11T00:00:00+00:00"),
                        {"Open": 500.0, "High": 505.0, "Low": 499.0, "Close": 504.0, "Volume": 1000},
                    )
                ]

            def tail(self, count: int):
                return self

            def iterrows(self):
                for row in self.rows:
                    yield row

        captured: list[str] = []
        provider = YFinanceMarketDataProvider(
            MarketDataConfig("yfinance", "", "", 20),
            self.universe,
            ticker_factory=lambda symbol: captured.append(symbol) or type("T", (), {"history": lambda self, **kwargs: FakeHistory()})(),
        )

        provider.get_candles("BRK.B", "D1", 1)

        self.assertEqual(captured, ["BRK-B"])

    def test_yfinance_wraps_history_timeout_as_market_data_error(self) -> None:
        class TimeoutTicker:
            def history(self, period: str, interval: str, auto_adjust: bool = False):
                raise TimeoutError("The read operation timed out")

        provider = YFinanceMarketDataProvider(
            MarketDataConfig("yfinance", "", "", 20),
            self.universe,
            ticker_factory=lambda symbol: TimeoutTicker(),
        )

        with self.assertRaises(MarketDataError) as ctx:
            provider.get_candles("AAPL", "D1", 10)

        self.assertIn("Unable to load price history for AAPL", str(ctx.exception))

    def test_yfinance_wraps_none_type_history_failure_as_market_data_error(self) -> None:
        class BrokenTicker:
            def history(self, period: str, interval: str, auto_adjust: bool = False):
                raise TypeError("'NoneType' object is not subscriptable")

        provider = YFinanceMarketDataProvider(
            MarketDataConfig("yfinance", "", "", 20),
            self.universe,
            ticker_factory=lambda symbol: BrokenTicker(),
        )

        with self.assertRaises(MarketDataError) as ctx:
            provider.get_candles("AAPL", "D1", 10)

        self.assertIn("Unable to load price history for AAPL", str(ctx.exception))

    def test_yfinance_reuses_cached_ticker_and_history(self) -> None:
        class FakeHistory:
            empty = False

            def __init__(self) -> None:
                self.rows = [
                    (
                        datetime.fromisoformat("2026-04-10T00:00:00+00:00"),
                        {"Open": 10.0, "High": 12.0, "Low": 9.0, "Close": 11.0, "Volume": 1000},
                    ),
                    (
                        datetime.fromisoformat("2026-04-11T00:00:00+00:00"),
                        {"Open": 11.0, "High": 13.0, "Low": 10.0, "Close": 12.0, "Volume": 1200},
                    ),
                ]

            def tail(self, count: int):
                return self

            def iterrows(self):
                for row in self.rows:
                    yield row

        class FakeTicker:
            def __init__(self) -> None:
                self.history_calls = 0

            def history(self, period: str, interval: str, auto_adjust: bool = False):
                self.history_calls += 1
                return FakeHistory()

        factory_calls: list[str] = []
        ticker = FakeTicker()
        provider = YFinanceMarketDataProvider(
            MarketDataConfig("yfinance", "", "", 20),
            self.universe,
            ticker_factory=lambda symbol: factory_calls.append(symbol) or ticker,
        )

        first = provider.get_candles("AAPL", "D1", 2)
        second = provider.get_candles("AAPL", "D1", 2)

        self.assertEqual([c.close for c in first], [11.0, 12.0])
        self.assertEqual([c.close for c in second], [11.0, 12.0])
        self.assertEqual(factory_calls, ["AAPL"])
        self.assertEqual(ticker.history_calls, 1)

    def test_yfinance_maps_extended_fundamentals_for_fair_value(self) -> None:
        class FundamentalsTicker:
            info = {
                "currentPrice": 100.0,
                "marketCap": 1_000_000_000.0,
                "sharesOutstanding": 10_000_000.0,
                "sector": "Technology",
                "trailingPE": 20.0,
                "forwardPE": 18.0,
                "priceToBook": 4.0,
                "pegRatio": 1.2,
                "profitMargins": 0.22,
                "operatingMargins": 0.25,
                "returnOnEquity": 0.20,
                "revenueGrowth": 0.10,
                "earningsGrowth": 0.12,
                "debtToEquity": 40.0,
                "targetMeanPrice": 118.0,
                "freeCashflow": 60_000_000.0,
                "enterpriseValue": 900_000_000.0,
                "totalRevenue": 300_000_000.0,
                "totalDebt": 200_000_000.0,
                "totalCash": 50_000_000.0,
                "ebitda": 100_000_000.0,
            }

        provider = YFinanceMarketDataProvider(
            MarketDataConfig("yfinance", "", "", 20),
            self.universe,
            ticker_factory=lambda symbol: FundamentalsTicker(),
        )

        data = provider.get_stock_fundamentals("AAPL")

        self.assertEqual(data.sector, "Technology")
        self.assertAlmostEqual(data.earnings_yield or 0.0, 0.05)
        self.assertAlmostEqual(data.free_cash_flow_yield or 0.0, 60_000_000 / 900_000_000)
        self.assertAlmostEqual(data.fcf_margin or 0.0, 60_000_000 / 300_000_000)
        self.assertAlmostEqual(data.net_debt_to_ebit or 0.0, 1.5)

    def test_synthetic_provider_can_build_stock_analysis(self) -> None:
        provider = SyntheticMarketDataProvider(self.universe)

        report = provider.get_stock_analysis("AAPL", ["MSFT"])

        self.assertEqual(report.symbol, "AAPL")
        self.assertIsNotNone(report.intrinsic_value)
        self.assertIn(report.recommendation, {"BUY", "HOLD", "SELL"})


if __name__ == "__main__":
    unittest.main()
