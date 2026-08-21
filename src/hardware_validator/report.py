"""Deterministic plain-text inventory presentation."""

from collections.abc import Callable
from decimal import Decimal
import unicodedata

from .collector import DetectionStatus
from .collector import InventoryResults
from .models import NetworkAddressInfo
from .models import NetworkGatewayInfo
from .models import NetworkInfo
from .models import NetworkInterfaceInfo
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


def _format_network_speed(value: int | None) -> str:
    if value is None:
        return UNKNOWN
    return _format_scaled(value, 1000, ("Mbps", "Gbps", "Tbps"))


def _format_connection_status(value: bool | None) -> str:
    if value is None:
        return UNKNOWN
    return "Connected" if value else "Disconnected"


def _format_duplex(value: str | None) -> str:
    return UNKNOWN if value is None else value.capitalize()


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


def _summary_computer(results: InventoryResults) -> list[str]:
    system = results.system.value
    lines = ["Computer"]
    _field(lines, 2, "Manufacturer", system.manufacturer)
    _field(lines, 2, "Model", system.model)
    _field(lines, 2, "Distribution", system.distribution)
    _field(lines, 2, "Version", system.distribution_version)
    _field(lines, 2, "Architecture", system.architecture)
    return lines


def _summary_processor(results: InventoryResults) -> list[str]:
    cpu = results.cpu.value
    lines = ["Processor"]
    _field(lines, 2, "Model", cpu.model_names[0] if cpu.model_names else None)
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
    return lines


def _summary_memory(results: InventoryResults) -> list[str]:
    memory = results.memory.value
    lines = ["Memory"]
    _field(lines, 2, "Total", memory.total_bytes, _format_bytes)
    _field(lines, 2, "Used", memory.used_bytes, _format_bytes)
    _field(lines, 2, "Available", memory.available_bytes, _format_bytes)
    _field(lines, 2, "Free", memory.free_bytes, _format_bytes)
    modules_are_known = bool(memory.modules) and all(
        any(
            value is not None
            for value in (
                module.locator,
                module.manufacturer,
                module.part_number,
                module.serial_number,
                module.size_bytes,
                module.memory_type,
                module.speed_mt_s,
            )
        )
        for module in memory.modules
    )
    module_summary = (
        f"{len(memory.modules)} detected" if modules_are_known else None
    )
    _field(lines, 2, "Modules", module_summary)
    return lines


def _summary_storage(results: InventoryResults) -> list[str]:
    result = results.storage
    storage = result.value
    disks = tuple(
        disk for disk in storage.disks if disk.kind not in {"virtual", "removable"}
    )
    lines = ["Storage"]
    if disks:
        lines.append("  Devices:")
        for index, disk in enumerate(disks, start=1):
            lines.append(f"    Device {index}:")
            _field(lines, 6, "Model", disk.model or disk.name)
            _field(lines, 6, "Capacity", disk.size_bytes, _format_bytes)
            _field(lines, 6, "Rotational", disk.rotational, _format_bool)
    else:
        lines.append(f"  Devices: {_empty_collection(result.status)}")

    root_mount = next(
        (mount for mount in storage.mounts if mount.mount_point == "/"), None
    )
    if root_mount is None:
        lines.append("  Root Filesystem: Unknown")
    else:
        lines.append("  Root Filesystem:")
        _field(lines, 4, "Total", root_mount.total_bytes, _format_bytes)
        _field(lines, 4, "Used", root_mount.used_bytes, _format_bytes)
        _field(lines, 4, "Available", root_mount.available_bytes, _format_bytes)
    return lines


