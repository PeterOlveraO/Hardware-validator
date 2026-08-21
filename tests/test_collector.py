"""Tests for detector contracts and aggregation."""

import unittest

from hardware_validator.collector import ContractViolationError
from hardware_validator.collector import DetectionResult
from hardware_validator.collector import DetectionStatus
from hardware_validator.collector import InventoryDetectors
from hardware_validator.collector import collect_inventory
from hardware_validator.models import CpuInfo
from hardware_validator.models import GpuInventory
from hardware_validator.models import GpuInfo
from hardware_validator.models import MemoryInfo
from hardware_validator.models import NetworkInfo
from hardware_validator.models import StorageInfo
from hardware_validator.models import SystemInfo


class StaticDetector:
    def __init__(self, result: DetectionResult[object]) -> None:
        self._result = result

    def detect(self) -> DetectionResult[object]:
        return self._result


class FailingDetector:
    def detect(self) -> DetectionResult[GpuInventory]:
        raise PermissionError("fixture")


class WrongValueDetector:
    def detect(self) -> DetectionResult[SystemInfo]:
        return DetectionResult.complete(SystemInfo())


class InvalidResultDetector:
    def detect(self) -> DetectionResult[GpuInventory]:
        return DetectionResult.partial(GpuInventory())


class InvalidModelDetector:
    def detect(self) -> DetectionResult[GpuInventory]:
        invalid_gpu = GpuInfo(vendor_id=1)  # type: ignore[arg-type]
        return DetectionResult.complete(GpuInventory(devices=(invalid_gpu,)))


def make_detectors(gpu: object) -> InventoryDetectors:
    return InventoryDetectors(
        system=StaticDetector(DetectionResult.complete(SystemInfo())),
        cpu=StaticDetector(DetectionResult.complete(CpuInfo())),
        memory=StaticDetector(DetectionResult.complete(MemoryInfo())),
        storage=StaticDetector(DetectionResult.complete(StorageInfo())),
        gpu=gpu,  # type: ignore[arg-type]
        network=StaticDetector(DetectionResult.complete(NetworkInfo())),
    )


class CollectorTests(unittest.TestCase):
    def test_complete_result_has_no_issues(self) -> None:
        result = DetectionResult.complete(SystemInfo())

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(result.issues, ())

    def test_partial_result_requires_an_issue(self) -> None:
        with self.assertRaises(ContractViolationError):
            DetectionResult.partial(SystemInfo())

    def test_operational_failure_is_isolated(self) -> None:
        results = collect_inventory(make_detectors(FailingDetector()))

        self.assertIs(results.gpu.status, DetectionStatus.UNAVAILABLE)
        self.assertEqual(results.snapshot.gpu.devices, ())
        self.assertIs(results.system.status, DetectionStatus.COMPLETE)

    def test_programming_failure_is_fatal(self) -> None:
        class BrokenDetector:
            def detect(self) -> DetectionResult[GpuInventory]:
                raise AttributeError("fixture")

        with self.assertRaisesRegex(
            ContractViolationError, "gpu detector raised unexpected AttributeError"
        ):
            collect_inventory(make_detectors(BrokenDetector()))

    def test_detector_wiring_is_validated(self) -> None:
        with self.assertRaisesRegex(
            ContractViolationError, "gpu detector must provide callable detect"
        ):
            make_detectors(object())

    def test_wrong_detector_value_is_contract_violation(self) -> None:
        with self.assertRaises(ContractViolationError):
            collect_inventory(make_detectors(WrongValueDetector()))

    def test_result_invariant_failure_is_not_recovered(self) -> None:
        with self.assertRaises(ContractViolationError):
            collect_inventory(make_detectors(InvalidResultDetector()))

    def test_model_invariant_failure_is_not_recovered(self) -> None:
        with self.assertRaises(ContractViolationError):
            collect_inventory(make_detectors(InvalidModelDetector()))


if __name__ == "__main__":
    unittest.main()
