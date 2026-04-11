from __future__ import annotations

from pathlib import Path
import logging
import unittest

from xtb_trading_bot.config import AppConfig, MarketDataConfig, RiskConfig, TelegramConfig, UniverseConfig
from xtb_trading_bot.domain import ApprovalStatus
from xtb_trading_bot.instruments import InstrumentFilter
from xtb_trading_bot.market_data import SyntheticMarketDataProvider
from xtb_trading_bot.orchestrator import TradingBot
from xtb_trading_bot.risk import RiskEngine
from xtb_trading_bot.storage import JsonStateStore
from xtb_trading_bot.strategy import TrendSignalEngine
from xtb_trading_bot.telegram_service import TelegramApprovalService


class TradingBotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state_path = Path(".test-artifacts") / "orchestrator-state.json"
        self.state_path.parent.mkdir(exist_ok=True)
        if self.state_path.exists():
            self.state_path.unlink()
        self.config = AppConfig(
            market_data=MarketDataConfig("synthetic", "", "https://www.alphavantage.co/query", 20),
            risk=RiskConfig(100000, 0.005, 4, 0.02, 0.05, 2.0, 240),
            universe=UniverseConfig(
                allowed_fx=("EURUSD",),
                allowed_stocks=("AAPL",),
                context_symbols=("SPX500",),
                allowed_timeframes=("H4",),
            ),
            telegram=TelegramConfig("token", "chat", 30, 25, False),
            poll_seconds=300,
            log_level="INFO",
            storage_path=self.state_path,
        )
        self.state_store = JsonStateStore(self.config.storage_path)
        self.market_data = SyntheticMarketDataProvider(self.config.universe)
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
            strategy=TrendSignalEngine(self.state_store),
            risk=RiskEngine(self.config.risk),
            state_store=self.state_store,
            logger=logging.getLogger("test"),
        )

    def tearDown(self) -> None:
        if self.state_path.exists():
            self.state_path.unlink()

    def test_scan_generates_pending_proposals(self) -> None:
        generated = self.bot.scan()
        self.assertGreaterEqual(generated, 1)
        self.assertGreaterEqual(len(self.state_store.list_pending_proposals()), 1)

    def test_approved_signal_is_recorded_without_execution(self) -> None:
        self.bot.scan()
        proposal = self.state_store.list_pending_proposals()[0]
        self.approvals.receive_command(f"/approve {proposal.proposal_id}")
        processed = self.bot.process_approvals()
        self.assertEqual(processed, 1)
        decisions = self.state_store._load()["decisions"]
        self.assertEqual(decisions[-1]["status"], ApprovalStatus.APPROVED.value)

    def test_rejected_signal_is_recorded(self) -> None:
        self.bot.scan()
        proposal = self.state_store.list_pending_proposals()[0]
        self.approvals.receive_command(f"/reject {proposal.proposal_id}")
        self.bot.process_approvals()
        decisions = self.state_store._load()["decisions"]
        self.assertEqual(decisions[-1]["status"], ApprovalStatus.REJECTED.value)


if __name__ == "__main__":
    unittest.main()
