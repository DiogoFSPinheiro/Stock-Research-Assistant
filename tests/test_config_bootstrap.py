from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import sys
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stock_research_assistant.app import build_application
from stock_research_assistant.config import AppConfig
from stock_research_assistant.market_data import SyntheticMarketDataProvider


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        if key:
            values[key.strip()] = value.strip()
    return values


@contextmanager
def _temp_env(**updates: str) -> None:
    with patch.dict(os.environ, updates, clear=True):
        yield


class ConfigBootstrapTests(unittest.TestCase):
    def test_app_config_uses_safe_defaults_and_trims_csv(self) -> None:
        with _temp_env(
            MARKET_DATA_PROVIDER="yfinance",
            ALPHA_VANTAGE_API_KEY="",
            BOT_STOCK_UNIVERSE_PATH="",
            TELEGRAM_BOT_TOKEN="",
            TELEGRAM_CHAT_ID="",
            BOT_ALLOWED_FX=" EURUSD , GBPUSD ,, USDJPY ",
            BOT_ALLOWED_STOCKS=" AAPL , MSFT , ",
            BOT_CONTEXT_SYMBOLS=" SPX500 , GOLD ",
            BOT_ALLOWED_TIMEFRAMES=" H4 , D1 ",
            BOT_PORTFOLIO_PATH="config/portfolio.txt",
            TELEGRAM_DROP_PENDING_UPDATES_ON_START="yes",
        ):
            config = AppConfig.from_env()

        self.assertEqual(config.market_data.provider, "yfinance")
        self.assertEqual(config.market_data.alpha_vantage_api_key, "")
        self.assertEqual(config.risk.capital, 100000.0)
        self.assertEqual(config.risk.risk_per_trade, 0.005)
        self.assertEqual(config.risk.max_open_positions, 4)
        self.assertEqual(config.universe.allowed_fx, ("EURUSD", "GBPUSD", "USDJPY"))
        self.assertEqual(config.universe.allowed_stocks, ("AAPL", "MSFT"))
        self.assertEqual(config.universe.context_symbols, ("SPX500", "GOLD"))
        self.assertEqual(config.universe.allowed_timeframes, ("H4", "D1"))
        self.assertEqual(config.telegram.bot_token, "")
        self.assertEqual(config.telegram.chat_id, "")
        self.assertTrue(config.telegram.drop_pending_updates_on_start)
        self.assertEqual(config.poll_seconds, 300)
        self.assertEqual(config.auto_scan_hour, 9)
        self.assertEqual(config.auto_scan_minute, 0)
        self.assertEqual(config.storage_path, Path("data/state.json"))
        self.assertEqual(config.portfolio_path, Path("config/portfolio.txt"))

    def test_build_application_bootstraps_synthetic_mode_without_network(self) -> None:
        class FakeApprovalService:
            def __init__(self, config, signal_expiry_minutes):
                self.config = config
                self.signal_expiry_minutes = signal_expiry_minutes
                self.positions_provider = None
                self.published: list[tuple[object, object]] = []
                self.published_text: list[str] = []

            def publish_signal(self, signal, proposal) -> None:
                self.published.append((signal, proposal))

            def publish_text(self, text, chat_id=None) -> None:
                self.published_text.append(text)

            def get_pending_decisions(self) -> list[object]:
                return []

        storage_path = Path(".test-artifacts") / f"bootstrap-state-{uuid.uuid4().hex}.json"
        storage_path.parent.mkdir(exist_ok=True)
        self.addCleanup(lambda: storage_path.unlink() if storage_path.exists() else None)
        if storage_path.exists():
            storage_path.unlink()
        with _temp_env(
            BOT_STORAGE_PATH=str(storage_path),
            MARKET_DATA_PROVIDER="synthetic",
            TELEGRAM_BOT_TOKEN="dummy-token",
            TELEGRAM_CHAT_ID="123456789",
        ), patch("stock_research_assistant.app.TelegramApprovalService", FakeApprovalService):
            bot = build_application()

        self.assertIsInstance(bot.market_data, SyntheticMarketDataProvider)
        self.assertEqual(bot.config.storage_path, storage_path)
        self.assertEqual(bot.approvals.config.bot_token, "dummy-token")
        self.assertEqual(bot.approvals.config.chat_id, "123456789")
        generated = bot.scan()
        self.assertGreaterEqual(generated, 0)
        self.assertEqual(len(bot.state_store.list_pending_proposals()), 0)
        self.assertGreaterEqual(len(bot.approvals.published_text), 1)

    def test_env_example_matches_safe_local_bootstrap_assumptions(self) -> None:
        values = _parse_env_file(Path(".env.example"))

        self.assertNotIn("BOT_MODE", values)
        self.assertEqual(values["BOT_PORTFOLIO_PATH"], "config/portfolio.txt")
        self.assertEqual(values["MARKET_DATA_PROVIDER"], "yfinance")
        self.assertEqual(values["TELEGRAM_BOT_TOKEN"], "replace-me")
        self.assertEqual(values["TELEGRAM_CHAT_ID"], "replace-me")
        self.assertNotIn("YFINANCE_PERIOD", values)
        self.assertNotIn("BOT_ALLOWED_TIMEFRAMES", values)


if __name__ == "__main__":
    unittest.main()
