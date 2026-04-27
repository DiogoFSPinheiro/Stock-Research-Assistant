from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import time
from typing import Callable
from urllib import error, parse, request

from .config import TelegramConfig
from .domain import ApprovalDecision, OrderProposal, PositionSnapshot, Signal
from .reporting import (
    SEPARATOR,
    format_display_company,
    format_horizon,
    format_price,
    format_score,
    format_signed_pct,
    html_escape,
)


HttpPost = Callable[[str, dict], None]
HttpGet = Callable[[str], dict]


class TelegramApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class TelegramUpdate:
    update_id: int
    chat_id: str | int | None
    text: str
    actor: str


@dataclass(frozen=True)
class TipRequest:
    chat_id: str | int | None
    actor: str
    text: str


@dataclass(frozen=True)
class TelegramCommand:
    update_id: int
    kind: str
    chat_id: str | int | None
    actor: str
    text: str
    symbol: str | None = None
    limit: int | None = None


def _default_post(url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=10):
            return
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise TelegramApiError(f"Telegram sendMessage failed: HTTP {exc.code} {body}") from exc


def _default_get(url: str) -> dict:
    try:
        with request.urlopen(url, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise TelegramApiError(f"Telegram getUpdates failed: HTTP {exc.code} {body}") from exc
    except (TimeoutError, OSError, error.URLError) as exc:
        raise TelegramApiError(f"Telegram getUpdates failed: {exc}") from exc


@dataclass
class TelegramApprovalService:
    config: TelegramConfig
    signal_expiry_minutes: int
    http_post: HttpPost = _default_post
    http_get: HttpGet = _default_get
    decisions: list[ApprovalDecision] = field(default_factory=list)
    positions_provider: Callable[[], list[PositionSnapshot]] | None = None
    last_update_id: int | None = None
    dedupe_window_seconds: int = 20
    recent_command_signatures: dict[tuple[str | int | None, str], float] = field(default_factory=dict)

    def _bot_api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.config.bot_token}/{method}"

    def _send_message(self, chat_id: str | int, text: str) -> None:
        if not self.config.bot_token:
            return
        self.http_post(
            self._bot_api_url("sendMessage"),
            {"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        )

    def _resolve_chat_id(self, chat_id: str | int | None = None) -> str | int | None:
        return chat_id if chat_id is not None else self.config.chat_id

    def _is_tip_request(self, text: str) -> bool:
        normalized = " ".join(text.strip().lower().split())
        command = normalized.split("@", 1)[0]
        return command in {"/tip", "/give_a_tip", "tip", "give a tip"}

    def _parse_command(self, update: TelegramUpdate) -> TelegramCommand | None:
        normalized = " ".join(update.text.strip().split())
        lowered = normalized.lower().replace('"', "").replace("'", "")
        first = lowered.split(" ", 1)[0].split("@", 1)[0]
        parts = normalized.replace('"', "").replace("'", "").split()
        if first in {"/tip", "/give_a_tip", "tip"} or lowered == "give a tip":
            symbol = parts[1].upper() if len(parts) >= 2 else None
            kind = "tip_for_symbol" if symbol else "tip"
            return TelegramCommand(update_id=update.update_id, kind=kind, chat_id=update.chat_id, actor=update.actor, text=update.text, symbol=symbol)
        if first in {"/top", "/tips", "top", "tips"}:
            limit = None
            if len(parts) >= 2:
                try:
                    limit = max(1, min(int(parts[1]), 10))
                except ValueError:
                    limit = None
            return TelegramCommand(update_id=update.update_id, kind="top_tips", chat_id=update.chat_id, actor=update.actor, text=update.text, limit=limit)
        if first in {"/watch", "watch", "/add", "add"}:
            if len(parts) >= 2:
                return TelegramCommand(
                    update_id=update.update_id,
                    kind="watch_stock",
                    chat_id=update.chat_id,
                    actor=update.actor,
                    text=update.text,
                    symbol=parts[1].upper(),
                )
        if first in {"/analise", "analise", "/analyze", "analyze", "/analyse", "analyse"}:
            if len(parts) >= 2:
                return TelegramCommand(
                    update_id=update.update_id,
                    kind="stock_analysis",
                    chat_id=update.chat_id,
                    actor=update.actor,
                    text=update.text,
                    symbol=parts[1].upper(),
                )
        if first in {"/portfolio", "portfolio"}:
            return TelegramCommand(
                update_id=update.update_id,
                kind="portfolio",
                chat_id=update.chat_id,
                actor=update.actor,
                text=update.text,
            )
        if first in {"/help", "help"}:
            return TelegramCommand(
                update_id=update.update_id,
                kind="help",
                chat_id=update.chat_id,
                actor=update.actor,
                text=update.text,
            )
        return None

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

    def publish_signal(self, signal: Signal, proposal: OrderProposal, chat_id: str | int | None = None) -> None:
        target_chat_id = self._resolve_chat_id(chat_id)
        if not self.config.bot_token or not target_chat_id:
            return
        best_horizon = format_horizon(signal)
        fair_value = format_price(signal.fair_value)
        margin = format_signed_pct(signal.margin_of_safety)
        quality = format_score(signal.quality_score)
        timing = format_score(signal.timing_score)
        risks = ", ".join(signal.risk_flags) if signal.risk_flags else "none flagged"
        company_label = format_display_company(signal.symbol, signal.company_name)
        text = "\n".join(
            [
                "💡 <b>Research Idea</b>",
                f"<b>{html_escape(company_label)}</b>",
                SEPARATOR,
                "",
                "🎯 <b>Setup</b>",
                f"Best horizon: {html_escape(best_horizon)}",
                f"Research window: {html_escape(signal.timeframe)}",
                f"Confidence: {signal.confidence:.0%}",
                "",
                "💵 <b>Valuation</b>",
                f"Estimated fair value: {html_escape(fair_value)}",
                f"Margin of safety: {html_escape(margin)}",
                "",
                "📈 <b>Scores</b>",
                f"Quality score: {html_escape(quality)}",
                f"Timing score: {html_escape(timing)}",
                "",
                "🧠 <b>Why</b>",
                html_escape(signal.rationale),
                "",
                "⚠️ <b>Risk Flags</b>",
                html_escape(risks),
            ]
        )
        self._send_message(target_chat_id, text)

    def publish_text(self, text: str, chat_id: str | int | None = None) -> None:
        target_chat_id = self._resolve_chat_id(chat_id)
        if not self.config.bot_token or not target_chat_id:
            return
        self._send_message(target_chat_id, text)

    def initialize(self) -> None:
        if self.config.drop_pending_updates_on_start:
            self.poll_updates(reply=False)

    def list_positions(self) -> list[PositionSnapshot]:
        if self.positions_provider is None:
            return []
        return self.positions_provider()

    def poll_tip_requests(self) -> list[TipRequest]:
        return [
            TipRequest(chat_id=command.chat_id, actor=command.actor, text=command.text)
            for command in self.poll_commands()
            if command.kind == "tip"
        ]

    def poll_commands(self) -> list[TelegramCommand]:
        if not self.config.bot_token:
            return []

        params = {
            "timeout": str(min(self.config.polling_timeout_seconds, 5)),
            "limit": str(self.config.polling_limit),
        }
        if self.last_update_id is not None:
            params["offset"] = str(self.last_update_id + 1)

        try:
            payload = self.http_get(f"{self._bot_api_url('getUpdates')}?{parse.urlencode(params)}")
        except TelegramApiError:
            return []
        if not isinstance(payload, dict):
            return []

        results = payload.get("result", [])
        if not isinstance(results, list):
            return []

        commands: list[TelegramCommand] = []
        for raw_update in results:
            if not isinstance(raw_update, dict):
                continue
            update = self._parse_update(raw_update)
            if update is None:
                continue
            self.last_update_id = update.update_id if self.last_update_id is None else max(self.last_update_id, update.update_id)
            command = self._parse_command(update)
            if command is not None and not self._is_duplicate_command(command):
                commands.append(command)
        return commands

    def poll_updates(self, reply: bool = False) -> list[ApprovalDecision | str]:
        self.poll_commands()
        return []

    def get_pending_decisions(self) -> list[ApprovalDecision]:
        decisions, self.decisions = self.decisions[:], []
        return decisions

    def _is_duplicate_command(self, command: TelegramCommand) -> bool:
        now = time.monotonic()
        signature = (command.chat_id, " ".join(command.text.strip().split()).lower())
        expired = [
            key
            for key, seen_at in self.recent_command_signatures.items()
            if (now - seen_at) > self.dedupe_window_seconds
        ]
        for key in expired:
            self.recent_command_signatures.pop(key, None)
        previous = self.recent_command_signatures.get(signature)
        self.recent_command_signatures[signature] = now
        return previous is not None and (now - previous) <= self.dedupe_window_seconds

