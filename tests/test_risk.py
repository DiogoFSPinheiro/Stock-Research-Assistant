from __future__ import annotations

import unittest

from stock_research_assistant.config import RiskConfig
from stock_research_assistant.domain import AssetClass, PerformanceSnapshot, PositionSnapshot, Signal, SignalSide
from stock_research_assistant.risk import RiskEngine, RiskError


class RiskEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = RiskEngine(
            RiskConfig(
                capital=100000,
                risk_per_trade=0.005,
                max_open_positions=2,
                max_daily_loss=0.02,
                max_weekly_loss=0.05,
                reward_to_risk=2.0,
                signal_expiry_minutes=240,
            )
        )
        self.signal = Signal(
            signal_id="sig123",
            symbol="EURUSD",
            company_name=None,
            asset_class=AssetClass.FX,
            side=SignalSide.BUY,
            timeframe="H4",
            confidence=0.8,
            rationale="Aligned trend",
            entry=1.25,
            stop_loss=1.20,
            take_profit=1.35,
        )

    def test_builds_order_with_stop_loss(self) -> None:
        proposal = self.engine.build_proposal(
            self.signal,
            [],
            PerformanceSnapshot(daily_pnl=0.0, weekly_pnl=0.0),
        )
        self.assertGreater(proposal.quantity, 0)
        self.assertEqual(proposal.estimated_risk_amount, 500.0)

    def test_blocks_after_daily_loss_limit(self) -> None:
        with self.assertRaises(RiskError):
            self.engine.build_proposal(
                self.signal,
                [],
                PerformanceSnapshot(daily_pnl=-2500.0, weekly_pnl=-2500.0),
            )

    def test_blocks_when_max_positions_reached(self) -> None:
        positions = [
            PositionSnapshot("EURUSD", SignalSide.BUY, 1, 1.2, 1.22, 10, 50),
            PositionSnapshot("AAPL", SignalSide.BUY, 1, 150, 151, 10, 50),
        ]
        with self.assertRaises(RiskError):
            self.engine.build_proposal(
                self.signal,
                positions,
                PerformanceSnapshot(daily_pnl=0.0, weekly_pnl=0.0),
            )


if __name__ == "__main__":
    unittest.main()
