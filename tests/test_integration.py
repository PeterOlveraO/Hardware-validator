"""Integration tests for detector orchestration and terminal output."""

import io
import unittest
from contextlib import redirect_stderr
from contextlib import redirect_stdout
from unittest.mock import patch

from hardware_validator import __main__
from hardware_validator.collector import ContractViolationError
from hardware_validator.collector import DetectionResult
from hardware_validator.collector import DetectionStatus
from hardware_validator.collector import InventoryDetectors
from hardware_validator.collector import InventoryResults
from hardware_validator.collector import collect_inventory
from hardware_validator.detectors import CpuDetector
from hardware_validator.detectors import GpuDetector
from hardware_validator.detectors import MemoryDetector
from hardware_validator.detectors import NetworkDetector
from hardware_validator.detectors import StorageDetector
from hardware_validator.detectors import SystemDetector
from hardware_validator.detectors import default_detectors
from hardware_validator.models import CpuInfo
from hardware_validator.models import GpuInventory
from hardware_validator.models import HardwareSnapshot
from hardware_validator.models import MemoryInfo
from hardware_validator.models import NetworkInfo
from hardware_validator.models import StorageInfo
from hardware_validator.models import SystemInfo
from hardware_validator.report import render_inventory

COMPONENT_NAMES = ("system", "cpu", "memory", "storage", "gpu", "network")


class RecordingDetector:
    def __init__(
        self,
        name: str,
        calls: list[str],
        result: DetectionResult[object],
        failure: Exception | None = None,
    ) -> None:
        self._name = name
        self._calls = calls
        self._result = result
        self._failure = failure

    def detect(self) -> DetectionResult[object]:
        self._calls.append(self._name)
        if self._failure is not None:
            raise self._failure
        return self._result


def complete_component_results() -> dict[str, DetectionResult[object]]:
    return {
        "system": DetectionResult.complete(SystemInfo(hostname="fixture-host")),
        "cpu": DetectionResult.complete(CpuInfo(logical_processor_count=4)),
        "memory": DetectionResult.complete(
            MemoryInfo(
                total_bytes=1024,
                available_bytes=768,
                used_bytes=256,
                free_bytes=512,
            )
        ),
        "storage": DetectionResult.complete(StorageInfo()),
        "gpu": DetectionResult.complete(GpuInventory()),
        "network": DetectionResult.complete(NetworkInfo()),
    }


def recording_detectors(
    calls: list[str],
    *,
    results: dict[str, DetectionResult[object]] | None = None,
    failures: dict[str, Exception] | None = None,
) -> InventoryDetectors:
    component_results = results or complete_component_results()
    component_failures = failures or {}
    detectors = {
        name: RecordingDetector(
            name,
            calls,
            component_results[name],
            component_failures.get(name),
        )
        for name in COMPONENT_NAMES
    }
    return InventoryDetectors(**detectors)  # type: ignore[arg-type]


