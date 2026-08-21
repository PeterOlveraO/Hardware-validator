"""Tests for the package entry point."""

import io
import sys
import unittest
from contextlib import redirect_stdout
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import patch

from hardware_validator import __main__
from hardware_validator.collector import DetectionResult
from hardware_validator.collector import InventoryResults
from hardware_validator.models import CpuInfo
from hardware_validator.models import GpuInventory
from hardware_validator.models import MemoryInfo
from hardware_validator.models import StorageInfo
from hardware_validator.models import SystemInfo


def empty_results() -> InventoryResults:
    return InventoryResults(
        system=DetectionResult.complete(SystemInfo()),
        cpu=DetectionResult.complete(CpuInfo()),
        memory=DetectionResult.complete(MemoryInfo()),
        storage=DetectionResult.complete(StorageInfo()),
        gpu=DetectionResult.complete(GpuInventory()),
    )


class MainTests(unittest.TestCase):
    def test_main_prints_complete_inventory(self) -> None:
        output = io.StringIO()

        with (
            patch("hardware_validator.collector.collect_inventory", return_value=empty_results()),
            patch("hardware_validator.detectors.default_detectors"),
            redirect_stdout(output),
        ):
            exit_code = __main__.main([])

        self.assertEqual(exit_code, 0)
        self.assertIn("System\n", output.getvalue())
        self.assertIn("\nGPU\n", output.getvalue())

    def test_help_does_not_require_runtime_detection(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output), self.assertRaises(SystemExit) as error:
            __main__.main(["--help"])

        self.assertEqual(error.exception.code, 0)
        self.assertIn("non-invasive", output.getvalue())

    def test_version_does_not_require_runtime_detection(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output), self.assertRaises(SystemExit) as error:
            __main__.main(["--version"])

        self.assertEqual(error.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), "0.1.0")

    def test_report_encoding_failure_is_written_to_stderr(self) -> None:
        class RestrictedOutput(io.StringIO):
            def write(self, value: str) -> int:
                raise UnicodeEncodeError("ascii", value, 0, 1, "fixture")

        error = io.StringIO()
        with (
            patch("hardware_validator.collector.collect_inventory", return_value=empty_results()),
            patch("hardware_validator.detectors.default_detectors"),
            redirect_stdout(RestrictedOutput()),
            redirect_stderr(error),
        ):
            exit_code = __main__.main([])

        self.assertEqual(exit_code, 1)
        self.assertIn("Unable to write the hardware inventory", error.getvalue())

    def test_main_rejects_missing_psutil(self) -> None:
        error = io.StringIO()

        with patch.dict(sys.modules, {"psutil": None}), redirect_stderr(error):
            exit_code = __main__.main([])

        self.assertEqual(exit_code, 1)
        self.assertIn("requires psutil 7.2.1", error.getvalue())

    def test_main_rejects_incompatible_psutil(self) -> None:
        error = io.StringIO()
        incompatible_psutil = SimpleNamespace(__version__="1.0.0")

        with (
            patch.dict(sys.modules, {"psutil": incompatible_psutil}),
            redirect_stderr(error),
        ):
            exit_code = __main__.main([])

        self.assertEqual(exit_code, 1)
        self.assertIn("found 1.0.0", error.getvalue())

    def test_main_rejects_unsupported_operating_system(self) -> None:
        error = io.StringIO()

        with patch.object(sys, "platform", "win32"), redirect_stderr(error):
            exit_code = __main__.main([])

        self.assertEqual(exit_code, 1)
        self.assertIn("requires Linux", error.getvalue())


if __name__ == "__main__":
    unittest.main()
