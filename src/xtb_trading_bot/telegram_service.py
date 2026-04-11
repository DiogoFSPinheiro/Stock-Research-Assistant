from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from typing import Callable
from urllib import request

from .config import TelegramConfig
from .domain import ApprovalDecision, ApprovalStatus, OrderProposal, PositionSnapshot, Signal


HttpPost = Callable[[str, dict], None]


def _default_post(url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=10):
        return


@dataclass
class TelegramApprovalService:
    config: TelegramConfig
    signal_expiry_minutes: int
    http_post: HttpPost = _default_post
    decisions: list[ApprovalDecision] = field(default_factory=list)
    published_proposals: dict[str, datetime] = field(default_factory=dict)
    positions_provider: Callable[[], list[PositionSnapshot]] | None = None

    def publish_signal(self, signal: Signal, proposal: OrderProposal) -> None:
        self.published_proposals[proposal.proposal_id] = datetime.now(timezone.utc)
        if not self.config.bot_token or not self.config.chat_id:
            return
        text = (
            f"Signal {signal.side.value} {signal.symbol} {signal.timeframe}\n"
            f"Entry: {proposal.entry:.4f}\n"
            f"Stop: {proposal.stop_loss:.4f}\n"
            f"Target: {proposal.take_profit:.4f}\n"
            f"Risk: {proposal.estimated_risk_amount:.2f}\n"
            f"Proposal ID: {proposal.proposal_id}\n"
            f"Use /approve {proposal.proposal_id} or /reject {proposal.proposal_id}"
        )
        url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
        self.http_post(url, {"chat_id": self.config.chat_id, "text": text})

    def list_positions(self) -> list[PositionSnapshot]:
        if self.positions_provider is None:
            return []
        return self.positions_provider()

    def receive_command(self, command: str, actor: str = "telegram-user") -> ApprovalDecision | str:
        parts = command.strip().split()
        if not parts:
            return "Empty command."

        verb = parts[0].lower()
        if verb == "/positions":
            positions = self.list_positions()
            if not positions:
                return "No open positions."
            return "\n".join(
                f"{position.symbol} {position.side.value} qty={position.quantity} pnl={position.unrealized_pnl:.2f}"
                for position in positions
            )
        if verb == "/pending":
            return "No pending proposals." if not self.published_proposals else "\n".join(sorted(self.published_proposals))
        if len(parts) < 2:
            return "Missing proposal id."

        proposal_id = parts[1]
        published_at = self.published_proposals.get(proposal_id)
        if published_at is None:
            return "Unknown proposal id."
        if datetime.now(timezone.utc) - published_at > timedelta(minutes=self.signal_expiry_minutes):
            decision = ApprovalDecision(
                proposal_id=proposal_id,
                status=ApprovalStatus.EXPIRED,
                actor=actor,
                note="Proposal expired.",
            )
            self.decisions.append(decision)
            self.published_proposals.pop(proposal_id, None)
            return decision

        if verb == "/approve":
            status = ApprovalStatus.APPROVED
        elif verb == "/reject":
            status = ApprovalStatus.REJECTED
        else:
            return "Unsupported command."

        decision = ApprovalDecision(proposal_id=proposal_id, status=status, actor=actor)
        self.decisions.append(decision)
        self.published_proposals.pop(proposal_id, None)
        return decision

    def get_pending_decisions(self) -> list[ApprovalDecision]:
        decisions, self.decisions = self.decisions[:], []
        return decisions