class InventoryIntegrationTests(unittest.TestCase):
    def test_default_factory_wires_every_concrete_detector(self) -> None:
        detectors = default_detectors()

        self.assertIsInstance(detectors.system, SystemDetector)
        self.assertIsInstance(detectors.cpu, CpuDetector)
        self.assertIsInstance(detectors.memory, MemoryDetector)
        self.assertIsInstance(detectors.storage, StorageDetector)
        self.assertIsInstance(detectors.gpu, GpuDetector)
        self.assertIsInstance(detectors.network, NetworkDetector)

    def test_successful_collection_reaches_report_as_valid_models(self) -> None:
        calls: list[str] = []

        results = collect_inventory(recording_detectors(calls))
        report = render_inventory(results)

        self.assertEqual(calls, list(COMPONENT_NAMES))
        self.assertIsInstance(results.snapshot, HardwareSnapshot)
        for section in ("System", "CPU", "Memory", "Storage", "GPU", "Network"):
            self.assertIn(section, report)

    def test_partial_permission_and_complete_results_are_combined(self) -> None:
        calls: list[str] = []
        component_results = complete_component_results()
        component_results["system"] = DetectionResult.partial(
            SystemInfo(hostname="fixture-host"), "Optional DMI field denied."
        )
        component_results["storage"] = DetectionResult.partial(
            StorageInfo(), "Optional mount usage denied."
        )
        detectors = recording_detectors(
            calls,
            results=component_results,
            failures={"memory": PermissionError("fixture")},
        )

        results = collect_inventory(detectors)
        report = render_inventory(results)

        self.assertEqual(calls, list(COMPONENT_NAMES))
        self.assertIs(results.system.status, DetectionStatus.PARTIAL)
        self.assertIs(results.memory.status, DetectionStatus.UNAVAILABLE)
        self.assertIs(results.storage.status, DetectionStatus.PARTIAL)
        self.assertIs(results.gpu.status, DetectionStatus.COMPLETE)
        self.assertIsInstance(results.memory.value, MemoryInfo)
        self.assertIn("Memory\n  Total: Unknown", report)
        self.assertIn("GPU\n  Devices: None detected", report)

    def test_each_operational_detector_failure_is_independently_isolated(self) -> None:
        expected_types = {
            "system": SystemInfo,
            "cpu": CpuInfo,
            "memory": MemoryInfo,
            "storage": StorageInfo,
            "gpu": GpuInventory,
            "network": NetworkInfo,
        }
        for failed_name in COMPONENT_NAMES:
            with self.subTest(component=failed_name):
                calls: list[str] = []
                detectors = recording_detectors(
                    calls,
                    failures={failed_name: PermissionError("fixture")},
                )

                results = collect_inventory(detectors)

                self.assertEqual(calls, list(COMPONENT_NAMES))
                failed_result = getattr(results, failed_name)
                self.assertIs(failed_result.status, DetectionStatus.UNAVAILABLE)
                self.assertIsInstance(
                    failed_result.value, expected_types[failed_name]
                )
                for successful_name in COMPONENT_NAMES:
                    if successful_name != failed_name:
                        self.assertIs(
                            getattr(results, successful_name).status,
                            DetectionStatus.COMPLETE,
                        )

    def test_programming_error_is_fatal_and_not_hidden(self) -> None:
        calls: list[str] = []
        detectors = recording_detectors(
            calls,
            failures={"cpu": AttributeError("fixture")},
        )

        with self.assertRaisesRegex(
            ContractViolationError, "cpu detector raised unexpected AttributeError"
        ):
            collect_inventory(detectors)

        self.assertEqual(calls, ["system", "cpu"])

    def test_invalid_aggregate_is_rejected_before_rendering(self) -> None:
        valid = complete_component_results()

        with self.assertRaisesRegex(
            ContractViolationError, "cpu result value must be CpuInfo"
        ):
            InventoryResults(
                system=valid["system"],  # type: ignore[arg-type]
                cpu=DetectionResult.complete(SystemInfo()),  # type: ignore[arg-type]
                memory=valid["memory"],  # type: ignore[arg-type]
                storage=valid["storage"],  # type: ignore[arg-type]
                gpu=valid["gpu"],  # type: ignore[arg-type]
                network=valid["network"],  # type: ignore[arg-type]
            )

    def test_main_renders_nonfatal_component_failure_and_returns_zero(self) -> None:
        detectors = recording_detectors(
            [], failures={"memory": PermissionError("fixture")}
        )
        output = io.StringIO()
        error = io.StringIO()

        with (
            patch(
                "hardware_validator.detectors.default_detectors",
                return_value=detectors,
            ),
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            exit_code = __main__.main([])

        self.assertEqual(exit_code, 0)
        self.assertEqual(error.getvalue(), "")
        self.assertIn("Memory\n  Total: Unknown", output.getvalue())
        self.assertIn("Graphics\n", output.getvalue())
        self.assertIn("Network\n", output.getvalue())

    def test_main_reports_programming_failure_and_returns_nonzero(self) -> None:
        detectors = recording_detectors(
            [], failures={"cpu": AssertionError("fixture")}
        )
        output = io.StringIO()
        error = io.StringIO()

        with (
            patch(
                "hardware_validator.detectors.default_detectors",
                return_value=detectors,
            ),
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            exit_code = __main__.main([])

        self.assertEqual(exit_code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn(
            "Internal hardware inventory contract error: "
            "cpu detector raised unexpected AssertionError",
            error.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
