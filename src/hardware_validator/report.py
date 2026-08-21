"""Deterministic plain-text inventory presentation."""

from collections.abc import Callable
from decimal import Decimal
import unicodedata

from .collector import DetectionStatus
from .collector import InventoryResults
from .models import PartitionInfo

UNKNOWN = "Unknown"
NONE_DETECTED = "None detected"


def _plain_text(value: object) -> str:
    """Render control characters visibly instead of emitting terminal controls."""

    characters: list[str] = []
    for character in str(value):
        code_point = ord(character)
        if unicodedata.category(character) in {"Cc", "Cf", "Cs"}:
            characters.append(
                f"\\x{code_point:02x}"
                if code_point <= 255
                else (
                    f"\\u{code_point:04x}"
                    if code_point <= 0xFFFF
                    else f"\\U{code_point:08x}"
                )
            )
        else:
            characters.append(character)
    return "".join(characters)


def format_optional(value: object | None) -> str:
    """Format an optional scalar without inventing a replacement value."""

    return UNKNOWN if value is None else _plain_text(value)


def _format_bool(value: bool | None) -> str:
    if value is None:
        return UNKNOWN
    return "Yes" if value else "No"


def _format_scaled(value: int, base: int, units: tuple[str, ...]) -> str:
    unit_index = 0
    divisor = 1
    while unit_index < len(units) - 1 and value >= divisor * base:
        divisor *= base
        unit_index += 1
    if divisor == 1:
        number = str(value)
    else:
        number = format(Decimal(value) / Decimal(divisor), ".2f")
        number = number.rstrip("0").rstrip(".")
    return f"{number} {units[unit_index]}"


def _format_bytes(value: int | None) -> str:
    if value is None:
        return UNKNOWN
    return _format_scaled(
        value,
        1024,
        ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"),
    )


def _format_frequency(value: int | None) -> str:
    if value is None:
        return UNKNOWN
    return _format_scaled(value, 1000, ("Hz", "kHz", "MHz", "GHz", "THz"))


def _format_speed(value: int | None) -> str:
    return UNKNOWN if value is None else f"{value} MT/s"


def _empty_collection(status: DetectionStatus) -> str:
    return NONE_DETECTED if status is DetectionStatus.COMPLETE else UNKNOWN


def _field(
    lines: list[str],
    indent: int,
    label: str,
    value: object | None,
    formatter: Callable[[object | None], str] = format_optional,
) -> None:
    lines.append(f"{' ' * indent}{label}: {formatter(value)}")


def _string_collection(
    lines: list[str],
    indent: int,
    label: str,
    values: tuple[str, ...],
    status: DetectionStatus,
) -> None:
    if not values:
        lines.append(f"{' ' * indent}{label}: {_empty_collection(status)}")
        return
    lines.append(f"{' ' * indent}{label}:")
    lines.extend(f"{' ' * (indent + 2)}- {_plain_text(value)}" for value in values)


def _render_system(results: InventoryResults) -> list[str]:
    system = results.system.value
    lines = ["System"]
    _field(lines, 2, "Distribution", system.distribution)
    _field(lines, 2, "Distribution Version", system.distribution_version)
    _field(lines, 2, "Kernel Version", system.kernel_version)
    _field(lines, 2, "Hostname", system.hostname)
    _field(lines, 2, "Manufacturer", system.manufacturer)
    _field(lines, 2, "Model", system.model)
    _field(lines, 2, "Architecture", system.architecture)
    lines.append("  Baseboard:")
    _field(lines, 4, "Manufacturer", system.baseboard.manufacturer)
    _field(lines, 4, "Model", system.baseboard.model)
    _field(lines, 4, "Version", system.baseboard.version)
    lines.append("  BIOS:")
    _field(lines, 4, "Manufacturer", system.bios.manufacturer)
    _field(lines, 4, "Version", system.bios.version)
    _field(lines, 4, "Release Date", system.bios.release_date)
    return lines


def _render_cpu(results: InventoryResults) -> list[str]:
    result = results.cpu
    cpu = result.value
    lines = ["CPU"]
    _string_collection(lines, 2, "Model Names", cpu.model_names, result.status)
    _string_collection(lines, 2, "Vendor IDs", cpu.vendor_ids, result.status)
    _field(lines, 2, "Architecture", cpu.architecture)
    _field(lines, 2, "Physical Packages", cpu.physical_package_count)
    _field(lines, 2, "Physical Cores", cpu.physical_core_count)
    _field(lines, 2, "Logical Processors", cpu.logical_processor_count)
    _field(
        lines,
        2,
        "Current Frequency",
        cpu.current_frequency_hz,
        _format_frequency,
    )
    _field(
        lines,
        2,
        "Maximum Frequency",
        cpu.maximum_frequency_hz,
        _format_frequency,
    )
    if cpu.caches:
        lines.append("  Caches:")
        for index, cache in enumerate(cpu.caches, start=1):
            lines.append(f"    Cache {index}:")
            _field(lines, 6, "Level", cache.level)
            _field(lines, 6, "Kind", cache.kind)
            _field(lines, 6, "Size", cache.size_bytes, _format_bytes)
    else:
        lines.append(f"  Caches: {_empty_collection(result.status)}")
    _string_collection(lines, 2, "Capabilities", cpu.capabilities, result.status)
    return lines


