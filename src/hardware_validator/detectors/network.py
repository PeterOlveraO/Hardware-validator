"""Local Linux network interface detection from psutil and sysfs."""

from collections.abc import Callable, Iterable, Mapping
import ipaddress
from pathlib import Path
import re
from socket import AF_INET, AF_INET6
import sys
from typing import Literal

import psutil

from ..collector import DetectionResult
from ..models import NetworkAddressInfo
from ..models import NetworkDuplex
from ..models import NetworkGatewayInfo
from ..models import NetworkInfo
from ..models import NetworkInterfaceInfo
from ..models import NetworkInterfaceType

NetIfAddrs = Callable[[], Mapping[str, Iterable[object]]]
NetIfStats = Callable[[], Mapping[str, object]]
TextReader = Callable[[Path], str]
PathResolver = Callable[[Path], Path]
PathExists = Callable[[Path], bool]

_MAC_ADDRESS = re.compile(
    r"[0-9a-fA-F]{2}(?P<separator>[:-])"
    r"(?:[0-9a-fA-F]{2}(?P=separator)){4}[0-9a-fA-F]{2}"
)
_MISSING = object()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _resolve_path(path: Path) -> Path:
    return path.resolve(strict=True)


def _valid_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value not in {".", ".."}
        and "/" not in value
        and "\x00" not in value
    )


def _is_virtual_identity(identity: Path | None) -> bool:
    if identity is None:
        return False
    parts = identity.parts
    return any(
        parts[index : index + 3] == ("devices", "virtual", "net")
        for index in range(len(parts) - 2)
    )


