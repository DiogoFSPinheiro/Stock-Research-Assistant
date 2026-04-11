from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from typing import Callable
from urllib import parse, request

from .config import TelegramConfig
from .domain import ApprovalDecision, ApprovalStatus, OrderProposal, PositionSnapshot, Signal


HttpPost = Callable[[str, dict], None]
HttpGet = Callable[[str], dict]


@dataclass(frozen=True)
class TelegramUpdate:
    update_id: int
    chat_id: str | int | None
    text: str
    actor: str


def _default_post(url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=10):
        return


def _default_get(url: str) -> dict:
    with request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


@dataclass
class TelegramApprovalService:
    config: TelegramConfig
    signal_expiry_minutes: int
    http_post: HttpPost = _default_post
    http_get: HttpGet = _default_get
    decisions: list[ApprovalDecision] = field(default_factory=list)
    published_proposals: dict[str, datetime] = field(default_factory=dict)
    positions_provider: Callable[[], list[PositionSnapshot]] | None = None
    last_update_id: int | None = None

    def _bot_api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.config.bot_token}/{method}"

    def _send_message(self, chat_id: str | int, text: str) -> None:
        if not self.config.bot_token:
            return
        self.http_post(
            self._bot_api_url("sendMessage"),
            {"chat_id": chat_id, "text": text},
        )

    def _format_positions(self) -> str:
        positions = self.list_positions()
        if not positions:
            return "No open positions."
        return "\n".join(
            f"{position.symbol} {position.side.value} qty={position.quantity} pnl={position.unrealized_pnl:.2f}"
            for position in positions
        )

    def _format_pending(self) -> str:
        if not self.published_proposals:
            return "No pending proposals."
        return "\n".join(sorted(self.published_proposals))

    def _maybe_reply(self, chat_id: str | int | None, reply: bool, message: str) -> None:
        if reply and chat_id is not None:
            self._send_message(chat_id, message)

    def _normalize_command(self, text: str) -> list[str]:
        parts = text.strip().split()
        if not parts:
            return []
        parts[0] = parts[0].split("@", 1)[0].lower()
        return parts

    def _parse_update(self, update: dict) -> TelegramUpdate | None:
        try:
            update_id = int(update["update_id"])
        except (KeyError, TypeError, ValueError):
            return None

        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return None

        text = message.get("text")
        chat = message.get("chat") or {}
        from_user = message.get("from") or {}
        if not isinstance(text, str):
            return None

        chat_id = chat.get("id") if isinstance(chat, dict) else None
        username = from_user.get("username") if isinstance(from_user, dict) else None
        actor = str(username or from_user.get("first_name") or "telegram-user") if isinstance(from_user, dict) else "telegram-user"
        return TelegramUpdate(update_id=update_id, chat_id=chat_id, text=text, actor=actor)

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
        self._send_message(self.config.chat_id, text)

    def initialize(self) -> None:
        if self.config.drop_pending_updates_on_start:
            self.poll_updates(reply=False)

    def list_positions(self) -> list[PositionSnapshot]:
        if self.positions_provider is None:
            return []
        return self.positions_provider()

    def receive_command(
        self,
        command: str,
        actor: str = "telegram-user",
        chat_id: str | int | None = None,
        reply: bool = False,
    ) -> ApprovalDecision | str:
        parts = self._normalize_command(command)
        if not parts:
            return "Empty command."

        verb = parts[0].lower()
        if verb == "/positions":
            response: ApprovalDecision | str = self._format_positions()
            self._maybe_reply(chat_id, reply, str(response))
            return response
        if verb == "/pending":
            response = self._format_pending()
            self._maybe_reply(chat_id, reply, response)
            return response
        if len(parts) < 2:
            response = "Missing proposal id."
            self._maybe_reply(chat_id, reply, response)
            return response

        proposal_id = parts[1]
        published_at = self.published_proposals.get(proposal_id)
        if published_at is None:
            response = "Unknown proposal id."
            self._maybe_reply(chat_id, reply, response)
            return response
        if datetime.now(timezone.utc) - published_at > timedelta(minutes=self.signal_expiry_minutes):
            decision = ApprovalDecision(
                proposal_id=proposal_id,
                status=ApprovalStatus.EXPIRED,
                actor=actor,
                note="Proposal expired.",
            )
            self.decisions.append(decision)
            self.published_proposals.pop(proposal_id, None)
            self._maybe_reply(chat_id, reply, "Proposal expired.")
            return decision

        if verb == "/approve":
            status = ApprovalStatus.APPROVED
            response_text = f"Approved proposal {proposal_id}."
        elif verb == "/reject":
            status = ApprovalStatus.REJECTED
            response_text = f"Rejected proposal {proposal_id}."
        else:
            response = "Unsupported command."
            self._maybe_reply(chat_id, reply, response)
            return response

        decision = ApprovalDecision(proposal_id=proposal_id, status=status, actor=actor)
        self.decisions.append(decision)
        self.published_proposals.pop(proposal_id, None)
        self._maybe_reply(chat_id, reply, response_text)
        return decision

    def poll_updates(self, reply: bool = False) -> list[ApprovalDecision | str]:
        if not self.config.bot_token:
            return []

        params = {
            "timeout": str(self.config.polling_timeout_seconds),
            "limit": str(self.config.polling_limit),
        }
        if self.last_update_id is not None:
            params["offset"] = str(self.last_update_id + 1)

        payload = self.http_get(f"{self._bot_api_url('getUpdates')}?{parse.urlencode(params)}")
        if not isinstance(payload, dict):
            return []

        results = payload.get("result", [])
        if not isinstance(results, list):
            return []

        processed: list[ApprovalDecision | str] = []
        for raw_update in results:
            if not isinstance(raw_update, dict):
                continue
            update = self._parse_update(raw_update)
            if update is None:
                continue
            self.last_update_id = update.update_id if self.last_update_id is None else max(self.last_update_id, update.update_id)
            if not update.text.strip().startswith("/"):
                continue
            processed.append(
                self.receive_command(
                    update.text,
                    actor=update.actor,
                    chat_id=update.chat_id,
                    reply=reply,
                )
            )
        return processed

    def get_pending_decisions(self) -> list[ApprovalDecision]:
        decisions, self.decisions = self.decisions[:], []
        return decisions
