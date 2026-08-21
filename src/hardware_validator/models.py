"""Public hardware inventory data models."""

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

CacheKind: TypeAlias = Literal["data", "instruction", "unified"]
AdapterType: TypeAlias = Literal["integrated", "dedicated"]
BlockDeviceKind: TypeAlias = Literal[
    "hdd", "ssd", "nvme", "emmc", "removable", "virtual"
]


class _ModelValueError(ValueError):
    """Value invariant failure distinguishable at the collector boundary."""


class _ModelTypeError(TypeError):
    """Type invariant failure distinguishable at the collector boundary."""


def _validate_optional_string(name: str, value: str | None) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise _ModelValueError(f"{name} must be a non-empty string or None")


def _validate_optional_non_negative_int(name: str, value: int | None) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise _ModelValueError(f"{name} must be a non-negative integer or None")


def _validate_optional_positive_int(name: str, value: int | None) -> None:
    if value is not None and (type(value) is not int or value <= 0):
        raise _ModelValueError(f"{name} must be a positive integer or None")


def _validate_tuple(name: str, value: tuple[object, ...], item_type: type) -> None:
    if not isinstance(value, tuple):
        raise _ModelTypeError(f"{name} must be a tuple")
    if not all(isinstance(item, item_type) for item in value):
        raise _ModelTypeError(f"every {name} item must be {item_type.__name__}")


def _validate_string_tuple(name: str, value: tuple[str, ...]) -> None:
    _validate_tuple(name, value, str)
    if any(not item.strip() for item in value):
        raise _ModelValueError(f"every {name} item must be non-empty")


@dataclass(frozen=True, slots=True)
class BaseboardInfo:
    manufacturer: str | None = None
    model: str | None = None
    version: str | None = None

    def __post_init__(self) -> None:
        _validate_optional_string("manufacturer", self.manufacturer)
        _validate_optional_string("model", self.model)
        _validate_optional_string("version", self.version)


@dataclass(frozen=True, slots=True)
class BiosInfo:
    manufacturer: str | None = None
    version: str | None = None
    release_date: str | None = None

    def __post_init__(self) -> None:
        _validate_optional_string("manufacturer", self.manufacturer)
        _validate_optional_string("version", self.version)
        _validate_optional_string("release_date", self.release_date)


@dataclass(frozen=True, slots=True)
class SystemInfo:
    distribution: str | None = None
    distribution_version: str | None = None
    kernel_version: str | None = None
    hostname: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    architecture: str | None = None
    baseboard: BaseboardInfo = field(default_factory=BaseboardInfo)
    bios: BiosInfo = field(default_factory=BiosInfo)

    def __post_init__(self) -> None:
        for name in (
            "distribution",
            "distribution_version",
            "kernel_version",
            "hostname",
            "manufacturer",
            "model",
            "architecture",
        ):
            _validate_optional_string(name, getattr(self, name))
        if not isinstance(self.baseboard, BaseboardInfo):
            raise _ModelTypeError("baseboard must be BaseboardInfo")
        if not isinstance(self.bios, BiosInfo):
            raise _ModelTypeError("bios must be BiosInfo")


@dataclass(frozen=True, slots=True)
class CpuCacheInfo:
    level: int
    kind: CacheKind
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        if type(self.level) is not int or self.level < 0:
            raise _ModelValueError("level must be a non-negative integer")
        if self.kind not in {"data", "instruction", "unified"}:
            raise _ModelValueError("kind must be data, instruction, or unified")
        _validate_optional_non_negative_int("size_bytes", self.size_bytes)


@dataclass(frozen=True, slots=True)
class CpuInfo:
    model_names: tuple[str, ...] = ()
    vendor_ids: tuple[str, ...] = ()
    architecture: str | None = None
    physical_package_count: int | None = None
    physical_core_count: int | None = None
    logical_processor_count: int | None = None
    current_frequency_hz: int | None = None
    maximum_frequency_hz: int | None = None
    caches: tuple[CpuCacheInfo, ...] = ()
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_string_tuple("model_names", self.model_names)
        _validate_string_tuple("vendor_ids", self.vendor_ids)
        _validate_optional_string("architecture", self.architecture)
        _validate_optional_non_negative_int(
            "physical_package_count", self.physical_package_count
        )
        _validate_optional_non_negative_int(
            "physical_core_count", self.physical_core_count
        )
        _validate_optional_non_negative_int(
            "logical_processor_count", self.logical_processor_count
        )
        _validate_optional_positive_int(
            "current_frequency_hz", self.current_frequency_hz
        )
        _validate_optional_positive_int(
            "maximum_frequency_hz", self.maximum_frequency_hz
        )
        _validate_tuple("caches", self.caches, CpuCacheInfo)
        _validate_string_tuple("capabilities", self.capabilities)


