from __future__ import annotations

from pathlib import Path
import logging
import unittest
import uuid

from xtb_trading_bot.config import AppConfig, ConfigError, MarketDataConfig, RiskConfig, TelegramConfig, UniverseConfig
from xtb_trading_bot.domain import StockFundamentals
from xtb_trading_bot.instruments import InstrumentFilter
from xtb_trading_bot.market_data import MarketDataError, SyntheticMarketDataProvider
from xtb_trading_bot.orchestrator import TradingBot
from xtb_trading_bot.risk import RiskEngine
from xtb_trading_bot.storage import JsonStateStore
from xtb_trading_bot.strategy import UndervaluedStockEngine
from xtb_trading_bot.telegram_service import TelegramApprovalService


class RichSyntheticMarketDataProvider(SyntheticMarketDataProvider):
    def get_stock_fundamentals(self, symbol: str) -> StockFundamentals:
        current = self.get_quote(symbol)
        if symbol == "AAPL":
            return StockFundamentals(
                symbol, "Apple Inc.", current, 2.5e12, 15.0e9, "Technology", 28, 24, 9.0, 2.4, 0.22, 0.28, 1.4, 0.03, 0.04, 110,
                1 / 28.0, 0.028, 0.09, 2.8, current * 1.05
            )
        if symbol == "MSFT":
            return StockFundamentals(
                symbol, "Microsoft Corporation", current, 1.8e12, 7.4e9, "Technology", 18, 15, 3.4, 1.1, 0.24, 0.27, 0.22, 0.12, 0.15, 40,
                1 / 18.0, 0.060, 0.20, 1.1, current * 1.25
            )
        return StockFundamentals(
            symbol, f"{symbol} Holdings", current, 8.0e11, 6.0e9, "Technology", 20, 17, 4.5, 1.6, 0.18, 0.22, 0.16, 0.08, 0.10, 65,
            1 / 20.0, 0.045, 0.14, 1.6, current * 1.18
        )


class TradingBotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state_path = Path(".test-artifacts") / f"orchestrator-state-{uuid.uuid4().hex}.json"
        self.universe_path = Path(".test-artifacts") / f"orchestrator-universe-{uuid.uuid4().hex}.txt"
        self.state_path.parent.mkdir(exist_ok=True)
        self.universe_path.write_text("AAPL\nMSFT\n", encoding="utf-8")
        self.config = AppConfig(
            market_data=MarketDataConfig("synthetic", "", "https://www.alphavantage.co/query", 20),
            risk=RiskConfig(100000, 0.005, 4, 0.02, 0.05, 2.0, 240),
            universe=UniverseConfig(
                allowed_fx=(),
                allowed_stocks=("AAPL", "MSFT"),
                stock_universe_path=self.universe_path,
                context_symbols=("SPY",),
                allowed_timeframes=("D1",),
            ),
            telegram=TelegramConfig("token", "chat", 30, 25, False),
            poll_seconds=1800,
            log_level="INFO",
            storage_path=self.state_path,
        )
        self.state_store = JsonStateStore(self.config.storage_path)
        self.market_data = RichSyntheticMarketDataProvider(self.config.universe)
        self.approvals = TelegramApprovalService(
            self.config.telegram,
            self.config.risk.signal_expiry_minutes,
            http_post=lambda url, payload: None,
        )
        self.approvals.positions_provider = self.market_data.list_positions
        self.bot = TradingBot(
            config=self.config,
            market_data=self.market_data,
            approvals=self.approvals,
            instrument_filter=InstrumentFilter(self.config.universe),
            strategy=UndervaluedStockEngine(self.state_store),
            risk=RiskEngine(self.config.risk),
            state_store=self.state_store,
            logger=logging.getLogger("test"),
        )

    def tearDown(self) -> None:
        if self.state_path.exists():
            self.state_path.unlink()
        if self.universe_path.exists():
            self.universe_path.unlink()

    def test_scan_publishes_ranked_shortlist_by_default(self) -> None:
        generated = self.bot.scan()

        self.assertEqual(generated, 1)
        proposals = self.state_store.list_pending_proposals()
        self.assertEqual(len(proposals), 0)

    def test_signal_only_updates_are_ignored(self) -> None:
        self.approvals.http_get = lambda url: {
            "ok": True,
            "result": [
                {
                    "update_id": 12,
                    "message": {
                        "chat": {"id": 999},
                        "from": {"username": "alice"},
                        "text": "/positions",
                    },
                }
            ],
        }

        results = self.approvals.poll_tip_requests()

        self.assertEqual(results, [])
        self.assertEqual(self.approvals.last_update_id, 12)

    def test_send_tip_allows_repeat_for_manual_request(self) -> None:
        first = self.bot.send_tip()
        second = self.bot.send_tip(chat_id="chat", allow_repeat=True, notify_when_empty=True)

        self.assertEqual(first, 1)
        self.assertEqual(second, 1)

    def test_send_tip_for_specific_symbol(self) -> None:
        sent_messages: list[str] = []
        self.bot.approvals.http_post = lambda url, payload: sent_messages.append(payload["text"])

        generated = self.bot.send_tip(chat_id="chat", allow_repeat=True, notify_when_empty=True, symbol="MSFT")

        self.assertEqual(generated, 1)
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("Ticker: MSFT", sent_messages[0])

    def test_send_top_tips_publishes_ranked_summary(self) -> None:
        sent_messages: list[str] = []
        self.bot.approvals.http_post = lambda url, payload: sent_messages.append(payload["text"])

        generated = self.bot.send_top_tips(chat_id="chat", limit=2)

        self.assertGreaterEqual(generated, 1)
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("TOP QUALITY-VALUE IDEAS", sent_messages[0])
        self.assertIn("1.", sent_messages[0])
        self.assertIn("Margin of safety:", sent_messages[0])
        self.assertIn("Quality score:", sent_messages[0])

    def test_list_top_candidates_ranks_by_margin_of_safety_first(self) -> None:
        ranked = self.bot.list_top_candidates(limit=2, allow_repeat=True)

        self.assertGreaterEqual(len(ranked), 1)
        self.assertEqual(ranked[0][0].symbol, "MSFT")

    def test_send_top_tips_reports_empty_when_everything_fails_quality_screen(self) -> None:
        sent_messages: list[str] = []
        self.bot.approvals.http_post = lambda url, payload: sent_messages.append(payload["text"])
        original = self.bot.market_data.get_stock_fundamentals

        def weak(symbol: str) -> StockFundamentals:
            data = original(symbol)
            return StockFundamentals(
                data.symbol,
                data.company_name,
                data.current_price,
                data.market_cap,
                data.shares_outstanding,
                data.sector,
                data.trailing_pe,
                data.forward_pe,
                data.price_to_book,
                data.peg_ratio,
                -0.02,
                -0.01,
                0.03,
                data.revenue_growth,
                -0.05,
                220.0,
                None,
                None,
                -0.02,
                5.5,
                data.target_mean_price,
            )

        self.bot.market_data.get_stock_fundamentals = weak

        generated = self.bot.send_top_tips(chat_id="chat", limit=3)

        self.assertEqual(generated, 0)
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("No stocks passed the quality-value screen", sent_messages[0])

    def test_analyze_stock_publishes_compact_report_and_adds_symbol(self) -> None:
        sent_messages: list[str] = []
        self.bot.approvals.http_post = lambda url, payload: sent_messages.append(payload["text"])

        generated = self.bot.analyze_stock("NVDA", chat_id="chat")

        self.assertEqual(generated, 1)
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("ANALISE NVDA", sent_messages[0])
        self.assertIn("NVDA Holdings (NVDA)", sent_messages[0])
        self.assertIn("FCF model:", sent_messages[0])
        self.assertIn("DCF model:", sent_messages[0])
        self.assertIn("Intrinsic value:", sent_messages[0])
        self.assertIn("Decision:", sent_messages[0])
        self.assertIn("NVDA", self.bot.config.universe.allowed_stocks)

    def test_add_stock_updates_runtime_universe_without_restart(self) -> None:
        added, symbol, total = self.bot.add_stock("NVDA")

        self.assertTrue(added)
        self.assertEqual(symbol, "NVDA")
        self.assertEqual(total, 3)
        self.assertIn("NVDA", self.bot.config.universe.allowed_stocks)

    def test_add_stock_rejects_invalid_market_symbol(self) -> None:
        original_get_candles = self.bot.market_data.get_candles

        def fail_for_bad_symbol(symbol: str, timeframe: str, limit: int):
            if symbol == "BAD.LS":
                raise MarketDataError("No price history returned.")
            return original_get_candles(symbol, timeframe, limit)

        self.bot.market_data.get_candles = fail_for_bad_symbol

        with self.assertRaises(ConfigError):
            self.bot.add_stock("BAD.LS")

        self.assertNotIn("BAD.LS", self.bot.config.universe.allowed_stocks)


if __name__ == "__main__":
    unittest.main()
