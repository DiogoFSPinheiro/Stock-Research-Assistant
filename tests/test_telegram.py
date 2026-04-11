from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
from datetime import datetime, timezone
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
PositionSnapshot = domain_module.PositionSnapshot
Signal = domain_module.Signal
SignalSide = domain_module.SignalSide
TelegramApprovalService = telegram_module.TelegramApprovalService


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
        self.assertIn("/approve prop1", self.calls[0][1]["text"])

    def test_approve_command_creates_decision(self) -> None:
        self.service.publish_signal(self.signal, self.proposal)
        result = self.service.receive_command("/approve prop1")
        self.assertEqual(result.status.value, "approved")

    def test_positions_command_lists_positions(self) -> None:
        self.service.positions_provider = lambda: [
            PositionSnapshot("AAPL", SignalSide.BUY, 10, 100, 103, 30, 20)
        ]
        result = self.service.receive_command("/positions")
        self.assertIn("AAPL BUY", result)

    def test_pending_command_lists_published_proposals(self) -> None:
        self.service.published_proposals["prop1"] = datetime.now(timezone.utc)

        result = self.service.receive_command("/pending")

        self.assertEqual(result, "prop1")

    def test_poll_updates_processes_commands_and_replies(self) -> None:
        self.service.published_proposals["prop1"] = datetime.now(timezone.utc)
        self.responses.append(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 41,
                        "message": {
                            "chat": {"id": 999},
                            "from": {"username": "alice"},
                            "text": "/approve@TradingBot prop1",
                        },
                    }
                ],
            }
        )

        results = self.service.poll_updates(reply=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status.value, "approved")
        self.assertEqual(self.service.last_update_id, 41)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], "https://api.telegram.org/bottoken/sendMessage")
        self.assertEqual(self.calls[0][1]["chat_id"], 999)
        self.assertEqual(self.calls[0][1]["text"], "Approved proposal prop1.")
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
                                "text": "/pending",
                            },
                        }
                    ],
                },
                {"ok": True, "result": []},
            ]
        )

        self.service.poll_updates()
        self.service.poll_updates()

        self.assertEqual(self.service.last_update_id, 10)
        self.assertIn("offset=11", self.get_calls[-1])
        self.assertEqual(self.get_calls[-1].count("offset="), 1)


if __name__ == "__main__":
    unittest.main()
