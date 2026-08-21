"""Tests for local Linux network interface detection."""

import tempfile
import unittest
from pathlib import Path
from socket import AF_INET, AF_INET6
from types import SimpleNamespace

import psutil

from hardware_validator.collector import DetectionStatus
from hardware_validator.detectors.network import NetworkDetector
from hardware_validator.models import NetworkAddressInfo
from hardware_validator.models import NetworkGatewayInfo


def address(family: object, value: object, netmask: object = None) -> object:
    return SimpleNamespace(family=family, address=value, netmask=netmask)


def stats(
    *,
    is_up: object,
    speed: object = 0,
    mtu: object = 1500,
    duplex: object = psutil.NIC_DUPLEX_UNKNOWN,
) -> object:
    return SimpleNamespace(isup=is_up, speed=speed, mtu=mtu, duplex=duplex)


class NetworkFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.class_root = root / "sys/class/net"
        self.class_root.mkdir(parents=True)
        self.ipv4_route_path = root / "proc/net/route"
        self.ipv4_route_path.parent.mkdir(parents=True)
        self.write_ipv4_routes()
        self.ipv6_route_path = root / "proc/net/ipv6_route"
        self.resolv_conf_path = root / "etc/resolv.conf"
        self.resolv_conf_path.parent.mkdir(parents=True)
        self.resolv_conf_path.write_text("", encoding="utf-8")
        self.systemd_resolv_conf_path = root / "run/systemd/resolve/resolv.conf"

    @property
    def source_paths(self) -> dict[str, Path]:
        return {
            "ipv4_route_path": self.ipv4_route_path,
            "ipv6_route_path": self.ipv6_route_path,
            "resolv_conf_path": self.resolv_conf_path,
            "systemd_resolv_conf_path": self.systemd_resolv_conf_path,
        }

    def write_ipv4_routes(self, *rows: str) -> None:
        header = "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT"
        self.ipv4_route_path.write_text(
            "\n".join((header, *rows)) + "\n", encoding="utf-8"
        )

    def write_ipv6_routes(self, *rows: str) -> None:
        self.ipv6_route_path.write_text("\n".join(rows), encoding="utf-8")

    def add_interface(
        self,
        name: str,
        *,
        hardware_type: str | None = "1",
        virtual: bool = False,
        backing_device: bool = True,
        wireless: bool = False,
        phy80211: bool = False,
    ) -> Path:
        if virtual:
            target = self.root / "sys/devices/virtual/net" / name
        else:
            target = self.root / "sys/devices/platform" / name / "net" / name
        target.mkdir(parents=True)
        (self.class_root / name).symlink_to(target, target_is_directory=True)
        if hardware_type is not None:
            (target / "type").write_text(hardware_type, encoding="utf-8")
        if backing_device:
            (target / "device").mkdir()
        if wireless:
            (target / "wireless").mkdir()
        if phy80211:
            (target / "phy80211").mkdir()
        return target

    def detector(
        self,
        *,
        addresses: object = None,
        statistics: object = None,
        **kwargs: object,
    ) -> NetworkDetector:
        address_value = {} if addresses is None else addresses
        statistics_value = {} if statistics is None else statistics
        options: dict[str, object] = {
            "sys_net_root": self.class_root,
            "net_if_addrs": lambda: address_value,
            "net_if_stats": lambda: statistics_value,
            **self.source_paths,
        }
        options.update(kwargs)
        return NetworkDetector(
            **options,  # type: ignore[arg-type]
        )


class NetworkDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.fixture = NetworkFixture(Path(self.temporary_directory.name))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_connected_ethernet_collects_addresses_and_positive_stats(self) -> None:
        self.fixture.add_interface("eno9")
        result = self.fixture.detector(
            addresses={
                "eno9": (
                    address(psutil.AF_LINK, "02:00:00:00:00:09"),
                    address(AF_INET, "192.0.2.9"),
                )
            },
            statistics={
                "eno9": stats(
                    is_up=True,
                    speed=1000,
                    mtu=9000,
                    duplex=psutil.NIC_DUPLEX_FULL,
                )
            },
        ).detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        interface = result.value.interfaces[0]
        self.assertEqual(interface.interface_type, "ethernet")
        self.assertTrue(interface.is_up)
        self.assertEqual(interface.mac_address, "02:00:00:00:00:09")
        self.assertEqual(
            interface.ipv4_addresses, (NetworkAddressInfo("192.0.2.9"),)
        )
        self.assertEqual(interface.speed_mbps, 1000)
        self.assertEqual(interface.mtu, 9000)
        self.assertEqual(interface.duplex, "full")

    def test_disconnected_ethernet_can_have_no_mac_ipv4_or_known_speed(self) -> None:
        self.fixture.add_interface("offline")
        result = self.fixture.detector(
            statistics={
                "offline": stats(
                    is_up=False,
                    speed=0,
                    duplex=psutil.NIC_DUPLEX_HALF,
                )
            }
        ).detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        interface = result.value.interfaces[0]
        self.assertEqual(interface.interface_type, "ethernet")
        self.assertFalse(interface.is_up)
        self.assertIsNone(interface.mac_address)
        self.assertEqual(interface.ipv4_addresses, ())
        self.assertIsNone(interface.speed_mbps)
        self.assertEqual(interface.duplex, "half")

    def test_connected_and_disconnected_wifi_use_sysfs_evidence(self) -> None:
        self.fixture.add_interface("radio-a", wireless=True)
        self.fixture.add_interface("radio-b", phy80211=True)
        result = self.fixture.detector(
            statistics={
                "radio-a": stats(is_up=True, speed=866),
                "radio-b": stats(is_up=False, speed=0),
            }
        ).detect()

        first, second = result.value.interfaces
        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(
            (first.interface_type, second.interface_type), ("wifi", "wifi")
        )
        self.assertEqual((first.is_up, second.is_up), (True, False))
        self.assertEqual((first.speed_mbps, second.speed_mbps), (866, None))

    def test_multiple_addresses_are_validated_deduplicated_and_sorted(self) -> None:
        self.fixture.add_interface("multi")
        result = self.fixture.detector(
            addresses={
                "multi": (
                    address(AF_INET6, "fe80::2%multi"),
                    address(AF_INET, "198.51.100.20"),
                    address(psutil.AF_LINK, "not-a-mac"),
                    address(psutil.AF_LINK, "02:00:00:00:00:20"),
                    address(AF_INET6, "2001:db8::2"),
                    address(AF_INET, "192.0.2.20"),
                    address(AF_INET, "198.51.100.20"),
                    address(AF_INET6, "fe80::2%multi"),
                    address(AF_INET, "bad-ip"),
                )
            }
        ).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        interface = result.value.interfaces[0]
        self.assertEqual(interface.mac_address, "02:00:00:00:00:20")
        self.assertEqual(
            interface.ipv4_addresses,
            (
                NetworkAddressInfo("192.0.2.20"),
                NetworkAddressInfo("198.51.100.20"),
            ),
        )
        self.assertEqual(
            interface.ipv6_addresses,
            (
                NetworkAddressInfo("2001:db8::2"),
                NetworkAddressInfo("fe80::2%multi"),
            ),
        )
        self.assertEqual(
            result.issues, ("Malformed network address for multi.",)
        )

    def test_ipv4_masks_present_absent_and_malformed(self) -> None:
        self.fixture.add_interface("masked")
        result = self.fixture.detector(
            addresses={
                "masked": (
                    address(AF_INET, "192.0.2.3", "255.255.255.0"),
                    address(AF_INET, "192.0.2.1"),
                    address(AF_INET, "192.0.2.2", "255.0.255.0"),
                )
            }
        ).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertEqual(
            result.value.interfaces[0].ipv4_addresses,
            (
                NetworkAddressInfo("192.0.2.1"),
                NetworkAddressInfo("192.0.2.2"),
                NetworkAddressInfo("192.0.2.3", 24, "255.255.255.0"),
            ),
        )

    def test_ipv6_mask_is_canonical_and_scope_is_preserved(self) -> None:
        self.fixture.add_interface("scoped")
        result = self.fixture.detector(
            addresses={
                "scoped": (
                    address(
                        AF_INET6,
                        "fe80::10%scoped",
                        "ffff:ffff:ffff:ffff::",
                    ),
                )
            }
        ).detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(
            result.value.interfaces[0].ipv6_addresses,
            (
                NetworkAddressInfo(
                    "fe80::10%scoped", 64, "ffff:ffff:ffff:ffff::"
                ),
            ),
        )

    def test_duplicate_address_masks_merge_or_become_unknown_on_conflict(self) -> None:
        self.fixture.add_interface("duplicate")
        result = self.fixture.detector(
            addresses={
                "duplicate": (
                    address(AF_INET, "192.0.2.1"),
                    address(AF_INET, "192.0.2.1", "255.255.255.0"),
                    address(AF_INET, "192.0.2.1", "255.255.0.0"),
                )
            }
        ).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertEqual(
            result.value.interfaces[0].ipv4_addresses,
            (NetworkAddressInfo("192.0.2.1"),),
        )

    def test_explicit_null_address_record_is_partial_and_unknown(self) -> None:
        self.fixture.add_interface("broken")

        result = self.fixture.detector(addresses={"broken": None}).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        interface = result.value.interfaces[0]
        self.assertIsNone(interface.ipv4_addresses)
        self.assertIsNone(interface.ipv6_addresses)
        self.assertIn("Malformed network address data for broken.", result.issues)

    def test_unusable_address_record_keeps_both_families_unknown(self) -> None:
        self.fixture.add_interface("broken")

        result = self.fixture.detector(addresses={"broken": "invalid"}).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        interface = result.value.interfaces[0]
        self.assertIsNone(interface.ipv4_addresses)
        self.assertIsNone(interface.ipv6_addresses)

    def test_malformed_address_keeps_only_its_family_unknown(self) -> None:
        self.fixture.add_interface("broken")
        result = self.fixture.detector(
            addresses={
                "broken": (
                    address(AF_INET, "invalid"),
                    address(AF_INET6, "2001:db8::1"),
                )
            }
        ).detect()

        interface = result.value.interfaces[0]
        self.assertIsNone(interface.ipv4_addresses)
        self.assertEqual(
            interface.ipv6_addresses,
            (NetworkAddressInfo("2001:db8::1"),),
        )

    def test_loopback_requires_arphrd_loopback_and_has_precedence(self) -> None:
        self.fixture.add_interface(
            "local",
            hardware_type="772",
            virtual=True,
            backing_device=False,
            wireless=True,
        )

        interface = self.fixture.detector().detect().value.interfaces[0]

        self.assertEqual(interface.interface_type, "loopback")

    def test_virtual_identity_precedes_wireless_marker(self) -> None:
        self.fixture.add_interface(
            "radio",
            hardware_type="1",
            virtual=True,
            backing_device=False,
            wireless=True,
        )

        interface = self.fixture.detector().detect().value.interfaces[0]

        self.assertEqual(interface.interface_type, "virtual")

    def test_canonical_virtual_identity_precedes_ethernet(self) -> None:
        self.fixture.add_interface(
            "guest-link", hardware_type="1", virtual=True, backing_device=True
        )

        interface = self.fixture.detector().detect().value.interfaces[0]

        self.assertEqual(interface.interface_type, "virtual")

    def test_unknown_and_no_name_prefix_inference(self) -> None:
        self.fixture.add_interface("eth0", hardware_type="999")
        self.fixture.add_interface("wlan0", hardware_type="1")
        self.fixture.add_interface("lanish", hardware_type="1", backing_device=False)

        interfaces = {
            item.name: item.interface_type
            for item in self.fixture.detector().detect().value.interfaces
        }

        self.assertEqual(interfaces["eth0"], "unknown")
        self.assertEqual(interfaces["wlan0"], "ethernet")
        self.assertEqual(interfaces["lanish"], "unknown")

    def test_union_and_interface_order_are_deterministic(self) -> None:
        self.fixture.add_interface("middle", hardware_type="772")
        result = self.fixture.detector(
            addresses={"z-last": ()},
            statistics={"a-first": stats(is_up=False)},
        ).detect()

        self.assertEqual(
            tuple(item.name for item in result.value.interfaces),
            ("middle", "a-first", "z-last"),
        )
        self.assertEqual(result.value.interfaces[1].interface_type, "unknown")
        self.assertEqual(
            result.value.interfaces,
            self.fixture.detector(
                addresses={"z-last": ()},
                statistics={"a-first": stats(is_up=False)},
            ).detect().value.interfaces,
        )

    def test_source_permission_failure_with_independent_data_is_partial(self) -> None:
        self.fixture.add_interface("visible", hardware_type="772")

        def denied_addresses() -> object:
            raise PermissionError("fixture")

        result = NetworkDetector(
            sys_net_root=self.fixture.class_root,
            net_if_addrs=denied_addresses,
            net_if_stats=lambda: {"visible": stats(is_up=True)},
            **self.fixture.source_paths,
        ).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertEqual(result.value.interfaces[0].name, "visible")
        self.assertEqual(
            result.issues, ("Unable to enumerate network addresses.",)
        )

    def test_missing_sysfs_with_psutil_data_is_partial(self) -> None:
        missing = self.fixture.root / "missing-net"
        result = NetworkDetector(
            sys_net_root=missing,
            net_if_addrs=lambda: {"known": (address(AF_INET, "203.0.113.1"),)},
            net_if_stats=lambda: {},
            **self.fixture.source_paths,
        ).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertEqual(result.value.interfaces[0].interface_type, "unknown")
        self.assertEqual(result.issues, ("Network sysfs is unavailable.",))

    def test_permission_reading_selected_sysfs_value_is_partial(self) -> None:
        target = self.fixture.add_interface("restricted")

        def restricted_reader(path: Path) -> str:
            if path.resolve() == target / "type":
                raise PermissionError("fixture")
            return path.read_text(encoding="utf-8")

        result = self.fixture.detector(read_text=restricted_reader).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertEqual(result.value.interfaces[0].interface_type, "unknown")
        self.assertEqual(
            result.issues, ("Unable to read network type for restricted.",)
        )

    def test_unresolved_identity_does_not_guess_ethernet(self) -> None:
        self.fixture.add_interface("restricted")

        def denied_resolver(_path: Path) -> Path:
            raise PermissionError("fixture")

        result = self.fixture.detector(resolve_path=denied_resolver).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertEqual(result.value.interfaces[0].interface_type, "unknown")
        self.assertEqual(
            result.issues,
            ("Unable to resolve network interface restricted.",),
        )

    def test_unreadable_wireless_evidence_does_not_guess_ethernet(self) -> None:
        target = self.fixture.add_interface("restricted")

        def restricted_exists(path: Path) -> bool:
            if path.resolve() == target / "wireless":
                raise PermissionError("fixture")
            return path.exists()

        result = self.fixture.detector(path_exists=restricted_exists).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertEqual(result.value.interfaces[0].interface_type, "unknown")

    def test_unhashable_duplex_is_malformed_not_fatal(self) -> None:
        self.fixture.add_interface("broken")
        result = self.fixture.detector(
            statistics={"broken": stats(is_up=True, duplex=[])}
        ).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertIsNone(result.value.interfaces[0].duplex)
        self.assertIn("Malformed network statistics for broken.", result.issues)

    def test_malformed_selected_values_are_partial_and_skipped(self) -> None:
        self.fixture.add_interface("broken", hardware_type="not-a-number")
        result = self.fixture.detector(
            statistics={
                "broken": stats(
                    is_up=1,
                    speed=-10,
                    mtu=0,
                    duplex=999,
                )
            }
        ).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        interface = result.value.interfaces[0]
        self.assertEqual(interface.interface_type, "unknown")
        self.assertIsNone(interface.is_up)
        self.assertIsNone(interface.speed_mbps)
        self.assertIsNone(interface.mtu)
        self.assertIsNone(interface.duplex)
        self.assertEqual(
            result.issues,
            (
                "Malformed network type for broken.",
                "Malformed network statistics for broken.",
            ),
        )

    def test_all_failed_sources_without_useful_data_are_unavailable(self) -> None:
        def failed() -> object:
            raise OSError("fixture")

        self.fixture.ipv4_route_path.unlink()
        self.fixture.resolv_conf_path.unlink()
        result = NetworkDetector(
            sys_net_root=self.fixture.root / "missing",
            net_if_addrs=failed,
            net_if_stats=failed,
            **self.fixture.source_paths,
        ).detect()

        self.assertIs(result.status, DetectionStatus.UNAVAILABLE)
        self.assertEqual(result.value.interfaces, ())
        self.assertEqual(
            result.issues,
            (
                "Unable to enumerate network addresses.",
                "Unable to enumerate network statistics.",
                "Network sysfs is unavailable.",
                "IPv4 routing information is unavailable.",
                "Configured DNS is unavailable.",
            ),
        )

    def test_all_readable_empty_sources_are_complete(self) -> None:
        result = self.fixture.detector().detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(result.value.interfaces, ())
        self.assertEqual(result.issues, ())

    def test_valid_ipv4_default_gateway_is_collected(self) -> None:
        self.fixture.add_interface("eno1")
        self.fixture.write_ipv4_routes(
            "eno1 00000000 010200C0 0003 0 0 100 00000000 0 0 0"
        )

        result = self.fixture.detector().detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(
            result.value.ipv4_default_gateways,
            (NetworkGatewayInfo("192.0.2.1", 4, "eno1", 100),),
        )

    def test_ipv4_defaults_are_sorted_and_non_defaults_are_not_retained(self) -> None:
        self.fixture.add_interface("eno1")
        self.fixture.add_interface("eno2")
        self.fixture.write_ipv4_routes(
            "eno1 00000000 010200C0 0003 0 0 200 00000000 0 0 0",
            "eno1 000200C0 00000000 0001 0 0 1 00FFFFFF 0 0 0",
            "eno2 00000000 0100000A 0003 0 0 10 00000000 0 0 0",
            "eno1 00000000 FE0100CB 0003 0 0 10 00000000 0 0 0",
        )

        result = self.fixture.detector().detect()

        self.assertEqual(
            result.value.ipv4_default_gateways,
            (
                NetworkGatewayInfo("203.0.1.254", 4, "eno1", 10),
                NetworkGatewayInfo("10.0.0.1", 4, "eno2", 10),
                NetworkGatewayInfo("192.0.2.1", 4, "eno1", 200),
            ),
        )

    def test_malformed_ipv4_default_route_marks_result_partial(self) -> None:
        self.fixture.add_interface("eno1")
        self.fixture.write_ipv4_routes(
            "eno1 00000000 invalid 0003 0 0 10 00000000 0 0 0"
        )

        result = self.fixture.detector().detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertIsNone(result.value.ipv4_default_gateways)
        self.assertIn("Malformed IPv4 default route.", result.issues)

    def test_empty_ipv4_route_file_is_unknown_not_known_empty(self) -> None:
        self.fixture.ipv4_route_path.write_text("", encoding="utf-8")

        result = self.fixture.detector().detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertIsNone(result.value.ipv4_default_gateways)
        self.assertIn("Malformed IPv4 routing information.", result.issues)

    def test_incorrect_ipv4_route_header_is_unknown(self) -> None:
        self.fixture.ipv4_route_path.write_text(
            "Iface Destination Gateway Flags Metric Mask\n", encoding="utf-8"
        )

        result = self.fixture.detector().detect()

        self.assertIsNone(result.value.ipv4_default_gateways)
        self.assertIn("Malformed IPv4 routing information.", result.issues)

    def test_big_endian_ipv4_gateway_is_decoded_in_native_order(self) -> None:
        self.fixture.add_interface("eno1")
        self.fixture.write_ipv4_routes(
            "eno1 00000000 C0000201 0003 0 0 100 00000000 0 0 0"
        )

        result = self.fixture.detector(byte_order="big").detect()

        self.assertEqual(
            result.value.ipv4_default_gateways,
            (NetworkGatewayInfo("192.0.2.1", 4, "eno1", 100),),
        )

    def test_rejected_and_zero_ipv4_gateways_are_not_reported(self) -> None:
        self.fixture.add_interface("eno1")
        self.fixture.write_ipv4_routes(
            "eno1 00000000 010200C0 0203 0 0 10 00000000 0 0 0",
            "eno1 00000000 00000000 0003 0 0 20 00000000 0 0 0",
        )

        result = self.fixture.detector().detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertIsNone(result.value.ipv4_default_gateways)
        self.assertIn("Malformed IPv4 default route.", result.issues)

    def test_truncated_ipv4_default_route_is_unknown(self) -> None:
        self.fixture.write_ipv4_routes(
            "eno1 00000000 010200C0 0003 0 0 10 00000000"
        )

        result = self.fixture.detector().detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertIsNone(result.value.ipv4_default_gateways)
        self.assertIn("Malformed IPv4 default route.", result.issues)

    def test_default_route_with_unknown_metric_is_sorted_last(self) -> None:
        self.fixture.add_interface("eno1")
        self.fixture.write_ipv4_routes(
            "eno1 00000000 010200C0 0003 0 0 invalid 00000000 0 0 0",
            "eno1 00000000 020200C0 0003 0 0 20 00000000 0 0 0",
        )

        result = self.fixture.detector().detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertEqual(
            result.value.ipv4_default_gateways,
            (
                NetworkGatewayInfo("192.0.2.2", 4, "eno1", 20),
                NetworkGatewayInfo("192.0.2.1", 4, "eno1", None),
            ),
        )

    def test_gateway_for_unknown_interface_is_retained_unassociated(self) -> None:
        self.fixture.write_ipv4_routes(
            "vanished 00000000 010200C0 0003 0 0 5 00000000 0 0 0"
        )

        result = self.fixture.detector().detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertEqual(
            result.value.ipv4_default_gateways,
            (NetworkGatewayInfo("192.0.2.1", 4, None, 5),),
        )

    def test_valid_and_absent_ipv6_default_gateway(self) -> None:
        absent = self.fixture.detector().detect()
        self.assertIsNone(absent.value.ipv6_default_gateways)

        self.fixture.add_interface("eno1")
        self.fixture.write_ipv6_routes(
            "00000000000000000000000000000000 00 "
            "00000000000000000000000000000000 00 "
            "00000000000000000000000000000000 ffffffff "
            "00000001 00000000 00200200 eno1",
            "00000000000000000000000000000000 00 "
            "00000000000000000000000000000000 00 "
            "20010db8000000000000000000000001 00000020 "
            "00000000 00000000 00000003 eno1"
        )

        result = self.fixture.detector().detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(
            result.value.ipv6_default_gateways,
            (NetworkGatewayInfo("2001:db8::1", 6, "eno1", 32),),
        )

    def test_malformed_ipv6_default_route_is_unknown(self) -> None:
        self.fixture.write_ipv6_routes(
            "00000000000000000000000000000000 00 "
            "00000000000000000000000000000000 00 invalid"
        )

        result = self.fixture.detector().detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertIsNone(result.value.ipv6_default_gateways)
        self.assertIn("Malformed IPv6 default route.", result.issues)

    def test_rejected_and_source_specific_ipv6_routes_are_not_global(self) -> None:
        self.fixture.write_ipv6_routes(
            "00000000000000000000000000000000 00 "
            "00000000000000000000000000000000 00 "
            "20010db8000000000000000000000001 00000020 "
            "00000000 00000000 00000203 eno1",
            "00000000000000000000000000000000 00 "
            "20010db8000000000000000000000000 40 "
            "20010db8000000000000000000000002 00000010 "
            "00000000 00000000 00000003 eno1",
        )

        result = self.fixture.detector().detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(result.value.ipv6_default_gateways, ())

    def test_out_of_range_ipv6_prefix_is_malformed(self) -> None:
        self.fixture.write_ipv6_routes(
            "00000000000000000000000000000000 ff "
            "00000000000000000000000000000000 00 "
            "20010db8000000000000000000000001 00000020 "
            "00000000 00000000 00000003 eno1"
        )

        result = self.fixture.detector().detect()

        self.assertIsNone(result.value.ipv6_default_gateways)
        self.assertIn("Malformed IPv6 default route.", result.issues)

    def test_configured_dns_preserves_unique_first_seen_order(self) -> None:
        self.fixture.resolv_conf_path.write_text(
            "# generated\n"
            "nameserver 2001:db8::53\n"
            "search example.test\n"
            "nameserver 192.0.2.53\n"
            "nameserver 2001:db8::53\n",
            encoding="utf-8",
        )

        result = self.fixture.detector().detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(
            result.value.configured_dns, ("2001:db8::53", "192.0.2.53")
        )

    def test_missing_and_protected_resolv_conf_are_unknown(self) -> None:
        self.fixture.resolv_conf_path.unlink()
        missing = self.fixture.detector().detect()

        self.assertIs(missing.status, DetectionStatus.PARTIAL)
        self.assertIsNone(missing.value.configured_dns)
        self.assertIn("Configured DNS is unavailable.", missing.issues)

        def protected_reader(path: Path) -> str:
            if path == self.fixture.resolv_conf_path:
                raise PermissionError("fixture")
            return path.read_text(encoding="utf-8")

        self.fixture.add_interface("visible", hardware_type="772")
        protected = self.fixture.detector(read_text=protected_reader).detect()
        self.assertIs(protected.status, DetectionStatus.PARTIAL)
        self.assertIsNone(protected.value.configured_dns)

    def test_malformed_nameserver_preserves_valid_dns_and_marks_partial(self) -> None:
        self.fixture.resolv_conf_path.write_text(
            "nameserver invalid\nnameserver 192.0.2.53\nnameserver\n",
            encoding="utf-8",
        )

        result = self.fixture.detector().detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertEqual(result.value.configured_dns, ("192.0.2.53",))
        self.assertIn("Malformed DNS configuration.", result.issues)

    def test_only_malformed_nameservers_are_unknown(self) -> None:
        self.fixture.resolv_conf_path.write_text(
            "nameserver invalid\nnameserver\n", encoding="utf-8"
        )

        result = self.fixture.detector().detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertIsNone(result.value.configured_dns)
        self.assertIn("Malformed DNS configuration.", result.issues)

    def test_stub_dns_and_systemd_upstream_dns_remain_distinct(self) -> None:
        self.fixture.resolv_conf_path.write_text(
            "nameserver 127.0.0.53\n", encoding="utf-8"
        )
        self.fixture.systemd_resolv_conf_path.parent.mkdir(parents=True)
        self.fixture.systemd_resolv_conf_path.write_text(
            "nameserver 9.9.9.9\nnameserver 1.1.1.1\n", encoding="utf-8"
        )

        result = self.fixture.detector().detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(result.value.configured_dns, ("127.0.0.53",))
        self.assertEqual(result.value.upstream_dns, ("9.9.9.9", "1.1.1.1"))


if __name__ == "__main__":
    unittest.main()
