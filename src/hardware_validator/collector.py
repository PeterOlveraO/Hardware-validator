"""Internal detector contracts and inventory aggregation."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Generic, Protocol, TypeVar

from .models import CpuInfo
from .models import GpuInventory
from .models import HardwareSnapshot
from .models import MemoryInfo
from .models import StorageInfo
from .models import SystemInfo
from .models import _ModelTypeError
from .models import _ModelValueError

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


class ContractViolationError(RuntimeError):
    """Raised when detector output violates the internal contract."""


class DetectionStatus(str, Enum):
    """Internal detection completeness state."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class DetectionResult(Generic[T_co]):
    """Internal detector output; never part of the public inventory model."""

    value: T_co
    status: DetectionStatus
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, DetectionStatus):
            raise ContractViolationError("status must be DetectionStatus")
        if not isinstance(self.issues, tuple):
            raise ContractViolationError("issues must be a tuple")
        if any(not isinstance(issue, str) or not issue.strip() for issue in self.issues):
            raise ContractViolationError("issues must contain non-empty strings")
        if self.status is DetectionStatus.COMPLETE and self.issues:
            raise ContractViolationError("complete results cannot contain issues")
        if self.status is not DetectionStatus.COMPLETE and not self.issues:
            raise ContractViolationError("incomplete results must contain an issue")

    @classmethod
    def complete(cls, value: T) -> "DetectionResult[T]":
        return cls(value=value, status=DetectionStatus.COMPLETE)

    @classmethod
    def partial(cls, value: T, *issues: str) -> "DetectionResult[T]":
        return cls(value=value, status=DetectionStatus.PARTIAL, issues=issues)

    @classmethod
    def unavailable(cls, value: T, *issues: str) -> "DetectionResult[T]":
        return cls(value=value, status=DetectionStatus.UNAVAILABLE, issues=issues)


class Detector(Protocol[T_co]):
    """Contract implemented by each component detector."""

    def detect(self) -> DetectionResult[T_co]:
        """Collect one component without raising expected source errors."""


@dataclass(frozen=True, slots=True)
class InventoryDetectors:
    system: Detector[SystemInfo]
    cpu: Detector[CpuInfo]
    memory: Detector[MemoryInfo]
    storage: Detector[StorageInfo]
    gpu: Detector[GpuInventory]

    def __post_init__(self) -> None:
        for name in ("system", "cpu", "memory", "storage", "gpu"):
            detector = getattr(self, name)
            if not callable(getattr(detector, "detect", None)):
                raise ContractViolationError(
                    f"{name} detector must provide callable detect()"
                )


@dataclass(frozen=True, slots=True)
class InventoryResults:
    """Internal aggregate retaining completeness through report rendering."""

    system: DetectionResult[SystemInfo]
    cpu: DetectionResult[CpuInfo]
    memory: DetectionResult[MemoryInfo]
    storage: DetectionResult[StorageInfo]
    gpu: DetectionResult[GpuInventory]

    def __post_init__(self) -> None:
        expected_types = {
            "system": SystemInfo,
            "cpu": CpuInfo,
            "memory": MemoryInfo,
            "storage": StorageInfo,
            "gpu": GpuInventory,
        }
        for name, expected_type in expected_types.items():
            result = getattr(self, name)
            if not isinstance(result, DetectionResult):
                raise ContractViolationError(
                    f"{name} result must be DetectionResult"
                )
            if not isinstance(result.value, expected_type):
                raise ContractViolationError(
                    f"{name} result value must be {expected_type.__name__}"
                )

    @property
    def snapshot(self) -> HardwareSnapshot:
        return HardwareSnapshot(
            system=self.system.value,
            cpu=self.cpu.value,
            memory=self.memory.value,
            storage=self.storage.value,
            gpu=self.gpu.value,
        )


def _run_detector(
    name: str,
    detector: Detector[T],
    fallback: Callable[[], T],
    expected_type: type[T],
) -> DetectionResult[T]:
    try:
        result = detector.detect()
        if not isinstance(result, DetectionResult):
            raise ContractViolationError(
                f"{name} detector must return DetectionResult"
            )
        if not isinstance(result.value, expected_type):
            raise ContractViolationError(
                f"{name} detector value must be {expected_type.__name__}"
            )
        return result
    except ContractViolationError:
        raise
    except (_ModelTypeError, _ModelValueError) as error:
        raise ContractViolationError(
            f"{name} detector produced an invalid model: {error}"
        ) from error
    except (OSError, NotImplementedError) as error:
        issue = f"{name} detector unavailable: {type(error).__name__}"
        return DetectionResult.unavailable(fallback(), issue)
    except Exception as error:
        raise ContractViolationError(
            f"{name} detector raised unexpected {type(error).__name__}"
        ) from error


def collect_inventory(detectors: InventoryDetectors) -> InventoryResults:
    """Run all detectors while isolating unexpected component failures."""

    return InventoryResults(
        system=_run_detector("system", detectors.system, SystemInfo, SystemInfo),
        cpu=_run_detector("cpu", detectors.cpu, CpuInfo, CpuInfo),
        memory=_run_detector("memory", detectors.memory, MemoryInfo, MemoryInfo),
        storage=_run_detector(
            "storage", detectors.storage, StorageInfo, StorageInfo
        ),
        gpu=_run_detector("gpu", detectors.gpu, GpuInventory, GpuInventory),
    )
