"""Component detector implementations."""

from ..collector import InventoryDetectors
from .cpu import CpuDetector
from .gpu import GpuDetector
from .memory import MemoryDetector
from .network import NetworkDetector
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
        network=NetworkDetector(),
    )


__all__ = [
    "CpuDetector",
    "GpuDetector",
    "MemoryDetector",
    "NetworkDetector",
    "StorageDetector",
    "SystemDetector",
    "default_detectors",
]
