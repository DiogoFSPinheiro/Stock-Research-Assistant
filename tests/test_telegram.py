from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
from datetime import timezone
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "src" / "stock_research_assistant"

package = types.ModuleType("stock_research_assistant")
package.__path__ = [str(PACKAGE_DIR)]
sys.modules.setdefault("stock_research_assistant", package)


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, PACKAGE_DIR / f"{name.rsplit('.', 1)[-1]}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


config_module = _load_module("stock_research_assistant.config")
domain_module = _load_module("stock_research_assistant.domain")
telegram_module = _load_module("stock_research_assistant.telegram_service")

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
        self.assertEqual(self.calls[0][1]["parse_mode"], "HTML")
        self.assertIn("💡 <b>Research Idea</b>", self.calls[0][1]["text"])
        self.assertIn("<b>AAPL - Apple Inc.</b>", self.calls[0][1]["text"])
        self.assertIn("Best horizon:", self.calls[0][1]["text"])
        self.assertIn("Research window: D1", self.calls[0][1]["text"])
        self.assertNotIn("Entry Price:", self.calls[0][1]["text"])

    def test_publish_text_splits_long_messages(self) -> None:
        long_lines = [f"<b>Item {index}</b> " + ("x" * 180) for index in range(60)]

        self.service.publish_text("\n".join(long_lines), chat_id=999)

        self.assertGreater(len(self.calls), 1)
        self.assertTrue(all(len(payload["text"]) <= 3900 for _url, payload in self.calls))
        self.assertIn("<b>Item 0</b>", self.calls[0][1]["text"])
        self.assertIn("<b>Item 59</b>", self.calls[-1][1]["text"])

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

    def test_poll_commands_parses_watchlist_alias(self) -> None:
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
            [TelegramCommand(update_id=56, kind="watch_stock", chat_id=999, actor="alice", text='add "NVDA"', symbol="NVDA")],
        )
        self.assertEqual(self.service.last_update_id, 56)

    def test_poll_commands_parses_watch_stock(self) -> None:
        self.responses.append(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 156,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": '/watch "NVDA"',
                        },
                    }
                ],
            }
        )

        results = self.service.poll_commands()

        self.assertEqual(
            results,
            [TelegramCommand(update_id=156, kind="watch_stock", chat_id=999, actor="alice", text='/watch "NVDA"', symbol="NVDA")],
        )

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

    def test_poll_commands_parses_portfolio_add_update_and_remove(self) -> None:
        self.responses.append(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 161,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/portfolio add msft 10 320.50",
                        },
                    },
                    {
                        "update_id": 162,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/portfolio update MSFT 12 315",
                        },
                    },
                    {
                        "update_id": 163,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/portfolio remove MSFT",
                        },
                    },
                ],
            }
        )

        results = self.service.poll_commands()

        self.assertEqual(results[0].kind, "portfolio_add")
        self.assertEqual(results[0].symbol, "MSFT")
        self.assertEqual(results[0].quantity, 10.0)
        self.assertEqual(results[0].average_cost, 320.5)
        self.assertEqual(results[1].kind, "portfolio_update")
        self.assertEqual(results[1].quantity, 12.0)
        self.assertEqual(results[1].average_cost, 315.0)
        self.assertEqual(results[2].kind, "portfolio_remove")
        self.assertEqual(results[2].symbol, "MSFT")

    def test_poll_commands_parses_watchlist_compare_and_alerts(self) -> None:
        self.responses.append(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 164,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/watchlist",
                        },
                    },
                    {
                        "update_id": 165,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/compare aapl msft nvda",
                        },
                    },
                    {
                        "update_id": 166,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/alert MSFT 15%",
                        },
                    },
                    {
                        "update_id": 167,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/alerts",
                        },
                    },
                    {
                        "update_id": 168,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/unalert MSFT",
                        },
                    },
                ],
            }
        )

        results = self.service.poll_commands()

        self.assertEqual(results[0].kind, "watchlist")
        self.assertEqual(results[1].kind, "compare")
        self.assertEqual(results[1].symbols, ("AAPL", "MSFT", "NVDA"))
        self.assertEqual(results[2].kind, "alert_add")
        self.assertEqual(results[2].symbol, "MSFT")
        self.assertEqual(results[2].threshold, 0.15)
        self.assertEqual(results[3].kind, "alerts")
        self.assertEqual(results[4].kind, "alert_remove")

    def test_poll_commands_parses_discover_with_mode_and_limit(self) -> None:
        self.responses.append(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 171,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/discover value 7",
                        },
                    },
                    {
                        "update_id": 172,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/discover 12 growth",
                        },
                    },
                ],
            }
        )

        results = self.service.poll_commands()

        self.assertEqual(results[0].kind, "discover")
        self.assertEqual(results[0].mode, "value")
        self.assertEqual(results[0].limit, 7)
        self.assertEqual(results[1].kind, "discover")
        self.assertEqual(results[1].mode, "growth")
        self.assertEqual(results[1].limit, 10)

    def test_poll_commands_marks_invalid_portfolio_and_compare_commands(self) -> None:
        self.responses.append(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 169,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/portfolio add MSFT ten 320",
                        },
                    },
                    {
                        "update_id": 170,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/compare AAPL",
                        },
                    },
                ],
            }
        )

        results = self.service.poll_commands()

        self.assertEqual(results[0].kind, "portfolio_invalid")
        self.assertEqual(results[1].kind, "compare_invalid")

    def test_poll_commands_parses_help(self) -> None:
        self.responses.append(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 160,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/help",
                        },
                    }
                ],
            }
        )

        results = self.service.poll_commands()

        self.assertEqual(
            results,
            [TelegramCommand(update_id=160, kind="help", chat_id=999, actor="alice", text="/help", symbol=None, limit=None)],
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
