"""Component detector implementations."""

from ..collector import InventoryDetectors
from .cpu import CpuDetector
from .gpu import GpuDetector
from .memory import MemoryDetector
from .storage import StorageDetector
from .system import SystemDetector


def default_detectors() -> InventoryDetectors:
    """Create the standard local Linux detector set."""

    return InventoryDetectors(
        system=SystemDetector(),
        cpu=CpuDetector(),
        memory=MemoryDetector(),
        storage=StorageDetector(),
        gpu=GpuDetector(),
    )


__all__ = [
    "CpuDetector",
    "GpuDetector",
    "MemoryDetector",
    "StorageDetector",
    "SystemDetector",
    "default_detectors",
]
