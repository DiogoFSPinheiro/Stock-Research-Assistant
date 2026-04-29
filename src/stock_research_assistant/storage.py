from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from .domain import ApprovalDecision, ApprovalStatus, OrderProposal, PerformanceSnapshot, Signal


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return getattr(value, "value", value)


class JsonStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save(
                {
                    "signals": [],
                    "proposals": [],
                    "decisions": [],
                    "executions": [],
                    "performance": {"daily_pnl": 0.0, "weekly_pnl": 0.0},
                    "scheduled_scan": {"last_run_on": None},
                    "portfolio_report": {"last_run_on": None},
                    "alerts": [],
                }
            )

    def record_signal(self, signal: Signal) -> None:
        state = self._load()
        state["signals"].append(asdict(signal))
        self._save(state)

    def record_proposal(self, proposal: OrderProposal) -> None:
        state = self._load()
        state["proposals"].append(asdict(proposal))
        self._save(state)

    def record_decision(self, decision: ApprovalDecision) -> None:
        state = self._load()
        state["decisions"].append(asdict(decision))
        self._save(state)

    def record_execution(self, proposal_id: str, execution_id: str) -> None:
        state = self._load()
        state["executions"].append(
            {"proposal_id": proposal_id, "execution_id": execution_id, "executed_at": datetime.now(timezone.utc)}
        )
        self._save(state)

    def has_recent_signal(self, symbol: str, timeframe: str, side: str) -> bool:
        state = self._load()
        threshold = datetime.now(timezone.utc) - timedelta(hours=12)
        for item in reversed(state["signals"]):
            created_at = datetime.fromisoformat(item["created_at"])
            if (
                item["symbol"] == symbol
                and item["timeframe"] == timeframe
                and item["side"] == side
                and created_at >= threshold
            ):
                return True
        return False

    def list_pending_proposals(self) -> list[OrderProposal]:
        state = self._load()
        decisions = {item["proposal_id"]: item["status"] for item in state["decisions"]}
        pending: list[OrderProposal] = []
        for item in state["proposals"]:
            if decisions.get(item["proposal_id"], ApprovalStatus.PENDING.value) == ApprovalStatus.PENDING.value:
                pending.append(
                    OrderProposal(
                        proposal_id=item["proposal_id"],
                        signal_id=item["signal_id"],
                        symbol=item["symbol"],
                        side=item["side"],
                        quantity=item["quantity"],
                        entry=item["entry"],
                        stop_loss=item["stop_loss"],
                        take_profit=item["take_profit"],
                        estimated_risk_amount=item["estimated_risk_amount"],
                        created_at=datetime.fromisoformat(item["created_at"]),
                    )
                )
        return pending

    def mark_expired(self, proposal_id: str) -> None:
        self.record_decision(
            ApprovalDecision(
                proposal_id=proposal_id,
                status=ApprovalStatus.EXPIRED,
                actor="system",
                note="Proposal expired before manual approval.",
            )
        )

    def get_performance(self) -> PerformanceSnapshot:
        state = self._load()
        performance = state["performance"]
        return PerformanceSnapshot(
            daily_pnl=performance["daily_pnl"],
            weekly_pnl=performance["weekly_pnl"],
        )

    def get_last_scheduled_scan_on(self) -> str | None:
        state = self._load()
        scheduled_scan = state.get("scheduled_scan", {})
        if not isinstance(scheduled_scan, dict):
            return None
        value = scheduled_scan.get("last_run_on")
        return value if isinstance(value, str) and value else None

    def mark_scheduled_scan_on(self, day: str) -> None:
        state = self._load()
        scheduled_scan = state.get("scheduled_scan")
        if not isinstance(scheduled_scan, dict):
            scheduled_scan = {}
        scheduled_scan["last_run_on"] = day
        state["scheduled_scan"] = scheduled_scan
        self._save(state)

    def get_last_portfolio_report_on(self) -> str | None:
        state = self._load()
        portfolio_report = state.get("portfolio_report", {})
        if not isinstance(portfolio_report, dict):
            return None
        value = portfolio_report.get("last_run_on")
        return value if isinstance(value, str) and value else None

    def mark_portfolio_report_on(self, day: str) -> None:
        state = self._load()
        portfolio_report = state.get("portfolio_report")
        if not isinstance(portfolio_report, dict):
            portfolio_report = {}
        portfolio_report["last_run_on"] = day
        state["portfolio_report"] = portfolio_report
        self._save(state)

    def list_alerts(self) -> list[dict[str, Any]]:
        state = self._load()
        alerts = state.get("alerts", [])
        if not isinstance(alerts, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in alerts:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            try:
                threshold = float(item.get("threshold"))
            except (TypeError, ValueError):
                continue
            if not symbol or threshold <= 0:
                continue
            normalized.append(
                {
                    "symbol": symbol,
                    "threshold": threshold,
                    "created_at": item.get("created_at"),
                    "last_triggered_at": item.get("last_triggered_at"),
                }
            )
        return normalized

    def upsert_alert(self, symbol: str, threshold: float) -> tuple[bool, dict[str, Any]]:
        normalized = symbol.strip().upper()
        state = self._load()
        alerts = self.list_alerts()
        created_at = datetime.now(timezone.utc).isoformat()
        new_alert = {
            "symbol": normalized,
            "threshold": threshold,
            "created_at": created_at,
            "last_triggered_at": None,
        }
        added = True
        for index, item in enumerate(alerts):
            if item["symbol"] == normalized:
                new_alert["created_at"] = item.get("created_at") or created_at
                new_alert["last_triggered_at"] = item.get("last_triggered_at")
                alerts[index] = new_alert
                added = False
                break
        if added:
            alerts.append(new_alert)
        state["alerts"] = alerts
        self._save(state)
        return added, new_alert

    def remove_alert(self, symbol: str) -> tuple[bool, str, int]:
        normalized = symbol.strip().upper()
        state = self._load()
        alerts = self.list_alerts()
        remaining = [item for item in alerts if item["symbol"] != normalized]
        removed = len(remaining) != len(alerts)
        state["alerts"] = remaining
        self._save(state)
        return removed, normalized, len(remaining)

    def mark_alert_triggered(self, symbol: str, triggered_at: datetime | None = None) -> None:
        normalized = symbol.strip().upper()
        state = self._load()
        alerts = self.list_alerts()
        timestamp = (triggered_at or datetime.now(timezone.utc)).isoformat()
        for item in alerts:
            if item["symbol"] == normalized:
                item["last_triggered_at"] = timestamp
                break
        state["alerts"] = alerts
        self._save(state)

    def _load(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, default=_json_default, indent=2), encoding="utf-8")
