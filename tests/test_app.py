from __future__ import annotations

import builtins
import unittest
from unittest.mock import patch

from xtb_trading_bot.app import TerminalStopController


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


if __name__ == "__main__":
    unittest.main()
