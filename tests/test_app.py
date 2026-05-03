from __future__ import annotations

import builtins
from datetime import datetime
import unittest
from unittest.mock import patch

from stock_research_assistant.app import (
    TerminalStopController,
    log_received_command,
    notify_bot_stopping,
    notify_portfolio_report_start,
    notify_scheduled_scan_start,
    should_run_portfolio_report,
    should_run_scheduled_scan,
)
from stock_research_assistant.reporting import SEPARATOR


class TerminalStopControllerTests(unittest.TestCase):
    def test_stop_command_sets_stop_flag(self) -> None:
        controller = TerminalStopController()
        with patch.object(builtins, "input", side_effect=["stop"]):
            controller._read_commands()

        self.assertTrue(controller.should_stop())

    def test_non_stop_command_keeps_listening_until_exit(self) -> None:
        controller = TerminalStopController()
        with patch.object(builtins, "input", side_effect=["hello", "exit"]):
            controller._read_commands()

        self.assertTrue(controller.should_stop())

    def test_wait_returns_true_when_stop_requested(self) -> None:
        controller = TerminalStopController()
        controller.stop()

        self.assertTrue(controller.wait(0.01))

    def test_notify_scheduled_scan_start_logs_and_publishes(self) -> None:
        messages: list[str] = []

        class FakeLogger:
            def info(self, message: str) -> None:
                messages.append(message)

            def warning(self, message: str, *args) -> None:
                messages.append(message % args if args else message)

        class FakeApprovals:
            def publish_text(self, text: str) -> None:
                messages.append(text)

        class FakeBot:
            logger = FakeLogger()
            approvals = FakeApprovals()

        notify_scheduled_scan_start(FakeBot())

        self.assertEqual(
            messages,
            [
                "Top research ideas cycle started. Please wait before sending more requests.",
                f"🔎 <b>Research cycle started</b>\n{SEPARATOR}\n\n"
                "Building the current top research ideas.\n"
                "Please wait before sending more requests.",
            ],
        )

    def test_notify_bot_stopping_logs_message(self) -> None:
        messages: list[str] = []

        class FakeLogger:
            def info(self, message: str) -> None:
                messages.append(message)

        notify_bot_stopping(FakeLogger())

        self.assertEqual(messages, ["Stopping bot..."])

    def test_notify_portfolio_report_start_logs_and_publishes(self) -> None:
        messages: list[str] = []

        class FakeLogger:
            def info(self, message: str) -> None:
                messages.append(message)

            def warning(self, message: str, *args) -> None:
                messages.append(message % args if args else message)

        class FakeApprovals:
            def publish_text(self, text: str) -> None:
                messages.append(text)

        class FakeBot:
            logger = FakeLogger()
            approvals = FakeApprovals()

        notify_portfolio_report_start(FakeBot())

        self.assertEqual(
            messages,
            [
                "PORTFOLIO daily report started. Please wait before sending more requests.",
                f"📁 <b>Portfolio report started</b>\n{SEPARATOR}\n\n"
                "Building today's portfolio performance report.\n"
                "Please wait before sending more requests.",
            ],
        )

    def test_log_received_command_for_top_tips_is_immediate_and_clear(self) -> None:
        messages: list[str] = []

        class FakeLogger:
            def info(self, message: str, *args) -> None:
                messages.append(message % args if args else message)

        class FakeCommand:
            kind = "top_tips"
            text = "/top 5"
            limit = 5

        log_received_command(FakeLogger(), FakeCommand())

        self.assertEqual(messages, ["Received Telegram command: /top 5. Building research shortlist with limit=5."])

    def test_should_run_scheduled_scan_only_once_on_weekday_after_schedule(self) -> None:
        class FakeConfig:
            auto_scan_hour = 9
            auto_scan_minute = 0

        class FakeStateStore:
            def __init__(self, last_run_on: str | None) -> None:
                self.last_run_on = last_run_on

            def get_last_scheduled_scan_on(self) -> str | None:
                return self.last_run_on

        class FakeBot:
            config = FakeConfig()
            state_store = FakeStateStore(None)

        monday_morning = datetime.fromisoformat("2026-04-13T09:30:00+01:00")
        monday_early = datetime.fromisoformat("2026-04-13T08:30:00+01:00")
        saturday = datetime.fromisoformat("2026-04-18T10:00:00+01:00")

        self.assertTrue(should_run_scheduled_scan(FakeBot(), monday_morning))
        self.assertFalse(should_run_scheduled_scan(FakeBot(), monday_early))
        self.assertFalse(should_run_scheduled_scan(FakeBot(), saturday))

        already_ran_bot = FakeBot()
        already_ran_bot.state_store = FakeStateStore("2026-04-13")
        self.assertFalse(should_run_scheduled_scan(already_ran_bot, monday_morning))

    def test_should_run_portfolio_report_once_after_us_close(self) -> None:
        class FakeStateStore:
            def __init__(self, last_run_on: str | None) -> None:
                self.last_run_on = last_run_on

            def get_last_portfolio_report_on(self) -> str | None:
                return self.last_run_on

        class FakeBot:
            state_store = FakeStateStore(None)

            def portfolio_symbols(self) -> tuple[str, ...]:
                return ("AAPL", "MSFT")

        before_close = datetime.fromisoformat("2026-04-13T20:30:00+01:00")
        after_close = datetime.fromisoformat("2026-04-13T22:15:00+01:00")
        saturday = datetime.fromisoformat("2026-04-18T22:15:00+01:00")

        self.assertFalse(should_run_portfolio_report(FakeBot(), before_close))
        self.assertTrue(should_run_portfolio_report(FakeBot(), after_close))
        self.assertFalse(should_run_portfolio_report(FakeBot(), saturday))

        already_ran_bot = FakeBot()
        already_ran_bot.state_store = FakeStateStore("2026-04-13")
        self.assertFalse(should_run_portfolio_report(already_ran_bot, after_close))


if __name__ == "__main__":
    unittest.main()
