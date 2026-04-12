from __future__ import annotations

from pathlib import Path
import logging
import unittest
import uuid

from xtb_trading_bot.config import AppConfig, MarketDataConfig, RiskConfig, TelegramConfig, UniverseConfig
from xtb_trading_bot.domain import StockFundamentals
from xtb_trading_bot.instruments import InstrumentFilter
from xtb_trading_bot.market_data import SyntheticMarketDataProvider
from xtb_trading_bot.orchestrator import TradingBot
from xtb_trading_bot.risk import RiskEngine
from xtb_trading_bot.storage import JsonStateStore
from xtb_trading_bot.strategy import UndervaluedStockEngine
from xtb_trading_bot.telegram_service import TelegramApprovalService


class RichSyntheticMarketDataProvider(SyntheticMarketDataProvider):
    def get_stock_fundamentals(self, symbol: str) -> StockFundamentals:
        current = self.get_quote(symbol)
        if symbol == "AAPL":
            return StockFundamentals(symbol, current, 2.5e12, 28, 24, 9.0, 2.4, 0.22, 0.28, 1.4, 0.03, 0.04, 110, current * 1.05)
        return StockFundamentals(symbol, current, 1.8e12, 18, 15, 3.4, 1.1, 0.24, 0.27, 0.22, 0.12, 0.15, 40, current * 1.20)


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

    def test_scan_publishes_single_best_stock_pick(self) -> None:
        generated = self.bot.scan()

        self.assertEqual(generated, 1)
        proposals = self.state_store.list_pending_proposals()
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].symbol, "MSFT")

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

    def test_add_stock_updates_runtime_universe_without_restart(self) -> None:
        added, symbol, total = self.bot.add_stock("NVDA")

        self.assertTrue(added)
        self.assertEqual(symbol, "NVDA")
        self.assertEqual(total, 3)
        self.assertIn("NVDA", self.bot.config.universe.allowed_stocks)


if __name__ == "__main__":
    unittest.main()