@dataclass(frozen=True, slots=True)
class MemoryModuleInfo:
    locator: str | None = None
    manufacturer: str | None = None
    part_number: str | None = None
    serial_number: str | None = None
    size_bytes: int | None = None
    memory_type: str | None = None
    speed_mt_s: int | None = None

    def __post_init__(self) -> None:
        _validate_optional_string("locator", self.locator)
        _validate_optional_string("manufacturer", self.manufacturer)
        _validate_optional_string("part_number", self.part_number)
        _validate_optional_string("serial_number", self.serial_number)
        _validate_optional_non_negative_int("size_bytes", self.size_bytes)
        _validate_optional_string("memory_type", self.memory_type)
        _validate_optional_non_negative_int("speed_mt_s", self.speed_mt_s)


@dataclass(frozen=True, slots=True)
class SwapInfo:
    total_bytes: int | None = None
    available_bytes: int | None = None
    used_bytes: int | None = None

    def __post_init__(self) -> None:
        for name in ("total_bytes", "available_bytes", "used_bytes"):
            _validate_optional_non_negative_int(name, getattr(self, name))
        if self.total_bytes is not None:
            for name in ("available_bytes", "used_bytes"):
                value = getattr(self, name)
                if value is not None and value > self.total_bytes:
                    raise _ModelValueError(f"{name} cannot exceed total_bytes")
        if (
            self.total_bytes is not None
            and self.available_bytes is not None
            and self.used_bytes is not None
            and self.used_bytes != self.total_bytes - self.available_bytes
        ):
            raise _ModelValueError(
                "used_bytes must equal total_bytes - available_bytes"
            )


@dataclass(frozen=True, slots=True)
class MemoryInfo:
    total_bytes: int | None = None
    available_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    modules: tuple[MemoryModuleInfo, ...] = ()
    swap: SwapInfo = field(default_factory=SwapInfo)

    def __post_init__(self) -> None:
        _validate_optional_positive_int("total_bytes", self.total_bytes)
        for name in ("available_bytes", "used_bytes", "free_bytes"):
            _validate_optional_non_negative_int(name, getattr(self, name))
        if self.total_bytes is not None:
            for name in ("available_bytes", "used_bytes", "free_bytes"):
                value = getattr(self, name)
                if value is not None and value > self.total_bytes:
                    raise _ModelValueError(f"{name} cannot exceed total_bytes")
        if (
            self.total_bytes is not None
            and self.available_bytes is not None
            and self.used_bytes is not None
            and self.used_bytes != self.total_bytes - self.available_bytes
        ):
            raise _ModelValueError(
                "used_bytes must equal total_bytes - available_bytes"
            )
        _validate_tuple("modules", self.modules, MemoryModuleInfo)
        if not isinstance(self.swap, SwapInfo):
            raise _ModelTypeError("swap must be SwapInfo")


@dataclass(frozen=True, slots=True)
class PartitionInfo:
    name: str
    parent_name: str
    device_path: str | None = None
    size_bytes: int | None = None
    read_only: bool | None = None
    filesystem: str | None = None
    mount_points: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("name", "parent_name"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise _ModelValueError(f"{name} must be a non-empty string")
        _validate_optional_string("device_path", self.device_path)
        _validate_optional_non_negative_int("size_bytes", self.size_bytes)
        if self.read_only is not None and type(self.read_only) is not bool:
            raise _ModelTypeError("read_only must be bool or None")
        _validate_optional_string("filesystem", self.filesystem)
        _validate_string_tuple("mount_points", self.mount_points)


@dataclass(frozen=True, slots=True)
class MountInfo:
    mount_point: str
    source: str | None = None
    filesystem: str | None = None
    total_bytes: int | None = None
    used_bytes: int | None = None
    available_bytes: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mount_point, str) or not self.mount_point.strip():
            raise _ModelValueError("mount_point must be a non-empty string")
        _validate_optional_string("source", self.source)
        _validate_optional_string("filesystem", self.filesystem)
        for name in ("total_bytes", "used_bytes", "available_bytes"):
            _validate_optional_non_negative_int(name, getattr(self, name))
        if self.total_bytes is not None:
            for name in ("used_bytes", "available_bytes"):
                value = getattr(self, name)
                if value is not None and value > self.total_bytes:
                    raise _ModelValueError(f"{name} cannot exceed total_bytes")
            if (
                self.used_bytes is not None
                and self.available_bytes is not None
                and self.used_bytes + self.available_bytes > self.total_bytes
            ):
                raise _ModelValueError(
                    "used_bytes plus available_bytes cannot exceed total_bytes"
                )


