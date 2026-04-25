from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1

from .config import RiskConfig
from .domain import OrderProposal, PerformanceSnapshot, PositionSnapshot, Signal, SignalSide


class RiskError(RuntimeError):
    pass


@dataclass
class RiskEngine:
    config: RiskConfig

    def build_proposal(
        self,
        signal: Signal,
        open_positions: list[PositionSnapshot],
        performance: PerformanceSnapshot,
    ) -> OrderProposal:
        if signal.side == SignalSide.NO_TRADE:
            raise RiskError("Cannot build an order for a NO_TRADE signal.")
        if signal.entry is None or signal.stop_loss is None or signal.take_profit is None:
            raise RiskError("Signal is missing execution levels.")
        if len(open_positions) >= self.config.max_open_positions:
            raise RiskError("Maximum number of open positions reached.")
        if performance.daily_pnl <= -(self.config.capital * self.config.max_daily_loss):
            raise RiskError("Daily loss limit reached.")
        if performance.weekly_pnl <= -(self.config.capital * self.config.max_weekly_loss):
            raise RiskError("Weekly loss limit reached.")

        stop_distance = abs(signal.entry - signal.stop_loss)
        if stop_distance <= 0:
            raise RiskError("Stop loss must be different from entry.")

        risk_amount = self.config.capital * self.config.risk_per_trade
        quantity = round(risk_amount / stop_distance, 4)
        if quantity <= 0:
            raise RiskError("Computed quantity is not positive.")

        proposal_id = sha1(f"{signal.signal_id}:{quantity}:{signal.entry}".encode("utf-8")).hexdigest()[:12]
        return OrderProposal(
            proposal_id=proposal_id,
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            estimated_risk_amount=risk_amount,
        )