def _summary_graphics(results: InventoryResults) -> list[str]:
    result = results.gpu
    devices = result.value.devices
    lines = ["Graphics"]
    if not devices:
        lines.append(f"  Devices: {_empty_collection(result.status)}")
        return lines
    lines.append("  Devices:")
    for index, gpu in enumerate(devices, start=1):
        lines.append(f"    GPU {index}:")
        if gpu.commercial_name is not None:
            name = gpu.commercial_name
        elif gpu.vendor_id is not None or gpu.device_id is not None:
            name = (
                f"PCI device {format_optional(gpu.vendor_id)}:"
                f"{format_optional(gpu.device_id)}"
            )
        else:
            name = None
        _field(lines, 6, "Name", name)
        _field(lines, 6, "Driver", gpu.driver)
        if gpu.adapter_type is not None:
            _field(lines, 6, "Type", gpu.adapter_type)
        if gpu.memory_total_bytes is not None:
            _field(lines, 6, "Memory", gpu.memory_total_bytes, _format_bytes)
    return lines


def _summary_network_interface(
    lines: list[str],
    indent: int,
    interface: NetworkInterfaceInfo,
    network: NetworkInfo,
) -> None:
    type_label = "Ethernet" if interface.interface_type == "ethernet" else "Wi-Fi"
    _field(lines, indent, "Type", type_label)
    _field(lines, indent, "Interface", interface.name)
    _field(lines, indent, "Status", interface.is_up, _format_connection_status)
    if interface.speed_mbps is not None:
        _field(lines, indent, "Speed", interface.speed_mbps, _format_network_speed)
    addresses = interface.ipv4_addresses
    if addresses is None:
        lines.append(f"{' ' * indent}IPv4: Unknown")
        lines.append(f"{' ' * indent}Subnet Mask: Unknown")
    elif not addresses:
        lines.append(f"{' ' * indent}IPv4: None assigned")
    elif len(addresses) == 1:
        _field(lines, indent, "IPv4", addresses[0].address)
        _field(lines, indent, "Subnet Mask", addresses[0].subnet_mask)
    else:
        for index, address in enumerate(addresses, start=1):
            _field(lines, indent, f"IPv4 {index}", address.address)
            _field(lines, indent, f"Subnet Mask {index}", address.subnet_mask)
    _field(lines, indent, "MAC", interface.mac_address)
    gateways = network.ipv4_default_gateways
    if gateways is None:
        gateway = UNKNOWN
    else:
        matching_gateway = next(
            (
                candidate
                for candidate in gateways
                if candidate.interface_name == interface.name
            ),
            None,
        )
        gateway = (
            matching_gateway.address
            if matching_gateway is not None
            else "None assigned"
        )
    _field(lines, indent, "Default Gateway", gateway)


def _summary_network_group(
    lines: list[str],
    label: str,
    interfaces: tuple[NetworkInterfaceInfo, ...],
    status: DetectionStatus,
    network: NetworkInfo,
) -> None:
    if not interfaces:
        lines.append(f"  {label}: {_empty_collection(status)}")
    elif len(interfaces) == 1:
        lines.append(f"  {label}:")
        _summary_network_interface(lines, 4, interfaces[0], network)
    else:
        lines.append(f"  {label}:")
        for index, interface in enumerate(interfaces, start=1):
            lines.append(f"    Interface {index}:")
            _summary_network_interface(lines, 6, interface, network)


def _render_dns_collection(
    lines: list[str], label: str, values: tuple[str, ...] | None, indent: int
) -> None:
    if values is None:
        lines.append(f"{' ' * indent}{label}: Unknown")
    elif not values:
        lines.append(f"{' ' * indent}{label}: None configured")
    else:
        lines.append(f"{' ' * indent}{label}:")
        lines.extend(
            f"{' ' * (indent + 2)}- {_plain_text(value)}" for value in values
        )


def _summary_network(results: InventoryResults) -> list[str]:
    result = results.network
    interfaces = result.value.interfaces
    network = result.value
    lines = ["Network"]
    if not interfaces and result.status is not DetectionStatus.COMPLETE:
        lines.append("  Interfaces: Unknown")
    else:
        ethernet = tuple(
            interface
            for interface in interfaces
            if interface.interface_type == "ethernet"
        )
        wifi = tuple(
            interface
            for interface in interfaces
            if interface.interface_type == "wifi"
        )
        _summary_network_group(
            lines, "Ethernet", ethernet, result.status, network
        )
        _summary_network_group(lines, "Wi-Fi", wifi, result.status, network)
    lines.append("")
    _render_dns_collection(lines, "Configured DNS", network.configured_dns, 2)
    return lines


