from __future__ import annotations

import unittest

from xtb_trading_bot.config import TelegramConfig
from xtb_trading_bot.domain import AssetClass, OrderProposal, PositionSnapshot, Signal, SignalSide
from xtb_trading_bot.telegram_service import TelegramApprovalService


class TelegramApprovalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.service = TelegramApprovalService(
            TelegramConfig(bot_token="token", chat_id="chat"),
            signal_expiry_minutes=60,
            http_post=lambda url, payload: self.calls.append((url, payload)),
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


if __name__ == "__main__":
    unittest.main()
