"""Tests for CPU-test terminal integration without real load."""

from contextlib import nullcontext
import io
import unittest
from contextlib import redirect_stderr
from contextlib import redirect_stdout

from hardware_validator.cpu_test.application import run_cpu_test_command
from hardware_validator.cpu_test.models import CpuTestConfig
from hardware_validator.cpu_test.models import CpuTestReady
from hardware_validator.cpu_test.models import CpuTestResult
from hardware_validator.cpu_test.models import CpuTestSample
from hardware_validator.cpu_test.models import CpuTestStatus
from hardware_validator.cpu_test.models import MonitoredTemperature


def make_result(
    status: CpuTestStatus,
    *,
    reason: str = "fixture reason",
    warnings: tuple[str, ...] = (),
) -> CpuTestResult:
    return CpuTestResult(
        status=status,
        reason=reason,
        requested_duration_seconds=10,
        actual_duration_seconds=10,
        requested_load_percent=20,
        worker_count=1,
        sample_count=1,
        utilization_average_percent=25,
        utilization_minimum_percent=25,
        utilization_maximum_percent=25,
        initial_temperature_celsius=40,
        maximum_temperature_celsius=41,
        initial_frequency_hz=1_000_000_000,
        minimum_frequency_hz=900_000_000,
        maximum_frequency_hz=1_100_000_000,
        warnings=warnings,
    )


class FakeEngine:
    def __init__(self, result: CpuTestResult) -> None:
        self.result = result

    def run(self, config: CpuTestConfig, observer) -> CpuTestResult:
        sensor = MonitoredTemperature(
            identity="fixture\x1bsensor",
            chip_name="fixture",
            label="label",
            current_celsius=41,
            effective_limit_celsius=85,
        )
        observer.on_ready(CpuTestReady(1, True, (sensor,)))
        observer.on_worker_event("worker started")
        observer.on_sample(
            CpuTestSample(
                elapsed_seconds=1,
                total_utilization_percent=20,
                per_processor_utilization_percent=(20,),
                aggregate_frequency_hz=1_000_000_000,
                per_processor_frequency_hz=(1_000_000_000,),
                temperatures=(sensor,),
                workers_alive=1,
                worker_count=1,
            )
        )
        observer.on_sample(
            CpuTestSample(
                elapsed_seconds=5,
                total_utilization_percent=25,
                per_processor_utilization_percent=(25,),
                aggregate_frequency_hz=1_100_000_000,
                per_processor_frequency_hz=(1_100_000_000,),
                temperatures=(sensor,),
                workers_alive=1,
                worker_count=1,
            )
        )
        for warning in self.result.warnings:
            observer.on_warning(warning)
        if self.result.status is not CpuTestStatus.COMPLETED:
            observer.on_error(self.result.reason)
        return self.result


class FakeSignals:
    def pending(self):
        return None

    def installed(self):
        return nullcontext()


class CpuTestApplicationTests(unittest.TestCase):
    def run_command(self, result, *, verbose=False):
        output = io.StringIO()
        errors = io.StringIO()
        code = run_cpu_test_command(
            CpuTestConfig(duration_seconds=10, load_percent=20, verbose=verbose),
            engine=FakeEngine(result),  # type: ignore[arg-type]
            processor_model="Fixture\x1b CPU",
            stdout=output,
            stderr=errors,
            signal_controller=FakeSignals(),  # type: ignore[arg-type]
        )
        return code, output.getvalue(), errors.getvalue()

    def test_normal_output_contains_configuration_progress_and_final_result(self) -> None:
        code, output, errors = self.run_command(
            make_result(CpuTestStatus.COMPLETED)
        )
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertIn("Processor Model: Fixture\\x1b CPU", output)
        self.assertIn("Requested Duration: 10 seconds", output)
        self.assertIn("Requested Load: 20% duty cycle", output)
        self.assertEqual(output.count("Progress:"), 1)
        self.assertIn("elapsed=5.0s", output)
        self.assertIn("Status: completed", output)
        self.assertIn("does not certify CPU health", output)
        self.assertNotIn("\x1b", output)
        for forbidden in ("PASS", "FAIL", "healthy", "stable"):
            self.assertNotIn(forbidden, output)

    def test_verbose_output_reports_every_sample_and_technical_values(self) -> None:
        code, output, _ = self.run_command(
            make_result(CpuTestStatus.COMPLETED), verbose=True
        )
        self.assertEqual(code, 0)
        self.assertEqual(output.count("Progress:"), 2)
        self.assertIn("Per-processor utilization", output)
        self.assertIn("Per-processor frequencies", output)
        self.assertIn("Monitored temperature sensors", output)
        self.assertIn("Worker event", output)
        self.assertIn("sequence=", output)
        self.assertIn("min 41.0 C", output)

    def test_warnings_and_errors_are_separated_to_stderr(self) -> None:
        code, output, errors = self.run_command(
            make_result(
                CpuTestStatus.ERROR,
                reason="sensor\x1berror",
                warnings=("thermal warning",),
            )
        )
        self.assertEqual(code, 1)
        self.assertIn("Status: error", output)
        self.assertIn("Warning: thermal warning", errors)
        self.assertIn(r"sensor\x1berror", errors)
        self.assertNotIn("\x1b", errors)

    def test_all_result_exit_codes(self) -> None:
        cases = (
            (CpuTestStatus.COMPLETED, "completed", 0),
            (CpuTestStatus.TEMPERATURE_LIMIT, "temperature", 1),
            (CpuTestStatus.ERROR, "error", 1),
            (CpuTestStatus.CANCELLED, "Interrupted by SIGTERM.", 1),
            (CpuTestStatus.CANCELLED, "Interrupted by Ctrl+C.", 130),
        )
        for status, reason, expected in cases:
            with self.subTest(status=status, reason=reason):
                code, _, _ = self.run_command(make_result(status, reason=reason))
                self.assertEqual(code, expected)

    def test_default_streams_support_standard_redirection(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = run_cpu_test_command(
                CpuTestConfig(duration_seconds=10, load_percent=20),
                engine=FakeEngine(
                    make_result(
                        CpuTestStatus.ERROR,
                        warnings=("redirected warning",),
                    )
                ),  # type: ignore[arg-type]
                processor_model="Fixture CPU",
                signal_controller=FakeSignals(),  # type: ignore[arg-type]
            )
        self.assertEqual(code, 1)
        self.assertIn("CPU Test", output.getvalue())
        self.assertIn("redirected warning", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