def render_summary(results: InventoryResults) -> str:
    """Render the short default inventory without performing detection."""

    sections = (
        _summary_computer(results),
        _summary_processor(results),
        _summary_memory(results),
        _summary_storage(results),
        _summary_graphics(results),
        _summary_network(results),
    )
    return "\n\n".join("\n".join(section) for section in sections)


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


def _render_network(results: InventoryResults) -> list[str]:
    result = results.network
    interfaces = result.value.interfaces
    lines = ["Network"]
    if not interfaces:
        lines.append(f"  Interfaces: {_empty_collection(result.status)}")
    else:
        lines.append("  Interfaces:")
        for index, interface in enumerate(interfaces, start=1):
            lines.append(f"    Interface {index}:")
            _field(lines, 6, "Name", interface.name)
            _field(lines, 6, "Type", interface.interface_type)
            _field(lines, 6, "Status", interface.is_up, _format_connection_status)
            _field(lines, 6, "MAC", interface.mac_address)
            _render_network_addresses(
                lines, "IPv4 Addresses", interface.ipv4_addresses, 4
            )
            _render_network_addresses(
                lines, "IPv6 Addresses", interface.ipv6_addresses, 6
            )
            _field(lines, 6, "Speed", interface.speed_mbps, _format_network_speed)
            mtu = f"{interface.mtu} bytes" if interface.mtu is not None else None
            _field(lines, 6, "MTU", mtu)
            _field(lines, 6, "Duplex", interface.duplex, _format_duplex)
    _render_gateway_collection(
        lines, "IPv4 Default Gateways", result.value.ipv4_default_gateways, 4
    )
    _render_gateway_collection(
        lines, "IPv6 Default Gateways", result.value.ipv6_default_gateways, 6
    )
    _render_dns_collection(lines, "Configured DNS", result.value.configured_dns, 2)
    if result.value.upstream_dns is not None:
        _render_dns_collection(lines, "Upstream DNS", result.value.upstream_dns, 2)
    return lines


def _render_network_addresses(
    lines: list[str],
    label: str,
    addresses: tuple[NetworkAddressInfo, ...] | None,
    ip_version: int,
) -> None:
    if addresses is None:
        lines.append(f"      {label}: Unknown")
    elif not addresses:
        lines.append(f"      {label}: None assigned")
    else:
        lines.append(f"      {label}:")
        for index, address in enumerate(addresses, start=1):
            lines.append(f"        Address {index}:")
            _field(lines, 10, "Address", address.address)
            if ip_version == 4:
                _field(lines, 10, "Subnet Mask", address.subnet_mask)
            _field(lines, 10, "Prefix Length", address.prefix_length)


def _render_gateway_collection(
    lines: list[str],
    label: str,
    gateways: tuple[NetworkGatewayInfo, ...] | None,
    ip_version: int,
) -> None:
    selected = (
        None
        if gateways is None
        else tuple(gateway for gateway in gateways if gateway.ip_version == ip_version)
    )
    if selected is None:
        lines.append(f"  {label}: Unknown")
    elif not selected:
        lines.append(f"  {label}: None assigned")
    else:
        lines.append(f"  {label}:")
        for index, gateway in enumerate(selected, start=1):
            lines.append(f"    Gateway {index}:")
            _field(lines, 6, "Address", gateway.address)
            _field(lines, 6, "Interface", gateway.interface_name)
            _field(lines, 6, "Metric", gateway.metric)


def render_inventory(results: InventoryResults) -> str:
    """Render collected results without detecting hardware or using a terminal."""

    sections = (
        _render_system(results),
        _render_cpu(results),
        _render_memory(results),
        _render_storage(results),
        _render_gpu(results),
        _render_network(results),
    )
    return "\n\n".join("\n".join(section) for section in sections)