def _render_memory(results: InventoryResults) -> list[str]:
    result = results.memory
    memory = result.value
    lines = ["Memory"]
    _field(lines, 2, "Total", memory.total_bytes, _format_bytes)
    _field(lines, 2, "Available", memory.available_bytes, _format_bytes)
    _field(lines, 2, "Used", memory.used_bytes, _format_bytes)
    _field(lines, 2, "Free", memory.free_bytes, _format_bytes)
    if memory.modules:
        lines.append("  Modules:")
        for index, module in enumerate(memory.modules, start=1):
            lines.append(f"    Module {index}:")
            _field(lines, 6, "Locator", module.locator)
            _field(lines, 6, "Manufacturer", module.manufacturer)
            _field(lines, 6, "Part Number", module.part_number)
            _field(lines, 6, "Serial Number", module.serial_number)
            _field(lines, 6, "Size", module.size_bytes, _format_bytes)
            _field(lines, 6, "Type", module.memory_type)
            _field(lines, 6, "Speed", module.speed_mt_s, _format_speed)
    else:
        lines.append(f"  Modules: {_empty_collection(result.status)}")
    lines.append("  Swap:")
    _field(lines, 4, "Total", memory.swap.total_bytes, _format_bytes)
    _field(lines, 4, "Available", memory.swap.available_bytes, _format_bytes)
    _field(lines, 4, "Used", memory.swap.used_bytes, _format_bytes)
    return lines


def _render_partition(
    lines: list[str], index: int, partition: PartitionInfo, status: DetectionStatus
) -> None:
    lines.append(f"        Partition {index}:")
    _field(lines, 10, "Name", partition.name)
    _field(lines, 10, "Parent Name", partition.parent_name)
    _field(lines, 10, "Device Path", partition.device_path)
    _field(lines, 10, "Size", partition.size_bytes, _format_bytes)
    _field(lines, 10, "Read Only", partition.read_only, _format_bool)
    _field(lines, 10, "Filesystem", partition.filesystem)
    _string_collection(
        lines, 10, "Mount Points", partition.mount_points, status
    )


def _render_storage(results: InventoryResults) -> list[str]:
    result = results.storage
    storage = result.value
    lines = ["Storage"]
    if storage.disks:
        lines.append("  Disks:")
        for index, disk in enumerate(storage.disks, start=1):
            lines.append(f"    Disk {index}:")
            _field(lines, 6, "Name", disk.name)
            _field(lines, 6, "Device Path", disk.device_path)
            _field(lines, 6, "Vendor", disk.vendor)
            _field(lines, 6, "Model", disk.model)
            _field(lines, 6, "Serial Number", disk.serial_number)
            _field(lines, 6, "Size", disk.size_bytes, _format_bytes)
            _field(lines, 6, "Rotational", disk.rotational, _format_bool)
            _field(lines, 6, "Transport", disk.transport)
            _field(lines, 6, "Removable", disk.removable, _format_bool)
            _field(lines, 6, "Kind", disk.kind)
            _field(lines, 6, "Read Only", disk.read_only, _format_bool)
            _field(
                lines,
                6,
                "Logical Sector Size",
                disk.logical_sector_size,
                _format_bytes,
            )
            _field(
                lines,
                6,
                "Physical Sector Size",
                disk.physical_sector_size,
                _format_bytes,
            )
            if disk.partitions:
                lines.append("      Partitions:")
                for partition_index, partition in enumerate(
                    disk.partitions, start=1
                ):
                    _render_partition(
                        lines, partition_index, partition, result.status
                    )
            else:
                lines.append(
                    f"      Partitions: {_empty_collection(result.status)}"
                )
    else:
        lines.append(f"  Disks: {_empty_collection(result.status)}")
    if storage.mounts:
        lines.append("  Mounts:")
        for index, mount in enumerate(storage.mounts, start=1):
            lines.append(f"    Mount {index}:")
            _field(lines, 6, "Mount Point", mount.mount_point)
            _field(lines, 6, "Source", mount.source)
            _field(lines, 6, "Filesystem", mount.filesystem)
            _field(lines, 6, "Total", mount.total_bytes, _format_bytes)
            _field(lines, 6, "Used", mount.used_bytes, _format_bytes)
            _field(lines, 6, "Available", mount.available_bytes, _format_bytes)
    else:
        lines.append(f"  Mounts: {_empty_collection(result.status)}")
    return lines


def _render_gpu(results: InventoryResults) -> list[str]:
    result = results.gpu
    devices = result.value.devices
    lines = ["GPU"]
    if not devices:
        lines.append(f"  Devices: {_empty_collection(result.status)}")
        return lines
    lines.append("  Devices:")
    for index, gpu in enumerate(devices, start=1):
        lines.append(f"    GPU {index}:")
        _field(lines, 6, "Commercial Name", gpu.commercial_name)
        _field(lines, 6, "Vendor ID", gpu.vendor_id)
        _field(lines, 6, "Device ID", gpu.device_id)
        _field(lines, 6, "Adapter Type", gpu.adapter_type)
        _field(lines, 6, "Memory Total", gpu.memory_total_bytes, _format_bytes)
        _field(lines, 6, "Driver", gpu.driver)
        _field(lines, 6, "Address", gpu.address)
        _field(lines, 6, "Primary", gpu.is_primary, _format_bool)
        _field(lines, 6, "Boot VGA", gpu.is_boot_vga, _format_bool)
        _string_collection(lines, 6, "Connectors", gpu.connectors, result.status)
    return lines


def render_inventory(results: InventoryResults) -> str:
    """Render collected results without detecting hardware or using a terminal."""

    sections = (
        _render_system(results),
        _render_cpu(results),
        _render_memory(results),
        _render_storage(results),
        _render_gpu(results),
    )
    return "\n\n".join("\n".join(section) for section in sections)
