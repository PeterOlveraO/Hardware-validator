"""Tests for public inventory models."""

import unittest
from dataclasses import FrozenInstanceError

from hardware_validator import CpuCacheInfo
from hardware_validator import CpuInfo
from hardware_validator import DiskInfo
from hardware_validator import GpuInfo
from hardware_validator import HardwareSnapshot
from hardware_validator import MemoryInfo
from hardware_validator import MemoryModuleInfo
from hardware_validator import NetworkInfo
from hardware_validator import NetworkAddressInfo
from hardware_validator import NetworkGatewayInfo
from hardware_validator import NetworkInterfaceInfo
from hardware_validator import __version__


class ModelTests(unittest.TestCase):
    def test_default_snapshot_contains_every_component(self) -> None:
        snapshot = HardwareSnapshot()

        self.assertEqual(snapshot.cpu.caches, ())
        self.assertEqual(snapshot.memory.modules, ())
        self.assertEqual(snapshot.storage.disks, ())
        self.assertEqual(snapshot.gpu.devices, ())
        self.assertEqual(snapshot.network.interfaces, ())

    def test_models_are_immutable(self) -> None:
        snapshot = HardwareSnapshot()

        with self.assertRaises(FrozenInstanceError):
            snapshot.cpu = snapshot.cpu  # type: ignore[misc]

    def test_nested_models_accept_valid_values(self) -> None:
        memory = MemoryInfo(
            total_bytes=16,
            available_bytes=10,
            used_bytes=6,
            free_bytes=4,
            modules=(MemoryModuleInfo(locator="DIMM_A1", size_bytes=16),),
        )
        cache = CpuCacheInfo(level=3, kind="unified", size_bytes=32)
        disk = DiskInfo(name="sda", rotational=True, size_bytes=512)
        gpu = GpuInfo(vendor_id="0x1002", device_id="0x73bf")

        self.assertEqual(memory.used_bytes, 6)
        self.assertEqual(cache.kind, "unified")
        self.assertTrue(disk.rotational)
        self.assertEqual(gpu.vendor_id, "0x1002")

    def test_negative_capacity_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            DiskInfo(name="sda", size_bytes=-1)

    def test_inconsistent_memory_usage_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MemoryInfo(total_bytes=10, available_bytes=4, used_bytes=5)

    def test_zero_memory_total_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MemoryInfo(total_bytes=0)

    def test_zero_cpu_frequency_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CpuInfo(current_frequency_hz=0)

    def test_zero_cache_level_is_valid(self) -> None:
        self.assertEqual(CpuCacheInfo(level=0, kind="data").level, 0)

    def test_collections_must_be_tuples(self) -> None:
        with self.assertRaises(TypeError):
            MemoryInfo(modules=[])  # type: ignore[arg-type]

    def test_gpu_ids_use_canonical_format(self) -> None:
        for vendor_id in ("1002", "0xABCD", "0x٠٠٠١"):
            with self.subTest(vendor_id=vendor_id), self.assertRaises(ValueError):
                GpuInfo(vendor_id=vendor_id)

    def test_package_version(self) -> None:
        self.assertEqual(__version__, "0.2.0")

    def test_network_models_accept_typed_immutable_values(self) -> None:
        interface = NetworkInterfaceInfo(
            name="fixture0",
            interface_type="ethernet",
            is_up=True,
            mac_address="00:11:22:33:44:55",
            ipv4_addresses=(
                NetworkAddressInfo("192.0.2.1", 24, "255.255.255.0"),
            ),
            ipv6_addresses=(NetworkAddressInfo("2001:db8::1", 64),),
            speed_mbps=1000,
            mtu=1500,
            duplex="full",
        )
        inventory = NetworkInfo(interfaces=(interface,))

        self.assertEqual(inventory.interfaces[0].speed_mbps, 1000)
        with self.assertRaises(FrozenInstanceError):
            interface.name = "changed"  # type: ignore[misc]

    def test_network_model_rejects_unknown_type_literal(self) -> None:
        with self.assertRaises(ValueError):
            NetworkInterfaceInfo(
                name="fixture0",
                interface_type="guessed",  # type: ignore[arg-type]
            )

    def test_network_address_rejects_invalid_or_conflicting_ip_data(self) -> None:
        invalid_values = (
            ("not-an-address", None, None),
            ("192.0.2.1", 33, None),
            ("192.0.2.1", 24, "255.0.255.0"),
            ("192.0.2.1", 16, "255.255.255.0"),
            ("192.0.2.1", 24, "ffff:ffff:ffff:ffff::"),
            ("fe80::1%fixture0", 64, "ffff:ffff:ffff:ffff::%fixture0"),
        )

        for address, prefix, mask in invalid_values:
            with self.subTest(address=address, prefix=prefix, mask=mask):
                with self.assertRaises(ValueError):
                    NetworkAddressInfo(address, prefix, mask)

    def test_network_collections_enforce_ip_families(self) -> None:
        with self.assertRaises(ValueError):
            NetworkInterfaceInfo(
                name="fixture0",
                interface_type="unknown",
                ipv4_addresses=(NetworkAddressInfo("2001:db8::1"),),
            )
        with self.assertRaises(ValueError):
            NetworkInfo(
                ipv4_default_gateways=(
                    NetworkGatewayInfo("2001:db8::1", 6),
                )
            )

    def test_network_gateway_and_dns_require_matching_valid_ips(self) -> None:
        with self.assertRaises(ValueError):
            NetworkGatewayInfo("192.0.2.1", 6)
        with self.assertRaises(ValueError):
            NetworkGatewayInfo("192.0.2.1", 4.0)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            NetworkInfo(configured_dns=("not-an-address",))


if __name__ == "__main__":
    unittest.main()
