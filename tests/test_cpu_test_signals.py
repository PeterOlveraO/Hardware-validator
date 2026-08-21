"""Tests for minimal, restorable CPU-test signal recording."""

import signal
import unittest
from unittest.mock import patch

from hardware_validator.cpu_test.signals import SignalController


class CpuTestSignalTests(unittest.TestCase):
    def test_handlers_only_record_first_signal_and_restore_previous_handlers(self) -> None:
        controller = SignalController()
        installed: dict[int, object] = {}
        previous = {
            signal.SIGINT: object(),
            signal.SIGTERM: object(),
        }

        def set_handler(signum: int, handler: object) -> None:
            installed[signum] = handler

        with (
            patch("signal.getsignal", side_effect=lambda signum: previous[signum]),
            patch("signal.signal", side_effect=set_handler),
        ):
            with controller.installed():
                interrupt_handler = installed[signal.SIGINT]
                termination_handler = installed[signal.SIGTERM]
                interrupt_handler(signal.SIGINT, None)  # type: ignore[operator]
                termination_handler(signal.SIGTERM, None)  # type: ignore[operator]
                self.assertEqual(controller.pending(), signal.SIGINT)

        self.assertIs(installed[signal.SIGINT], previous[signal.SIGINT])
        self.assertIs(installed[signal.SIGTERM], previous[signal.SIGTERM])


if __name__ == "__main__":
    unittest.main()
