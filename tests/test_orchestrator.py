from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone
import logging
import unittest
import uuid
from dataclasses import replace

from stock_research_assistant.config import AppConfig, ConfigError, MarketDataConfig, RiskConfig, TelegramConfig, UniverseConfig
from stock_research_assistant.domain import Candle, StockAnalysisReport, StockFundamentals
from stock_research_assistant.instruments import InstrumentFilter
from stock_research_assistant.market_data import MarketDataError, SyntheticMarketDataProvider
from stock_research_assistant.orchestrator import TradingBot
from stock_research_assistant.risk import RiskEngine
from stock_research_assistant.storage import JsonStateStore
from stock_research_assistant.strategy import UndervaluedStockEngine
from stock_research_assistant.telegram_service import TelegramApprovalService


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
            auto_scan_hour=9,
            auto_scan_minute=0,
            log_level="INFO",
            storage_path=self.state_path,
            portfolio_path=self.universe_path,
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
        self.assertIn("💡 <b>Research Idea | Microsoft Corporation</b>", sent_messages[0])
        self.assertNotIn("MSFT - Microsoft Corporation", sent_messages[0])
        self.assertNotIn("Entry Price:", sent_messages[0])

    def test_send_top_tips_publishes_ranked_summary(self) -> None:
        sent_messages: list[str] = []
        self.bot.approvals.http_post = lambda url, payload: sent_messages.append(payload["text"])

        generated = self.bot.send_top_tips(chat_id="chat", limit=2)

        self.assertGreaterEqual(generated, 1)
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("🏆 <b>Top Research Ideas</b>", sent_messages[0])
        self.assertIn("1.", sent_messages[0])
        self.assertIn("Microsoft Corporation (MSFT)", sent_messages[0])
        self.assertIn("Margin of safety:", sent_messages[0])
        self.assertIn("Quality score:", sent_messages[0])
        self.assertIn("Why now:", sent_messages[0])
        self.assertIn("Main risk:", sent_messages[0])

    def test_send_top_tips_explains_when_fewer_candidates_pass_than_requested(self) -> None:
        sent_messages: list[str] = []
        self.bot.approvals.http_post = lambda url, payload: sent_messages.append(payload["text"])

        generated = self.bot.send_top_tips(chat_id="chat", limit=5)

        self.assertGreaterEqual(generated, 1)
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("🏆 <b>Top Research Ideas</b>", sent_messages[0])
        self.assertIn("of 5 ideas passed the screen", sent_messages[0])

    def test_list_top_candidates_ranks_by_margin_of_safety_first(self) -> None:
        ranked = self.bot.list_top_candidates(limit=2, allow_repeat=True)

        self.assertGreaterEqual(len(ranked), 1)
        self.assertEqual(ranked[0].symbol, "MSFT")
        self.assertGreater(ranked[0].investment_score, 0.0)

    def test_list_top_candidates_returns_each_symbol_only_once(self) -> None:
        ranked = self.bot.list_top_candidates(limit=5, allow_repeat=True)

        symbols = [report.symbol for report in ranked]
        self.assertEqual(len(symbols), len(set(symbols)))

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
        self.assertIn("No companies passed the research screen", sent_messages[0])

    def test_analyze_stock_publishes_research_report_without_auto_adding_symbol(self) -> None:
        sent_messages: list[str] = []
        self.bot.approvals.http_post = lambda url, payload: sent_messages.append(payload["text"])
        original = self.bot.market_data.get_stock_analysis

        def buy_report(symbol: str, peer_symbols: list[str]) -> StockAnalysisReport:
            return replace(original(symbol, peer_symbols), recommendation="BUY")

        self.bot.market_data.get_stock_analysis = buy_report

        generated = self.bot.analyze_stock("NVDA", chat_id="chat")

        self.assertEqual(generated, 1)
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("📊 <b>NVDA - NVDA Holdings</b>", sent_messages[0])
        self.assertIn("Intrinsic value:", sent_messages[0])
        self.assertIn("Valuation range:", sent_messages[0])
        self.assertIn("Data quality:", sent_messages[0])
        self.assertIn("Models", sent_messages[0])
        self.assertIn("🎯 <b>Stance</b>\nBUY", sent_messages[0])
        self.assertIn("🧠 <b>Thesis</b>", sent_messages[0])
        self.assertIn("Suggested action:", sent_messages[0])
        self.assertNotIn("NVDA", self.bot.config.universe.allowed_stocks)

    def test_analyze_stock_keeps_universe_unchanged_when_not_buy(self) -> None:
        sent_messages: list[str] = []
        self.bot.approvals.http_post = lambda url, payload: sent_messages.append(payload["text"])
        original = self.bot.market_data.get_stock_analysis

        def hold_report(symbol: str, peer_symbols: list[str]) -> StockAnalysisReport:
            return replace(original(symbol, peer_symbols), recommendation="HOLD")

        self.bot.market_data.get_stock_analysis = hold_report

        generated = self.bot.analyze_stock("NVDA", chat_id="chat")

        self.assertEqual(generated, 1)
        self.assertNotIn("NVDA", self.bot.config.universe.allowed_stocks)
        self.assertIn("Status: Not on watchlist", sent_messages[0])
        self.assertIn("Suggested action:", sent_messages[0])

    def test_watch_stock_updates_runtime_universe_without_restart(self) -> None:
        added, symbol, total = self.bot.watch_stock("NVDA")

        self.assertTrue(added)
        self.assertEqual(symbol, "NVDA")
        self.assertEqual(total, 3)
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

    def test_send_portfolio_report_publishes_daily_moves(self) -> None:
        sent_messages: list[str] = []
        self.bot.approvals.http_post = lambda url, payload: sent_messages.append(payload["text"])

        generated = self.bot.send_portfolio_report(chat_id="chat")

        self.assertEqual(generated, 2)
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("📁 <b>Portfolio Daily Report</b>", sent_messages[0])
        self.assertIn("🟢 Up: 2  | 🔴 Down: 0  | ⚪ Neutral: 0", sent_messages[0])
        self.assertIn("Apple Inc. - AAPL", sent_messages[0])
        self.assertIn("Daily move:", sent_messages[0])
        self.assertIn("Possible reason:", sent_messages[0])

    def test_portfolio_reason_explains_up_move_with_price_action_and_fundamentals(self) -> None:
        base_time = datetime(2026, 4, 1, tzinfo=timezone.utc)
        candles = [
            Candle(base_time + timedelta(days=index), 100 + index, 101 + index, 99 + index, 100 + index, 1000)
            for index in range(21)
        ]
        candles.append(Candle(base_time + timedelta(days=21), 121, 126, 120, 125, 2400))
        fundamentals = StockFundamentals(
            "MSFT", "Microsoft Corporation", 125, 1.8e12, 7.4e9, "Technology", 18, 15, 3.4, 1.1, 0.24, 0.27, 0.22,
            0.12, 0.15, 40, 1 / 18.0, 0.060, 0.20, 1.1, 150
        )

        reason = self.bot._portfolio_reason(fundamentals, candles, (125 - 120) / 120)

        self.assertIn("heavy volume", reason)
        self.assertIn("closed near the day high", reason)
        self.assertIn("positive 5-day trend", reason)

    def test_portfolio_reason_explains_down_move_with_selling_and_risk(self) -> None:
        base_time = datetime(2026, 4, 1, tzinfo=timezone.utc)
        candles = [
            Candle(base_time + timedelta(days=index), 130 - index, 131 - index, 129 - index, 130 - index, 1000)
            for index in range(21)
        ]
        candles.append(Candle(base_time + timedelta(days=21), 109, 110, 103, 104, 2500))
        fundamentals = StockFundamentals(
            "AAPL", "Apple Inc.", 104, 2.5e12, 15.0e9, "Technology", 28, 24, 9.0, 2.4, 0.22, 0.28, 1.4,
            0.03, -0.04, 140, 1 / 28.0, 0.028, 0.09, 2.8, 95
        )

        reason = self.bot._portfolio_reason(fundamentals, candles, (104 - 109) / 109)

        self.assertIn("conviction selling", reason)
        self.assertIn("closed near the day low", reason)
        self.assertIn("weak 5-day trend", reason)

    def test_send_help_publishes_command_list(self) -> None:
        sent_messages: list[str] = []
        self.bot.approvals.http_post = lambda url, payload: sent_messages.append(payload["text"])

        generated = self.bot.send_help(chat_id="chat")

        self.assertEqual(generated, 1)
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("🤖 <b>Stock Research Assistant</b>", sent_messages[0])
        self.assertIn("/top 5 or top 5", sent_messages[0])
        self.assertIn("/analyze MSFT", sent_messages[0])
        self.assertIn("/watch NVDA", sent_messages[0])
        self.assertIn("portfolio or /portfolio", sent_messages[0])
        self.assertIn("help or /help", sent_messages[0])


if __name__ == "__main__":
    unittest.main()