@dataclass(frozen=True, slots=True)
class DiskInfo:
    name: str
    device_path: str | None = None
    vendor: str | None = None
    model: str | None = None
    serial_number: str | None = None
    size_bytes: int | None = None
    rotational: bool | None = None
    transport: str | None = None
    removable: bool | None = None
    kind: BlockDeviceKind | None = None
    read_only: bool | None = None
    logical_sector_size: int | None = None
    physical_sector_size: int | None = None
    partitions: tuple[PartitionInfo, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise _ModelValueError("name must be a non-empty string")
        for name in ("device_path", "vendor", "model", "serial_number", "transport"):
            _validate_optional_string(name, getattr(self, name))
        _validate_optional_non_negative_int("size_bytes", self.size_bytes)
        for name in ("rotational", "removable", "read_only"):
            value = getattr(self, name)
            if value is not None and type(value) is not bool:
                raise _ModelTypeError(f"{name} must be bool or None")
        if self.kind not in {
            None,
            "hdd",
            "ssd",
            "nvme",
            "emmc",
            "removable",
            "virtual",
        }:
            raise _ModelValueError(
                "kind must be a supported block device kind or None"
            )
        _validate_optional_non_negative_int(
            "logical_sector_size", self.logical_sector_size
        )
        _validate_optional_non_negative_int(
            "physical_sector_size", self.physical_sector_size
        )
        _validate_tuple("partitions", self.partitions, PartitionInfo)


@dataclass(frozen=True, slots=True)
class StorageInfo:
    disks: tuple[DiskInfo, ...] = ()
    mounts: tuple[MountInfo, ...] = ()

    def __post_init__(self) -> None:
        _validate_tuple("disks", self.disks, DiskInfo)
        _validate_tuple("mounts", self.mounts, MountInfo)


@dataclass(frozen=True, slots=True)
class GpuInfo:
    commercial_name: str | None = None
    vendor_id: str | None = None
    device_id: str | None = None
    adapter_type: AdapterType | None = None
    memory_total_bytes: int | None = None
    driver: str | None = None
    address: str | None = None
    is_primary: bool | None = None
    is_boot_vga: bool | None = None
    connectors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_optional_string("commercial_name", self.commercial_name)
        _validate_optional_string("driver", self.driver)
        _validate_optional_string("address", self.address)
        for name in ("vendor_id", "device_id"):
            value = getattr(self, name)
            if value is not None:
                if (
                    not isinstance(value, str)
                    or len(value) != 6
                    or not value.startswith("0x")
                    or any(
                        character not in "0123456789abcdef"
                        for character in value[2:]
                    )
                ):
                    raise _ModelValueError(
                        f"{name} must use lowercase 0xNNNN format"
                    )
        if self.adapter_type not in {None, "integrated", "dedicated"}:
            raise _ModelValueError(
                "adapter_type must be integrated, dedicated, or None"
            )
        _validate_optional_non_negative_int(
            "memory_total_bytes", self.memory_total_bytes
        )
        if self.is_primary is not None and type(self.is_primary) is not bool:
            raise _ModelTypeError("is_primary must be bool or None")
        if self.is_boot_vga is not None and type(self.is_boot_vga) is not bool:
            raise _ModelTypeError("is_boot_vga must be bool or None")
        _validate_string_tuple("connectors", self.connectors)


@dataclass(frozen=True, slots=True)
class GpuInventory:
    devices: tuple[GpuInfo, ...] = ()

    def __post_init__(self) -> None:
        _validate_tuple("devices", self.devices, GpuInfo)


@dataclass(frozen=True, slots=True)
class HardwareSnapshot:
    system: SystemInfo = field(default_factory=SystemInfo)
    cpu: CpuInfo = field(default_factory=CpuInfo)
    memory: MemoryInfo = field(default_factory=MemoryInfo)
    storage: StorageInfo = field(default_factory=StorageInfo)
    gpu: GpuInventory = field(default_factory=GpuInventory)

    def __post_init__(self) -> None:
        expected_types = {
            "system": SystemInfo,
            "cpu": CpuInfo,
            "memory": MemoryInfo,
            "storage": StorageInfo,
            "gpu": GpuInventory,
        }
        for name, expected_type in expected_types.items():
            if not isinstance(getattr(self, name), expected_type):
                raise _ModelTypeError(f"{name} must be {expected_type.__name__}")
