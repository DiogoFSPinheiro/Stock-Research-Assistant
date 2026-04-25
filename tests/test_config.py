from __future__ import annotations

from pathlib import Path
import os
import unittest

from stock_research_assistant.config import (
    AppConfig,
    ConfigError,
    MarketDataConfig,
    TelegramConfig,
    append_stock_to_universe,
    load_dotenv,
    load_stock_universe,
    normalize_stock_symbol,
)


class ConfigTests(unittest.TestCase):
    def test_load_dotenv_sets_missing_values_only(self) -> None:
        artifacts = Path(".test-artifacts")
        artifacts.mkdir(exist_ok=True)
        env_path = artifacts / "config-loader.env"
        env_path.write_text("TELEGRAM_BOT_TOKEN=file-token\nCUSTOM_VALUE=123\n", encoding="utf-8")
        os.environ["TELEGRAM_BOT_TOKEN"] = "existing-token"
        try:
            load_dotenv(env_path)
            self.assertEqual(os.environ["TELEGRAM_BOT_TOKEN"], "existing-token")
            self.assertEqual(os.environ["CUSTOM_VALUE"], "123")
        finally:
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("CUSTOM_VALUE", None)
            if env_path.exists():
                env_path.unlink()

    def test_validate_requires_telegram_values(self) -> None:
        config = AppConfig.from_env()
        object.__setattr__(config, "telegram", type(config.telegram)("", "", 30, 25, False))
        with self.assertRaises(ConfigError):
            config.validate()

    def test_validate_allows_yfinance_without_alpha_vantage_key(self) -> None:
        config = AppConfig.from_env()
        object.__setattr__(config, "market_data", MarketDataConfig("yfinance", "", "", 20))
        object.__setattr__(config, "telegram", TelegramConfig("token", "chat", 30, 25, False))

        config.validate()

    def test_stock_universe_can_be_loaded_from_file(self) -> None:
        artifacts = Path(".test-artifacts")
        artifacts.mkdir(exist_ok=True)
        universe_path = artifacts / "stock-universe.txt"
        universe_path.write_text("AAPL\nMSFT\n# comment\nNVDA\n", encoding="utf-8")
        os.environ["BOT_STOCK_UNIVERSE_PATH"] = str(universe_path)
        os.environ["BOT_ALLOWED_STOCKS"] = ""
        try:
            config = AppConfig.from_env()
            self.assertEqual(config.universe.allowed_stocks, ("AAPL", "MSFT", "NVDA"))
            self.assertEqual(config.universe.stock_universe_path, universe_path)
        finally:
            os.environ.pop("BOT_STOCK_UNIVERSE_PATH", None)
            os.environ.pop("BOT_ALLOWED_STOCKS", None)
            if universe_path.exists():
                universe_path.unlink()

    def test_append_stock_to_universe_adds_only_once(self) -> None:
        artifacts = Path(".test-artifacts")
        artifacts.mkdir(exist_ok=True)
        universe_path = artifacts / "stock-universe-add.txt"
        universe_path.write_text("AAPL\n", encoding="utf-8")
        try:
            added, symbol = append_stock_to_universe(universe_path, "nvda")
            duplicate, duplicate_symbol = append_stock_to_universe(universe_path, '"NVDA"')

            self.assertTrue(added)
            self.assertEqual(symbol, "NVDA")
            self.assertFalse(duplicate)
            self.assertEqual(duplicate_symbol, "NVDA")
            self.assertEqual(load_stock_universe(universe_path), ("AAPL", "NVDA"))
            self.assertEqual(normalize_stock_symbol("'msft'"), "MSFT")
        finally:
            if universe_path.exists():
                universe_path.unlink()


if __name__ == "__main__":
    unittest.main()