class NetworkDetector:
    """Collect local interface identity and configuration without network I/O."""

    def __init__(
        self,
        *,
        sys_net_root: Path = Path("/sys/class/net"),
        net_if_addrs: NetIfAddrs = psutil.net_if_addrs,
        net_if_stats: NetIfStats = psutil.net_if_stats,
        ipv4_route_path: Path = Path("/proc/net/route"),
        ipv6_route_path: Path = Path("/proc/net/ipv6_route"),
        resolv_conf_path: Path = Path("/etc/resolv.conf"),
        systemd_resolv_conf_path: Path = Path("/run/systemd/resolve/resolv.conf"),
        read_text: TextReader = _read_text,
        resolve_path: PathResolver = _resolve_path,
        path_exists: PathExists = Path.exists,
        byte_order: Literal["little", "big"] = sys.byteorder,
    ) -> None:
        if byte_order not in {"little", "big"}:
            raise ValueError("byte_order must be 'little' or 'big'")
        self._sys_net_root = sys_net_root
        self._net_if_addrs = net_if_addrs
        self._net_if_stats = net_if_stats
        self._ipv4_route_path = ipv4_route_path
        self._ipv6_route_path = ipv6_route_path
        self._resolv_conf_path = resolv_conf_path
        self._systemd_resolv_conf_path = systemd_resolv_conf_path
        self._read_text = read_text
        self._resolve_path = resolve_path
        self._path_exists = path_exists
        self._byte_order = byte_order

    def detect(self) -> DetectionResult[NetworkInfo]:
        """Return deterministic local interface information."""

        issues: list[str] = []
        addresses, addresses_available = self._read_mapping(
            self._net_if_addrs,
            "Unable to enumerate network addresses.",
            "Malformed network address data.",
            issues,
        )
        statistics, statistics_available = self._read_mapping(
            self._net_if_stats,
            "Unable to enumerate network statistics.",
            "Malformed network statistics data.",
            issues,
        )
        sysfs_interfaces, sysfs_available = self._list_sysfs(issues)

        names: set[str] = set(sysfs_interfaces)
        self._add_mapping_names(addresses, names, "address", issues)
        self._add_mapping_names(statistics, names, "statistics", issues)

        interfaces = tuple(
            self._make_interface(
                name,
                addresses.get(name, ()) if addresses_available else _MISSING,
                statistics.get(name, _MISSING)
                if statistics_available
                else _MISSING,
                sysfs_interfaces.get(name),
                name in sysfs_interfaces,
                issues,
            )
            for name in sorted(
                names,
                key=lambda item: (
                    str(sysfs_interfaces[item])
                    if sysfs_interfaces.get(item) is not None
                    else f"~{item}",
                    item,
                ),
            )
        )
        interface_names = {interface.name for interface in interfaces}
        ipv4_default_gateways = self._read_ipv4_routes(interface_names, issues)
        ipv6_default_gateways = self._read_ipv6_routes(interface_names, issues)
        configured_dns = self._read_dns(
            self._resolv_conf_path,
            "Configured DNS is unavailable.",
            False,
            issues,
        )
        upstream_dns = self._read_dns(
            self._systemd_resolv_conf_path,
            "Unable to read upstream DNS configuration.",
            True,
            issues,
        )
        value = NetworkInfo(
            interfaces=interfaces,
            ipv4_default_gateways=ipv4_default_gateways,
            ipv6_default_gateways=ipv6_default_gateways,
            configured_dns=configured_dns,
            upstream_dns=upstream_dns,
        )
        unique_issues = tuple(dict.fromkeys(issues))
        if unique_issues:
            if (
                bool(interfaces)
                or addresses_available
                or statistics_available
                or sysfs_available
                or ipv4_default_gateways is not None
                or ipv6_default_gateways is not None
                or configured_dns is not None
                or upstream_dns is not None
            ):
                return DetectionResult.partial(value, *unique_issues)
            return DetectionResult.unavailable(value, *unique_issues)
        return DetectionResult.complete(value)

    @staticmethod
    def _read_mapping(
        reader: Callable[[], object],
        unavailable_issue: str,
        malformed_issue: str,
        issues: list[str],
    ) -> tuple[Mapping[object, object], bool]:
        try:
            value = reader()
        except (OSError, NotImplementedError, psutil.Error):
            issues.append(unavailable_issue)
            return {}, False
        if not isinstance(value, Mapping):
            issues.append(malformed_issue)
            return {}, False
        return value, True

    @staticmethod
    def _add_mapping_names(
        mapping: Mapping[object, object],
        names: set[str],
        label: str,
        issues: list[str],
    ) -> None:
        for name in mapping:
            if _valid_name(name):
                names.add(name)
            else:
                issues.append(f"Malformed network {label} data.")

    def _list_sysfs(
        self, issues: list[str]
    ) -> tuple[dict[str, Path | None], bool]:
        try:
            entries = tuple(self._sys_net_root.iterdir())
        except FileNotFoundError:
            issues.append("Network sysfs is unavailable.")
            return {}, False
        except OSError:
            issues.append("Unable to enumerate network sysfs.")
            return {}, False

        interfaces: dict[str, Path | None] = {}
        for entry in entries:
            if _valid_name(entry.name):
                interfaces[entry.name] = self._resolve_identity(
                    entry, entry.name, issues
                )
            else:
                issues.append("Malformed network sysfs entry.")
        return interfaces, True

    def _make_interface(
        self,
        name: str,
        raw_addresses: object,
        raw_statistics: object,
        identity: Path | None,
        in_sysfs: bool,
        issues: list[str],
    ) -> NetworkInterfaceInfo:
        interface_type = "unknown"
        if in_sysfs:
            interface_type = self._classify(name, identity, issues)
        mac_address, ipv4_addresses, ipv6_addresses = self._parse_addresses(
            name, raw_addresses, issues
        )
        is_up, speed_mbps, mtu, duplex = self._parse_statistics(
            name, raw_statistics, issues
        )
        return NetworkInterfaceInfo(
            name=name,
            interface_type=interface_type,
            is_up=is_up,
            mac_address=mac_address,
            ipv4_addresses=ipv4_addresses,
            ipv6_addresses=ipv6_addresses,
            speed_mbps=speed_mbps,
            mtu=mtu,
            duplex=duplex,
        )

    def _classify(
        self, name: str, identity: Path | None, issues: list[str]
    ) -> NetworkInterfaceType:
        path = self._sys_net_root / name
        hardware_type = self._read_hardware_type(path / "type", name, issues)
        if hardware_type == 772:
            return "loopback"
        if _is_virtual_identity(identity):
            return "virtual"
        wireless = self._exists(path / "wireless", name, issues)
        phy80211 = self._exists(path / "phy80211", name, issues)
        if wireless is True or phy80211 is True:
            return (
                "wifi"
                if self._exists(path / "device", name, issues) is True
                else "unknown"
            )
        if wireless is None or phy80211 is None:
            return "unknown"
        if (
            hardware_type == 1
            and identity is not None
            and self._exists(path / "device", name, issues) is True
        ):
            return "ethernet"
        return "unknown"

    def _read_hardware_type(
        self, path: Path, name: str, issues: list[str]
    ) -> int | None:
        try:
            text = self._read_text(path)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError):
            issues.append(f"Unable to read network type for {name}.")
            return None
        try:
            value = int(text.strip())
        except (AttributeError, ValueError):
            issues.append(f"Malformed network type for {name}.")
            return None
        if value < 0:
            issues.append(f"Malformed network type for {name}.")
            return None
        return value

    def _resolve_identity(
        self, path: Path, name: str, issues: list[str]
    ) -> Path | None:
        try:
            return self._resolve_path(path)
        except FileNotFoundError:
            return None
        except (OSError, RuntimeError):
            issues.append(f"Unable to resolve network interface {name}.")
            return None

    def _exists(self, path: Path, name: str, issues: list[str]) -> bool | None:
        try:
            return bool(self._path_exists(path))
        except OSError:
            issues.append(f"Unable to inspect network interface {name}.")
            return None

    @staticmethod
    def _parse_addresses(
        name: str, raw_addresses: object, issues: list[str]
    ) -> tuple[
        str | None,
        tuple[NetworkAddressInfo, ...] | None,
        tuple[NetworkAddressInfo, ...] | None,
    ]:
        if raw_addresses is _MISSING:
            return None, None, None
        if raw_addresses is None:
            issues.append(f"Malformed network address data for {name}.")
            return None, None, None
        if isinstance(raw_addresses, (str, bytes)):
            issues.append(f"Malformed network address data for {name}.")
            return None, None, None
        try:
            entries = tuple(raw_addresses)  # type: ignore[arg-type]
        except TypeError:
            issues.append(f"Malformed network address data for {name}.")
            return None, None, None

        mac_address = None
        ipv4_addresses: dict[str, NetworkAddressInfo] = {}
        ipv6_addresses: dict[str, NetworkAddressInfo] = {}
        ipv4_conflicts: set[str] = set()
        ipv6_conflicts: set[str] = set()
        ipv4_malformed = False
        ipv6_malformed = False
        for entry in entries:
            family = getattr(entry, "family", _MISSING)
            if family is _MISSING:
                issues.append(f"Malformed network address for {name}.")
                ipv4_malformed = True
                ipv6_malformed = True
                continue
            address = getattr(entry, "address", None)
            if family == psutil.AF_LINK:
                if (
                    not isinstance(address, str)
                    or _MAC_ADDRESS.fullmatch(address) is None
                ):
                    issues.append(f"Malformed network address for {name}.")
                elif mac_address is None:
                    mac_address = address
            elif family == AF_INET:
                if not NetworkDetector._valid_ip(address, 4):
                    issues.append(f"Malformed network address for {name}.")
                    ipv4_malformed = True
                else:
                    information, malformed = NetworkDetector._address_information(
                        address, getattr(entry, "netmask", None), 4
                    )
                    if malformed:
                        issues.append(f"Malformed network address for {name}.")
                    NetworkDetector._merge_address(
                        ipv4_addresses,
                        ipv4_conflicts,
                        information,
                        name,
                        issues,
                    )
            elif family == AF_INET6:
                if not NetworkDetector._valid_ip(address, 6):
                    issues.append(f"Malformed network address for {name}.")
                    ipv6_malformed = True
                else:
                    information, malformed = NetworkDetector._address_information(
                        address, getattr(entry, "netmask", None), 6
                    )
                    if malformed:
                        issues.append(f"Malformed network address for {name}.")
                    NetworkDetector._merge_address(
                        ipv6_addresses,
                        ipv6_conflicts,
                        information,
                        name,
                        issues,
                    )
        return (
            mac_address,
            (
                tuple(ipv4_addresses[item] for item in sorted(ipv4_addresses))
                if ipv4_addresses or not ipv4_malformed
                else None
            ),
            (
                tuple(ipv6_addresses[item] for item in sorted(ipv6_addresses))
                if ipv6_addresses or not ipv6_malformed
                else None
            ),
        )

    @staticmethod
    def _address_information(
        address: str, netmask: object, version: int
    ) -> tuple[NetworkAddressInfo, bool]:
        if netmask is None or netmask == "":
            return NetworkAddressInfo(address), False
        if not isinstance(netmask, str):
            return NetworkAddressInfo(address), True
        try:
            parsed_mask = ipaddress.ip_address(netmask)
        except ValueError:
            return NetworkAddressInfo(address), True
        if parsed_mask.version != version or getattr(parsed_mask, "scope_id", None):
            return NetworkAddressInfo(address), True

        width = 32 if version == 4 else 128
        mask_value = int(parsed_mask)
        prefix_length = mask_value.bit_count()
        expected = ((1 << prefix_length) - 1) << (width - prefix_length)
        if mask_value != expected:
            return NetworkAddressInfo(address), True
        return (
            NetworkAddressInfo(
                address=address,
                prefix_length=prefix_length,
                subnet_mask=str(parsed_mask),
            ),
            False,
        )

    @staticmethod
    def _merge_address(
        addresses: dict[str, NetworkAddressInfo],
        conflicts: set[str],
        information: NetworkAddressInfo,
        name: str,
        issues: list[str],
    ) -> None:
        address = information.address
        if address in conflicts:
            return
        previous = addresses.get(address)
        if previous is None:
            addresses[address] = information
            return
        previous_mask = (previous.prefix_length, previous.subnet_mask)
        current_mask = (information.prefix_length, information.subnet_mask)
        if previous_mask == (None, None):
            addresses[address] = information
        elif current_mask != (None, None) and current_mask != previous_mask:
            addresses[address] = NetworkAddressInfo(address)
            conflicts.add(address)
            issues.append(f"Malformed network address for {name}.")

    @staticmethod
    def _valid_ip(address: object, version: int) -> bool:
        if not isinstance(address, str) or not address:
            return False
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return False
        return parsed.version == version

    def _read_ipv4_routes(
        self, interface_names: set[str], issues: list[str]
    ) -> tuple[NetworkGatewayInfo, ...] | None:
        try:
            text = self._read_text(self._ipv4_route_path)
        except FileNotFoundError:
            issues.append("IPv4 routing information is unavailable.")
            return None
        except (OSError, UnicodeError):
            issues.append("Unable to read IPv4 routing information.")
            return None
        if not isinstance(text, str):
            issues.append("Malformed IPv4 routing information.")
            return None

        lines = [line.split() for line in text.splitlines() if line.strip()]
        if not lines:
            issues.append("Malformed IPv4 routing information.")
            return None
        header = lines[0]
        expected_header = (
            "Iface",
            "Destination",
            "Gateway",
            "Flags",
            "RefCnt",
            "Use",
            "Metric",
            "Mask",
            "MTU",
            "Window",
            "IRTT",
        )
        if tuple(header) != expected_header:
            issues.append("Malformed IPv4 routing information.")
            return None
        indexes = {item: index for index, item in enumerate(header)}
        routes: list[tuple[NetworkGatewayInfo, str]] = []
        malformed = False
        for fields in lines[1:]:
            destination = self._route_field(fields, indexes["Destination"])
            if destination is None or not self._is_hex(destination, 8):
                issues.append("Malformed IPv4 default route.")
                malformed = True
                continue
            if destination != "00000000":
                continue
            mask = self._route_field(fields, indexes["Mask"])
            if mask is None or not self._is_hex(mask, 8):
                issues.append("Malformed IPv4 default route.")
                malformed = True
                continue
            if mask != "00000000":
                continue

            gateway = self._route_field(fields, indexes["Gateway"])
            flags_text = self._route_field(fields, indexes["Flags"])
            metric_text = self._route_field(fields, indexes["Metric"])
            interface = self._route_field(fields, indexes["Iface"])
            if len(fields) != len(header) or any(
                re.fullmatch(r"[0-9]+", fields[indexes[name]]) is None
                for name in ("RefCnt", "Use", "MTU", "Window", "IRTT")
            ):
                issues.append("Malformed IPv4 default route.")
                malformed = True
                continue
            if flags_text is None or not self._is_hex(flags_text, 4):
                issues.append("Malformed IPv4 default route.")
                malformed = True
                continue
            flags = int(flags_text, 16)
            if flags & 0x3 != 0x3 or flags & 0x200:
                continue
            if gateway is None or not self._is_hex(gateway, 8) or not _valid_name(
                interface
            ):
                issues.append("Malformed IPv4 default route.")
                malformed = True
                continue
            try:
                gateway_bytes = bytes.fromhex(gateway)
                if self._byte_order == "little":
                    gateway_bytes = gateway_bytes[::-1]
                address = str(ipaddress.IPv4Address(gateway_bytes))
            except (ValueError, OverflowError):
                issues.append("Malformed IPv4 default route.")
                malformed = True
                continue
            if address == "0.0.0.0":
                issues.append("Malformed IPv4 default route.")
                malformed = True
                continue
            if metric_text is None or re.fullmatch(r"[0-9]+", metric_text) is None:
                metric = None
                issues.append("Malformed IPv4 default route.")
                malformed = True
            else:
                metric = int(metric_text, 10)
            associated_interface = interface if interface in interface_names else None
            if associated_interface is None:
                issues.append(f"IPv4 default route interface {interface} is unknown.")
            routes.append(
                (
                    NetworkGatewayInfo(
                        address=address,
                        ip_version=4,
                        interface_name=associated_interface,
                        metric=metric,
                    ),
                    interface,
                )
            )
        routes.sort(
            key=lambda item: (
                item[0].metric is None,
                item[0].metric if item[0].metric is not None else 0,
                item[1],
                item[0].address,
            )
        )
        if malformed and not routes:
            return None
        return tuple(item[0] for item in routes)

    def _read_ipv6_routes(
        self, interface_names: set[str], issues: list[str]
    ) -> tuple[NetworkGatewayInfo, ...] | None:
        try:
            text = self._read_text(self._ipv6_route_path)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError):
            issues.append("Unable to read IPv6 routing information.")
            return None
        if not isinstance(text, str):
            issues.append("Malformed IPv6 routing information.")
            return None

        routes: list[tuple[NetworkGatewayInfo, str]] = []
        malformed = False
        for line in text.splitlines():
            if not line.strip():
                continue
            fields = line.split()
            if len(fields) < 2:
                issues.append("Malformed IPv6 default route.")
                malformed = True
                continue
            destination, destination_prefix = fields[:2]
            if not self._is_hex(destination, 32) or not self._is_hex(
                destination_prefix, 2
            ):
                issues.append("Malformed IPv6 default route.")
                malformed = True
                continue
            destination_prefix_value = int(destination_prefix, 16)
            if destination_prefix_value > 128:
                issues.append("Malformed IPv6 default route.")
                malformed = True
                continue
            if destination != "0" * 32:
                continue
            if destination_prefix_value != 0:
                continue
            if len(fields) != 10:
                issues.append("Malformed IPv6 default route.")
                malformed = True
                continue
            source, source_prefix = fields[2:4]
            if not self._is_hex(source, 32) or not self._is_hex(source_prefix, 2):
                issues.append("Malformed IPv6 default route.")
                malformed = True
                continue
            source_prefix_value = int(source_prefix, 16)
            if source_prefix_value > 128:
                issues.append("Malformed IPv6 default route.")
                malformed = True
                continue
            if source != "0" * 32:
                continue
            if source_prefix_value != 0:
                continue

            next_hop, metric_text, ref_count, use_count, flags_text, interface = (
                fields[4],
                fields[5],
                fields[6],
                fields[7],
                fields[8],
                fields[9],
            )
            if any(
                not self._is_hex(value, length)
                for value, length in (
                    (next_hop, 32),
                    (metric_text, 8),
                    (ref_count, 8),
                    (use_count, 8),
                    (flags_text, 8),
                )
            ):
                issues.append("Malformed IPv6 default route.")
                malformed = True
                continue
            flags = int(flags_text, 16)
            if flags & 0x3 != 0x3 or flags & 0x200:
                continue
            if (
                next_hop == "0" * 32
                or not _valid_name(interface)
            ):
                issues.append("Malformed IPv6 default route.")
                malformed = True
                continue
            try:
                address = str(ipaddress.IPv6Address(int(next_hop, 16)))
            except (ValueError, OverflowError):
                issues.append("Malformed IPv6 default route.")
                malformed = True
                continue
            metric = int(metric_text, 16)
            associated_interface = interface if interface in interface_names else None
            if associated_interface is None:
                issues.append(f"IPv6 default route interface {interface} is unknown.")
            routes.append(
                (
                    NetworkGatewayInfo(
                        address=address,
                        ip_version=6,
                        interface_name=associated_interface,
                        metric=metric,
                    ),
                    interface,
                )
            )
        routes.sort(
            key=lambda item: (
                item[0].metric is None,
                item[0].metric if item[0].metric is not None else 0,
                item[1],
                item[0].address,
            )
        )
        if malformed and not routes:
            return None
        return tuple(item[0] for item in routes)

    def _read_dns(
        self,
        path: Path,
        unavailable_issue: str,
        optional: bool,
        issues: list[str],
    ) -> tuple[str, ...] | None:
        try:
            text = self._read_text(path)
        except FileNotFoundError:
            if not optional:
                issues.append(unavailable_issue)
            return None
        except (OSError, UnicodeError):
            issues.append(unavailable_issue)
            return None
        if not isinstance(text, str):
            issues.append(
                "Malformed upstream DNS configuration."
                if optional
                else "Malformed DNS configuration."
            )
            return None

        servers: list[str] = []
        malformed = False
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", ";")):
                continue
            fields = stripped.split()
            if fields[0].lower() != "nameserver":
                continue
            if len(fields) != 2 or not (
                self._valid_ip(fields[1], 4) or self._valid_ip(fields[1], 6)
            ):
                malformed = True
                continue
            if fields[1] not in servers:
                servers.append(fields[1])
        if malformed:
            issues.append(
                "Malformed upstream DNS configuration."
                if optional
                else "Malformed DNS configuration."
            )
            if not servers:
                return None
        return tuple(servers)

    @staticmethod
    def _route_field(fields: list[str], index: int) -> str | None:
        return fields[index] if index < len(fields) else None

    @staticmethod
    def _is_hex(value: str, length: int | None = None) -> bool:
        return (length is None or len(value) == length) and bool(value) and all(
            character in "0123456789abcdefABCDEF" for character in value
        )

    @staticmethod
    def _parse_statistics(
        name: str, raw_statistics: object, issues: list[str]
    ) -> tuple[bool | None, int | None, int | None, NetworkDuplex | None]:
        if raw_statistics is _MISSING:
            return None, None, None, None
        if raw_statistics is None:
            issues.append(f"Malformed network statistics for {name}.")
            return None, None, None, None

        malformed = False
        raw_is_up = getattr(raw_statistics, "isup", _MISSING)
        if raw_is_up is _MISSING:
            is_up = None
            malformed = True
        elif raw_is_up is None:
            is_up = None
        elif type(raw_is_up) is bool:
            is_up = raw_is_up
        else:
            is_up = None
            malformed = True

        raw_speed = getattr(raw_statistics, "speed", _MISSING)
        if raw_speed is _MISSING:
            speed_mbps = None
            malformed = True
        elif raw_speed is None:
            speed_mbps = None
        elif type(raw_speed) is int:
            if raw_speed > 0:
                speed_mbps = raw_speed
            else:
                speed_mbps = None
                if raw_speed < 0:
                    malformed = True
        else:
            speed_mbps = None
            malformed = True

        raw_mtu = getattr(raw_statistics, "mtu", _MISSING)
        if raw_mtu is _MISSING:
            mtu = None
            malformed = True
        elif raw_mtu is None:
            mtu = None
        elif type(raw_mtu) is int and raw_mtu > 0:
            mtu = raw_mtu
        else:
            mtu = None
            malformed = True

        raw_duplex = getattr(raw_statistics, "duplex", _MISSING)
        duplex: NetworkDuplex | None
        if type(raw_duplex) is bool:
            duplex = None
            malformed = True
        elif raw_duplex == psutil.NIC_DUPLEX_FULL:
            duplex = "full"
        elif raw_duplex == psutil.NIC_DUPLEX_HALF:
            duplex = "half"
        elif raw_duplex is _MISSING:
            duplex = None
            malformed = True
        elif raw_duplex is None or raw_duplex == psutil.NIC_DUPLEX_UNKNOWN:
            duplex = None
        else:
            duplex = None
            malformed = True

        if malformed:
            issues.append(f"Malformed network statistics for {name}.")
        return is_up, speed_mbps, mtu, duplex
