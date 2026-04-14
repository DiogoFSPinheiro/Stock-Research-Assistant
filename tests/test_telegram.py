from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
from datetime import timezone
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "src" / "xtb_trading_bot"

package = types.ModuleType("xtb_trading_bot")
package.__path__ = [str(PACKAGE_DIR)]
sys.modules.setdefault("xtb_trading_bot", package)


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, PACKAGE_DIR / f"{name.rsplit('.', 1)[-1]}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


config_module = _load_module("xtb_trading_bot.config")
domain_module = _load_module("xtb_trading_bot.domain")
telegram_module = _load_module("xtb_trading_bot.telegram_service")

TelegramConfig = config_module.TelegramConfig
AssetClass = domain_module.AssetClass
OrderProposal = domain_module.OrderProposal
Signal = domain_module.Signal
SignalSide = domain_module.SignalSide
TelegramApprovalService = telegram_module.TelegramApprovalService
TelegramApiError = telegram_module.TelegramApiError
TelegramCommand = telegram_module.TelegramCommand
TipRequest = telegram_module.TipRequest


class TelegramApprovalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.get_calls: list[str] = []
        self.responses: list[dict] = []
        self.service = TelegramApprovalService(
            TelegramConfig(
                bot_token="token",
                chat_id="chat",
                polling_timeout_seconds=2,
                polling_limit=25,
                drop_pending_updates_on_start=False,
            ),
            signal_expiry_minutes=60,
            http_post=lambda url, payload: self.calls.append((url, payload)),
            http_get=self._http_get,
        )
        self.signal = Signal(
            signal_id="sig1",
            symbol="AAPL",
            company_name="Apple Inc.",
            asset_class=AssetClass.STOCK,
            side=SignalSide.BUY,
            timeframe="D1",
            confidence=0.8,
            rationale="Trend aligned",
            entry=100.0,
            stop_loss=95.0,
            take_profit=110.0,
        )
        self.proposal = OrderProposal(
            proposal_id="prop1",
            signal_id="sig1",
            symbol="AAPL",
            side=SignalSide.BUY,
            quantity=10,
            entry=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            estimated_risk_amount=50.0,
        )

    def _http_get(self, url: str) -> dict:
        self.get_calls.append(url)
        if not self.responses:
            return {"ok": True, "result": []}
        return self.responses.pop(0)

    def test_publish_signal_sends_message(self) -> None:
        self.service.publish_signal(self.signal, self.proposal)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("Quality-Value Stock Pick", self.calls[0][1]["text"])
        self.assertIn("Ticker: AAPL", self.calls[0][1]["text"])
        self.assertIn("Best Horizon:", self.calls[0][1]["text"])
        self.assertIn("Entry Price: 100.0000", self.calls[0][1]["text"])
        self.assertIn("Exit Price: 110.0000", self.calls[0][1]["text"])

    def test_poll_tip_requests_returns_tip_command_without_replying(self) -> None:
        self.responses.append(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 41,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/tip",
                        },
                    }
                ],
            }
        )

        results = self.service.poll_tip_requests()

        self.assertEqual(results, [TipRequest(chat_id=999, actor="alice", text="/tip")])
        self.assertEqual(self.service.last_update_id, 41)
        self.assertEqual(self.calls, [])
        self.assertTrue(self.get_calls[0].startswith("https://api.telegram.org/bottoken/getUpdates?"))
        self.assertIn("timeout=2", self.get_calls[0])
        self.assertIn("limit=25", self.get_calls[0])

    def test_poll_updates_uses_offset_after_first_batch(self) -> None:
        self.responses.extend(
            [
                {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 10,
                            "message": {
                                "chat": {"id": 999},
                                "from": {"username": "alice"},
                                "text": "/positions",
                            },
                        }
                    ],
                },
                {"ok": True, "result": []},
            ]
        )

        self.service.poll_tip_requests()
        self.service.poll_tip_requests()

        self.assertEqual(self.service.last_update_id, 10)
        self.assertIn("offset=11", self.get_calls[-1])
        self.assertEqual(self.get_calls[-1].count("offset="), 1)
        self.assertIn("timeout=2", self.get_calls[-1])

    def test_poll_tip_requests_ignores_unrelated_commands(self) -> None:
        self.responses.append(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 55,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/positions",
                        },
                    }
                ],
            }
        )

        results = self.service.poll_tip_requests()

        self.assertEqual(results, [])
        self.assertEqual(self.service.last_update_id, 55)

    def test_poll_commands_parses_add_stock(self) -> None:
        self.responses.append(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 56,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": 'add "NVDA"',
                        },
                    }
                ],
            }
        )

        results = self.service.poll_commands()

        self.assertEqual(
            results,
            [TelegramCommand(update_id=56, kind="add_stock", chat_id=999, actor="alice", text='add "NVDA"', symbol="NVDA")],
        )
        self.assertEqual(self.service.last_update_id, 56)

    def test_poll_commands_parses_top_tips_limit(self) -> None:
        self.responses.append(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 57,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/top 3",
                        },
                    }
                ],
            }
        )

        results = self.service.poll_commands()

        self.assertEqual(
            results,
            [TelegramCommand(update_id=57, kind="top_tips", chat_id=999, actor="alice", text="/top 3", symbol=None, limit=3)],
        )

    def test_poll_commands_parses_top_tips_limit_without_slash(self) -> None:
        self.responses.append(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 157,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "top 5",
                        },
                    }
                ],
            }
        )

        results = self.service.poll_commands()

        self.assertEqual(
            results,
            [TelegramCommand(update_id=157, kind="top_tips", chat_id=999, actor="alice", text="top 5", symbol=None, limit=5)],
        )

    def test_poll_commands_parses_specific_ticker_tip(self) -> None:
        self.responses.append(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 58,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/tip aapl",
                        },
                    }
                ],
            }
        )

        results = self.service.poll_commands()

        self.assertEqual(
            results,
            [TelegramCommand(update_id=58, kind="tip_for_symbol", chat_id=999, actor="alice", text="/tip aapl", symbol="AAPL", limit=None)],
        )

    def test_poll_commands_parses_stock_analysis(self) -> None:
        self.responses.append(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 59,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": 'Analise "msft"',
                        },
                    }
                ],
            }
        )

        results = self.service.poll_commands()

        self.assertEqual(
            results,
            [TelegramCommand(update_id=59, kind="stock_analysis", chat_id=999, actor="alice", text='Analise "msft"', symbol="MSFT", limit=None)],
        )

    def test_poll_commands_parses_portfolio(self) -> None:
        self.responses.append(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 159,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "portfolio",
                        },
                    }
                ],
            }
        )

        results = self.service.poll_commands()

        self.assertEqual(
            results,
            [TelegramCommand(update_id=159, kind="portfolio", chat_id=999, actor="alice", text="portfolio", symbol=None, limit=None)],
        )

    def test_poll_commands_dedupes_identical_repeated_command(self) -> None:
        self.responses.extend(
            [
                {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 60,
                            "message": {
                                "chat": {"id": 999},
                                "from": {"username": "alice"},
                                "text": "Analise MT",
                            },
                        }
                    ],
                },
                {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 61,
                            "message": {
                                "chat": {"id": 999},
                                "from": {"username": "alice"},
                                "text": "Analise MT",
                            },
                        }
                    ],
                },
            ]
        )

        first = self.service.poll_commands()
        second = self.service.poll_commands()

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    def test_poll_commands_caps_timeout_to_five_seconds(self) -> None:
        slow_service = TelegramApprovalService(
            TelegramConfig(
                bot_token="token",
                chat_id="chat",
                polling_timeout_seconds=30,
                polling_limit=25,
                drop_pending_updates_on_start=False,
            ),
            signal_expiry_minutes=60,
            http_post=lambda url, payload: None,
            http_get=self._http_get,
        )

        slow_service.poll_commands()

        self.assertIn("timeout=5", self.get_calls[-1])

    def test_poll_commands_swallows_get_updates_errors(self) -> None:
        self.service.http_get = lambda url: (_ for _ in ()).throw(TelegramApiError("Telegram getUpdates failed: timed out"))

        results = self.service.poll_commands()

        self.assertEqual(results, [])

    def test_publish_signal_propagates_readable_api_error(self) -> None:
        def fail_post(url: str, payload: dict) -> None:
            raise TelegramApiError('Telegram sendMessage failed: HTTP 400 {"ok":false,"description":"Bad Request: chat not found"}')

        service = TelegramApprovalService(
            TelegramConfig(
                bot_token="token",
                chat_id="chat",
                polling_timeout_seconds=2,
                polling_limit=25,
                drop_pending_updates_on_start=False,
            ),
            signal_expiry_minutes=60,
            http_post=fail_post,
            http_get=self._http_get,
        )

        with self.assertRaises(TelegramApiError) as ctx:
            service.publish_signal(self.signal, self.proposal)

        self.assertIn("chat not found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
