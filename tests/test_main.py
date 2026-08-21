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
from hardware_validator.models import NetworkInfo
from hardware_validator.models import StorageInfo
from hardware_validator.models import SystemInfo
from hardware_validator.cpu_test.models import CpuTestConfig


def empty_results() -> InventoryResults:
    return InventoryResults(
        system=DetectionResult.complete(SystemInfo()),
        cpu=DetectionResult.complete(CpuInfo()),
        memory=DetectionResult.complete(MemoryInfo()),
        storage=DetectionResult.complete(StorageInfo()),
        gpu=DetectionResult.complete(GpuInventory()),
        network=DetectionResult.complete(NetworkInfo()),
    )


class MainTests(unittest.TestCase):
    def test_main_prints_summary_by_default(self) -> None:
        output = io.StringIO()

        with (
            patch("hardware_validator.collector.collect_inventory", return_value=empty_results()),
            patch("hardware_validator.detectors.default_detectors"),
            redirect_stdout(output),
        ):
            exit_code = __main__.main([])

        self.assertEqual(exit_code, 0)
        self.assertIn("Computer\n", output.getvalue())
        self.assertIn("\nGraphics\n", output.getvalue())
        self.assertIn("\nNetwork\n", output.getvalue())
        self.assertNotIn("Kernel Version", output.getvalue())

    def test_verbose_prints_complete_technical_inventory(self) -> None:
        output = io.StringIO()

        with (
            patch("hardware_validator.collector.collect_inventory", return_value=empty_results()),
            patch("hardware_validator.detectors.default_detectors"),
            redirect_stdout(output),
        ):
            exit_code = __main__.main(["--verbose"])

        self.assertEqual(exit_code, 0)
        self.assertIn("System\n", output.getvalue())
        self.assertIn("Kernel Version: Unknown", output.getvalue())
        self.assertIn("\nGPU\n", output.getvalue())
        self.assertIn("\nNetwork\n", output.getvalue())

    def test_help_does_not_require_runtime_detection(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output), self.assertRaises(SystemExit) as error:
            __main__.main(["--help"])

        self.assertEqual(error.exception.code, 0)
        self.assertIn("non-invasive", output.getvalue())
        self.assertIn("--verbose", output.getvalue())

    def test_version_does_not_require_runtime_detection(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output), self.assertRaises(SystemExit) as error:
            __main__.main(["--version"])

        self.assertEqual(error.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), "0.2.0")

    def test_cpu_test_arguments_are_normalized_without_running_real_load(self) -> None:
        from hardware_validator.cpu_test import application

        for arguments in (
            [
                "test",
                "cpu",
                "--duration",
                "10s",
                "--load",
                "20",
                "--max-temperature",
                "75.5",
                "--allow-no-temperature",
                "--verbose",
            ],
            [
                "--verbose",
                "test",
                "cpu",
                "--duration",
                "10s",
                "--load",
                "20",
                "--max-temperature",
                "75.5",
                "--allow-no-temperature",
            ],
        ):
            with self.subTest(arguments=arguments), patch.object(
                application, "run_cpu_test_command", return_value=0
            ) as run:
                exit_code = __main__.main(arguments)

            self.assertEqual(exit_code, 0)
            config = run.call_args.args[0]
            self.assertEqual(
                config,
                CpuTestConfig(
                    duration_seconds=10,
                    load_percent=20,
                    maximum_temperature_celsius=75.5,
                    allow_no_temperature=True,
                    verbose=True,
                ),
            )

    def test_cpu_test_invalid_arguments_return_argparse_code_two(self) -> None:
        for arguments in (
            ["test", "cpu", "--duration", "9s"],
            ["test", "cpu", "--load", "100"],
            ["test", "cpu", "--max-temperature", "101"],
        ):
            with (
                self.subTest(arguments=arguments),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as error,
            ):
                __main__.main(arguments)
            self.assertEqual(error.exception.code, 2)

    def test_cpu_test_help_does_not_start_the_engine(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as error:
            __main__.main(["test", "cpu", "--help"])
        self.assertEqual(error.exception.code, 0)
        self.assertIn("--duration", output.getvalue())
        self.assertIn("--allow-no-temperature", output.getvalue())

    def test_inventory_commands_never_dispatch_cpu_test(self) -> None:
        from hardware_validator.cpu_test import application

        for arguments in ([], ["--verbose"]):
            with (
                self.subTest(arguments=arguments),
                patch.object(application, "run_cpu_test_command") as cpu_test,
                patch(
                    "hardware_validator.collector.collect_inventory",
                    return_value=empty_results(),
                ),
                patch("hardware_validator.detectors.default_detectors"),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(__main__.main(arguments), 0)
            cpu_test.assert_not_called()

    def test_report_encoding_failure_is_written_to_stderr(self) -> None:
        class RestrictedOutput(io.StringIO):
            def write(self, value: str) -> int:
                raise UnicodeEncodeError("ascii", value, 0, 1, "fixture")

        for arguments in ([], ["--verbose"]):
            with self.subTest(arguments=arguments):
                error = io.StringIO()
                with (
                    patch(
                        "hardware_validator.collector.collect_inventory",
                        return_value=empty_results(),
                    ),
                    patch("hardware_validator.detectors.default_detectors"),
                    redirect_stdout(RestrictedOutput()),
                    redirect_stderr(error),
                ):
                    exit_code = __main__.main(arguments)

                self.assertEqual(exit_code, 1)
                self.assertIn(
                    "Unable to write the hardware inventory", error.getvalue()
                )

    def test_cpu_test_output_failure_returns_one(self) -> None:
        from hardware_validator.cpu_test import application

        error = io.StringIO()
        with (
            patch.object(
                application,
                "run_cpu_test_command",
                side_effect=OSError("fixture\x1berror"),
            ),
            redirect_stderr(error),
        ):
            exit_code = __main__.main(["test", "cpu"])

        self.assertEqual(exit_code, 1)
        self.assertIn(r"fixture\x1berror", error.getvalue())
        self.assertNotIn("\x1b", error.getvalue())

    def test_cpu_test_stderr_failure_still_returns_one(self) -> None:
        from hardware_validator.cpu_test import application

        class RestrictedError(io.StringIO):
            def write(self, value: str) -> int:
                raise OSError("fixture")

        with (
            patch.object(
                application,
                "run_cpu_test_command",
                side_effect=OSError("fixture"),
            ),
            redirect_stderr(RestrictedError()),
        ):
            self.assertEqual(__main__.main(["test", "cpu"]), 1)

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
