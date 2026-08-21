"""Tests for deterministic inventory presentation."""

from dataclasses import replace
from textwrap import dedent
import unittest

from hardware_validator.collector import DetectionResult
from hardware_validator.collector import InventoryResults
from hardware_validator.models import BaseboardInfo
from hardware_validator.models import BiosInfo
from hardware_validator.models import CpuCacheInfo
from hardware_validator.models import CpuInfo
from hardware_validator.models import DiskInfo
from hardware_validator.models import GpuInfo
from hardware_validator.models import GpuInventory
from hardware_validator.models import MemoryInfo
from hardware_validator.models import MemoryModuleInfo
from hardware_validator.models import MountInfo
from hardware_validator.models import NetworkAddressInfo
from hardware_validator.models import NetworkGatewayInfo
from hardware_validator.models import NetworkInfo
from hardware_validator.models import NetworkInterfaceInfo
from hardware_validator.models import PartitionInfo
from hardware_validator.models import StorageInfo
from hardware_validator.models import SwapInfo
from hardware_validator.models import SystemInfo
from hardware_validator.report import UNKNOWN
from hardware_validator.report import format_optional
from hardware_validator.report import render_inventory
from hardware_validator.report import render_summary


def complete_results(
    *,
    system: SystemInfo = SystemInfo(),
    cpu: CpuInfo = CpuInfo(),
    memory: MemoryInfo = MemoryInfo(),
    storage: StorageInfo = StorageInfo(),
    gpu: GpuInventory = GpuInventory(),
    network: NetworkInfo = NetworkInfo(),
) -> InventoryResults:
    return InventoryResults(
        system=DetectionResult.complete(system),
        cpu=DetectionResult.complete(cpu),
        memory=DetectionResult.complete(memory),
        storage=DetectionResult.complete(storage),
        gpu=DetectionResult.complete(gpu),
        network=DetectionResult.complete(network),
    )


EMPTY_COMPLETE_REPORT = dedent(
    """\
    System
      Distribution: Unknown
      Distribution Version: Unknown
      Kernel Version: Unknown
      Hostname: Unknown
      Manufacturer: Unknown
      Model: Unknown
      Architecture: Unknown
      Baseboard:
        Manufacturer: Unknown
        Model: Unknown
        Version: Unknown
      BIOS:
        Manufacturer: Unknown
        Version: Unknown
        Release Date: Unknown

    CPU
      Model Names: None detected
      Vendor IDs: None detected
      Architecture: Unknown
      Physical Packages: Unknown
      Physical Cores: Unknown
      Logical Processors: Unknown
      Current Frequency: Unknown
      Maximum Frequency: Unknown
      Caches: None detected
      Capabilities: None detected

    Memory
      Total: Unknown
      Available: Unknown
      Used: Unknown
      Free: Unknown
      Modules: None detected
      Swap:
        Total: Unknown
        Available: Unknown
        Used: Unknown

    Storage
      Disks: None detected
      Mounts: None detected

    GPU
      Devices: None detected

    Network
      Interfaces: None detected
      IPv4 Default Gateways: Unknown
      IPv6 Default Gateways: Unknown
      Configured DNS: Unknown"""
)


class ReportTests(unittest.TestCase):
    def test_summary_snapshot_contains_only_primary_information(self) -> None:
        results = complete_results(
            system=SystemInfo(
                distribution="Example Linux",
                distribution_version="24.04",
                kernel_version="6.8.0",
                hostname="workstation",
                manufacturer="System Maker",
                model="System Model",
                architecture="x86_64",
                baseboard=BaseboardInfo(model="Hidden Board"),
                bios=BiosInfo(version="Hidden BIOS"),
            ),
            cpu=CpuInfo(
                model_names=("Main CPU", "Secondary CPU"),
                vendor_ids=("Hidden Vendor",),
                physical_package_count=2,
                physical_core_count=8,
                logical_processor_count=16,
                current_frequency_hz=2_500_000_000,
                maximum_frequency_hz=4_000_000_000,
                caches=(CpuCacheInfo(level=1, kind="data", size_bytes=32 * 1024),),
                capabilities=("hidden_flag",),
            ),
            memory=MemoryInfo(
                total_bytes=16 * 1024**3,
                available_bytes=6 * 1024**3,
                used_bytes=10 * 1024**3,
                free_bytes=2 * 1024**3,
                modules=(
                    MemoryModuleInfo(locator="DIMM_A1", size_bytes=8 * 1024**3),
                    MemoryModuleInfo(locator="DIMM_B1", size_bytes=8 * 1024**3),
                ),
                swap=SwapInfo(
                    total_bytes=2 * 1024**3,
                    available_bytes=1024**3,
                    used_bytes=1024**3,
                ),
            ),
            storage=StorageInfo(
                disks=(
                    DiskInfo(name="loop0", model="Hidden Loop", kind="virtual"),
                    DiskInfo(name="usb0", model="Hidden USB", kind="removable"),
                    DiskInfo(
                        name="nvme0n1",
                        serial_number="hidden-serial",
                        size_bytes=1024**4,
                        rotational=False,
                        transport="nvme",
                        partitions=(
                            PartitionInfo(
                                name="nvme0n1p1",
                                parent_name="nvme0n1",
                                filesystem="ext4",
                                mount_points=("/",),
                            ),
                        ),
                    ),
                ),
                mounts=(
                    MountInfo(
                        mount_point="/",
                        source="/dev/nvme0n1p1",
                        filesystem="ext4",
                        total_bytes=512 * 1024**3,
                        used_bytes=128 * 1024**3,
                        available_bytes=384 * 1024**3,
                    ),
                ),
            ),
            gpu=GpuInventory(
                devices=(
                    GpuInfo(commercial_name="Named GPU", driver="driver-a"),
                    GpuInfo(
                        vendor_id="0x1234",
                        device_id="0xabcd",
                        adapter_type="dedicated",
                        memory_total_bytes=8 * 1024**3,
                        driver="driver-b",
                        address="0000:01:00.0",
                        is_boot_vga=True,
                        connectors=("HDMI-A-1",),
                    ),
                )
            ),
            network=NetworkInfo(
                interfaces=(
                    NetworkInterfaceInfo(
                        name="uplink0",
                        interface_type="ethernet",
                        is_up=True,
                        mac_address="00:11:22:33:44:55",
                        ipv4_addresses=(
                            NetworkAddressInfo(
                                "192.0.2.10", 24, "255.255.255.0"
                            ),
                        ),
                        ipv6_addresses=(
                            NetworkAddressInfo("2001:db8::10", 64),
                            NetworkAddressInfo("fe80::10%uplink0", 64),
                        ),
                        speed_mbps=1000,
                        mtu=1500,
                        duplex="full",
                    ),
                    NetworkInterfaceInfo(
                        name="radio0",
                        interface_type="wifi",
                        is_up=False,
                        mac_address=None,
                        ipv4_addresses=(),
                        ipv6_addresses=(),
                    ),
                    NetworkInterfaceInfo(
                        name="local0",
                        interface_type="loopback",
                        is_up=True,
                    ),
                    NetworkInterfaceInfo(
                        name="virtual0",
                        interface_type="virtual",
                        is_up=True,
                    ),
                    NetworkInterfaceInfo(
                        name="mystery0",
                        interface_type="unknown",
                    ),
                ),
                ipv4_default_gateways=(
                    NetworkGatewayInfo("192.0.2.1", 4, "uplink0", 100),
                ),
                ipv6_default_gateways=(),
                configured_dns=("127.0.0.53",),
                upstream_dns=("192.0.2.53",),
            ),
        )

        output = render_summary(results)

        self.assertEqual(
            output,
            dedent(
                """\
                Computer
                  Manufacturer: System Maker
                  Model: System Model
                  Distribution: Example Linux
                  Version: 24.04
                  Architecture: x86_64

                Processor
                  Model: Main CPU
                  Physical Cores: 8
                  Logical Processors: 16
                  Current Frequency: 2.5 GHz
                  Maximum Frequency: 4 GHz

                Memory
                  Total: 16 GiB
                  Used: 10 GiB
                  Available: 6 GiB
                  Free: 2 GiB
                  Modules: 2 detected

                Storage
                  Devices:
                    Device 1:
                      Model: nvme0n1
                      Capacity: 1 TiB
                      Rotational: No
                  Root Filesystem:
                    Total: 512 GiB
                    Used: 128 GiB
                    Available: 384 GiB

                Graphics
                  Devices:
                    GPU 1:
                      Name: Named GPU
                      Driver: driver-a
                    GPU 2:
                      Name: PCI device 0x1234:0xabcd
                      Driver: driver-b
                      Type: dedicated
                      Memory: 8 GiB

                Network
                  Ethernet:
                    Type: Ethernet
                    Interface: uplink0
                    Status: Connected
                    Speed: 1 Gbps
                    IPv4: 192.0.2.10
                    Subnet Mask: 255.255.255.0
                    MAC: 00:11:22:33:44:55
                    Default Gateway: 192.0.2.1
                  Wi-Fi:
                    Type: Wi-Fi
                    Interface: radio0
                    Status: Disconnected
                    IPv4: None assigned
                    MAC: Unknown
                    Default Gateway: None assigned

                  Configured DNS:
                    - 127.0.0.53"""
            ),
        )
        for hidden in (
            "Kernel Version",
            "Hostname",
            "Baseboard",
            "BIOS",
            "Vendor IDs",
            "Physical Packages",
            "Caches",
            "Capabilities",
            "Swap",
            "Hidden Loop",
            "Hidden USB",
            "Serial Number",
            "Transport",
            "Read Only",
            "Partitions",
            "Mount Points",
            "Address",
            "Primary",
            "Boot VGA",
            "Connectors",
            "local0",
            "virtual0",
            "mystery0",
        ):
            self.assertNotIn(hidden, output)

    def test_summary_handles_empty_and_unverifiable_collections(self) -> None:
        complete_output = render_summary(complete_results())
        unknown_module_output = render_summary(
            complete_results(memory=MemoryInfo(modules=(MemoryModuleInfo(),)))
        )
        unavailable_network_output = render_summary(
            replace(
                complete_results(),
                network=DetectionResult.unavailable(
                    NetworkInfo(), "fixture network unavailable"
                ),
            )
        )
        unknown_addresses = NetworkInterfaceInfo(
            name="fixture0",
            interface_type="ethernet",
            is_up=True,
        )
        unknown_addresses_summary = render_summary(
            complete_results(
                network=NetworkInfo(interfaces=(unknown_addresses,))
            )
        )
        unknown_addresses_verbose = render_inventory(
            complete_results(
                network=NetworkInfo(interfaces=(unknown_addresses,))
            )
        )

        self.assertIn("Modules: Unknown", complete_output)
        self.assertIn("Storage\n  Devices: None detected", complete_output)
        self.assertIn("Graphics\n  Devices: None detected", complete_output)
        self.assertIn("Network\n  Ethernet: None detected", complete_output)
        self.assertIn("  Wi-Fi: None detected", complete_output)
        self.assertIn("Modules: Unknown", unknown_module_output)
        self.assertNotIn("Modules: None detected", complete_output)
        self.assertIn(
            "Network\n  Interfaces: Unknown", unavailable_network_output
        )
        self.assertIn("IPv4: Unknown", unknown_addresses_summary)
        self.assertIn("IPv4 Addresses: Unknown", unknown_addresses_verbose)
        self.assertIn("IPv6 Addresses: Unknown", unknown_addresses_verbose)

    def test_summary_handles_multiple_primary_disks_and_one_gpu(self) -> None:
        results = complete_results(
            storage=StorageInfo(
                disks=(
                    DiskInfo(name="disk-a", model="Disk A"),
                    DiskInfo(name="disk-b"),
                    DiskInfo(name="loop0", kind="virtual"),
                )
            ),
            gpu=GpuInventory(
                devices=(GpuInfo(vendor_id="0x1111", device_id="0x2222"),)
            ),
            network=NetworkInfo(
                interfaces=(
                    NetworkInterfaceInfo(
                        name="wired-b",
                        interface_type="ethernet",
                        is_up=False,
                    ),
                    NetworkInterfaceInfo(
                        name="wired-a",
                        interface_type="ethernet",
                        is_up=True,
                    ),
                )
            ),
        )

        output = render_summary(results)

        self.assertEqual(output.count("    Device "), 2)
        self.assertIn("Model: Disk A", output)
        self.assertIn("Model: disk-b", output)
        self.assertNotIn("loop0", output)
        self.assertEqual(output.count("    GPU "), 1)
        self.assertIn("Name: PCI device 0x1111:0x2222", output)
        self.assertIn("    Interface 1:", output)
        self.assertIn("    Interface 2:", output)

    def test_summary_network_shows_unknown_mask_gateway_and_system_dns_once(self) -> None:
        network = NetworkInfo(
            interfaces=(
                NetworkInterfaceInfo(
                    name="fixture0",
                    interface_type="ethernet",
                    is_up=True,
                    ipv4_addresses=(NetworkAddressInfo("192.0.2.10"),),
                    ipv6_addresses=(),
                ),
            ),
            ipv4_default_gateways=(),
            configured_dns=("192.0.2.53", "198.51.100.53"),
        )

        output = render_summary(complete_results(network=network))

        self.assertIn("IPv4: 192.0.2.10", output)
        self.assertIn("Subnet Mask: Unknown", output)
        self.assertIn("Default Gateway: None assigned", output)
        self.assertEqual(output.count("Configured DNS:"), 1)
        self.assertIn("- 192.0.2.53", output)
        self.assertIn("- 198.51.100.53", output)

    def test_summary_unknown_values_and_control_characters_are_safe(self) -> None:
        results = complete_results(
            system=SystemInfo(model="unsafe\x1b\u202emodel"),
            storage=StorageInfo(disks=(DiskInfo(name="disk\nname"),)),
            gpu=GpuInventory(devices=(GpuInfo(commercial_name="gpu\tname"),)),
            network=NetworkInfo(
                interfaces=(
                    NetworkInterfaceInfo(
                        name="net\x1bname",
                        interface_type="wifi",
                        ipv4_addresses=(NetworkAddressInfo("192.0.2.1"),),
                    ),
                )
            ),
        )

        output = render_summary(results)

        self.assertIn("Manufacturer: Unknown", output)
        self.assertIn(r"Model: unsafe\x1b\u202emodel", output)
        self.assertIn(r"Model: disk\x0aname", output)
        self.assertIn(r"Name: gpu\x09name", output)
        self.assertIn(r"Interface: net\x1bname", output)
        self.assertIn("IPv4: 192.0.2.1", output)
        self.assertNotIn("\x1b", output)

    def test_complete_inventory_snapshot_contains_every_model_field(self) -> None:
        results = complete_results(
            system=SystemInfo(
                distribution="Example Linux",
                distribution_version="24.04",
                kernel_version="6.8.0",
                hostname="workstation",
                manufacturer="System Maker",
                model="System Model",
                architecture="x86_64",
                baseboard=BaseboardInfo(
                    manufacturer="Board Maker",
                    model="Board Model",
                    version="1.2",
                ),
                bios=BiosInfo(
                    manufacturer="BIOS Maker",
                    version="F10",
                    release_date="2026-01-02",
                ),
            ),
            cpu=CpuInfo(
                model_names=("Example CPU",),
                vendor_ids=("VendorOne",),
                architecture="x86_64",
                physical_package_count=1,
                physical_core_count=8,
                logical_processor_count=16,
                current_frequency_hz=2_500_000_000,
                maximum_frequency_hz=4_000_000_000,
                caches=(CpuCacheInfo(level=1, kind="data", size_bytes=32 * 1024),),
                capabilities=("sse4_2", "avx2"),
            ),
            memory=MemoryInfo(
                total_bytes=16 * 1024**3,
                available_bytes=6 * 1024**3,
                used_bytes=10 * 1024**3,
                free_bytes=2 * 1024**3,
                modules=(
                    MemoryModuleInfo(
                        locator="DIMM_A1",
                        manufacturer="Memory Maker",
                        part_number="PART-1",
                        serial_number="SERIAL-1",
                        size_bytes=8 * 1024**3,
                        memory_type="DDR5",
                        speed_mt_s=5600,
                    ),
                ),
                swap=SwapInfo(
                    total_bytes=2 * 1024**3,
                    available_bytes=1536 * 1024**2,
                    used_bytes=512 * 1024**2,
                ),
            ),
            storage=StorageInfo(
                disks=(
                    DiskInfo(
                        name="nvme0n1",
                        device_path="/dev/nvme0n1",
                        vendor="Disk Maker",
                        model="Fast Disk",
                        serial_number="DISK-1",
                        size_bytes=1024**4,
                        rotational=False,
                        transport="nvme",
                        removable=False,
                        kind="nvme",
                        read_only=False,
                        logical_sector_size=512,
                        physical_sector_size=4096,
                        partitions=(
                            PartitionInfo(
                                name="nvme0n1p1",
                                parent_name="nvme0n1",
                                device_path="/dev/nvme0n1p1",
                                size_bytes=512 * 1024**3,
                                read_only=False,
                                filesystem="ext4",
                                mount_points=("/",),
                            ),
                        ),
                    ),
                ),
                mounts=(
                    MountInfo(
                        mount_point="/",
                        source="/dev/nvme0n1p1",
                        filesystem="ext4",
                        total_bytes=512 * 1024**3,
                        used_bytes=128 * 1024**3,
                        available_bytes=384 * 1024**3,
                    ),
                ),
            ),
            gpu=GpuInventory(
                devices=(
                    GpuInfo(
                        commercial_name="Example Graphics",
                        vendor_id="0x1234",
                        device_id="0xabcd",
                        adapter_type="dedicated",
                        memory_total_bytes=8 * 1024**3,
                        driver="example_driver",
                        address="0000:01:00.0",
                        is_primary=True,
                        is_boot_vga=True,
                        connectors=("DP-1", "HDMI-A-1"),
                    ),
                )
            ),
            network=NetworkInfo(
                interfaces=(
                    NetworkInterfaceInfo(
                        name="uplink0",
                        interface_type="ethernet",
                        is_up=True,
                        mac_address="00:11:22:33:44:55",
                        ipv4_addresses=(
                            NetworkAddressInfo(
                                "192.0.2.10", 24, "255.255.255.0"
                            ),
                        ),
                        ipv6_addresses=(
                            NetworkAddressInfo("2001:db8::10", 64),
                            NetworkAddressInfo("fe80::10%uplink0", 64),
                        ),
                        speed_mbps=1000,
                        mtu=1500,
                        duplex="full",
                    ),
                ),
                ipv4_default_gateways=(
                    NetworkGatewayInfo("192.0.2.1", 4, "uplink0", 100),
                ),
                ipv6_default_gateways=(
                    NetworkGatewayInfo("2001:db8::1", 6, "uplink0", 50),
                ),
                configured_dns=("127.0.0.53",),
                upstream_dns=("192.0.2.53", "2001:db8::53"),
            ),
        )

        self.assertEqual(
            render_inventory(results),
            dedent(
                """\
                System
                  Distribution: Example Linux
                  Distribution Version: 24.04
                  Kernel Version: 6.8.0
                  Hostname: workstation
                  Manufacturer: System Maker
                  Model: System Model
                  Architecture: x86_64
                  Baseboard:
                    Manufacturer: Board Maker
                    Model: Board Model
                    Version: 1.2
                  BIOS:
                    Manufacturer: BIOS Maker
                    Version: F10
                    Release Date: 2026-01-02

                CPU
                  Model Names:
                    - Example CPU
                  Vendor IDs:
                    - VendorOne
                  Architecture: x86_64
                  Physical Packages: 1
                  Physical Cores: 8
                  Logical Processors: 16
                  Current Frequency: 2.5 GHz
                  Maximum Frequency: 4 GHz
                  Caches:
                    Cache 1:
                      Level: 1
                      Kind: data
                      Size: 32 KiB
                  Capabilities:
                    - sse4_2
                    - avx2

                Memory
                  Total: 16 GiB
                  Available: 6 GiB
                  Used: 10 GiB
                  Free: 2 GiB
                  Modules:
                    Module 1:
                      Locator: DIMM_A1
                      Manufacturer: Memory Maker
                      Part Number: PART-1
                      Serial Number: SERIAL-1
                      Size: 8 GiB
                      Type: DDR5
                      Speed: 5600 MT/s
                  Swap:
                    Total: 2 GiB
                    Available: 1.5 GiB
                    Used: 512 MiB

                Storage
                  Disks:
                    Disk 1:
                      Name: nvme0n1
                      Device Path: /dev/nvme0n1
                      Vendor: Disk Maker
                      Model: Fast Disk
                      Serial Number: DISK-1
                      Size: 1 TiB
                      Rotational: No
                      Transport: nvme
                      Removable: No
                      Kind: nvme
                      Read Only: No
                      Logical Sector Size: 512 B
                      Physical Sector Size: 4 KiB
                      Partitions:
                        Partition 1:
                          Name: nvme0n1p1
                          Parent Name: nvme0n1
                          Device Path: /dev/nvme0n1p1
                          Size: 512 GiB
                          Read Only: No
                          Filesystem: ext4
                          Mount Points:
                            - /
                  Mounts:
                    Mount 1:
                      Mount Point: /
                      Source: /dev/nvme0n1p1
                      Filesystem: ext4
                      Total: 512 GiB
                      Used: 128 GiB
                      Available: 384 GiB

                GPU
                  Devices:
                    GPU 1:
                      Commercial Name: Example Graphics
                      Vendor ID: 0x1234
                      Device ID: 0xabcd
                      Adapter Type: dedicated
                      Memory Total: 8 GiB
                      Driver: example_driver
                      Address: 0000:01:00.0
                      Primary: Yes
                      Boot VGA: Yes
                      Connectors:
                        - DP-1
                        - HDMI-A-1

                Network
                  Interfaces:
                    Interface 1:
                      Name: uplink0
                      Type: ethernet
                      Status: Connected
                      MAC: 00:11:22:33:44:55
                      IPv4 Addresses:
                        Address 1:
                          Address: 192.0.2.10
                          Subnet Mask: 255.255.255.0
                          Prefix Length: 24
                      IPv6 Addresses:
                        Address 1:
                          Address: 2001:db8::10
                          Prefix Length: 64
                        Address 2:
                          Address: fe80::10%uplink0
                          Prefix Length: 64
                      Speed: 1 Gbps
                      MTU: 1500 bytes
                      Duplex: Full
                  IPv4 Default Gateways:
                    Gateway 1:
                      Address: 192.0.2.1
                      Interface: uplink0
                      Metric: 100
                  IPv6 Default Gateways:
                    Gateway 1:
                      Address: 2001:db8::1
                      Interface: uplink0
                      Metric: 50
                  Configured DNS:
                    - 127.0.0.53
                  Upstream DNS:
                    - 192.0.2.53
                    - 2001:db8::53"""
            ),
        )

    def test_complete_empty_collections_say_none_detected(self) -> None:
        self.assertEqual(render_inventory(complete_results()), EMPTY_COMPLETE_REPORT)

    def test_verbose_network_includes_every_interface_type(self) -> None:
        interfaces = tuple(
            NetworkInterfaceInfo(
                name=f"interface-{interface_type}",
                interface_type=interface_type,  # type: ignore[arg-type]
                is_up=False,
                ipv4_addresses=(),
                ipv6_addresses=(),
            )
            for interface_type in (
                "ethernet",
                "wifi",
                "loopback",
                "virtual",
                "unknown",
            )
        )

        output = render_inventory(
            complete_results(network=NetworkInfo(interfaces=interfaces))
        )

        for interface_type in (
            "ethernet",
            "wifi",
            "loopback",
            "virtual",
            "unknown",
        ):
            self.assertIn(f"Name: interface-{interface_type}", output)
            self.assertIn(f"Type: {interface_type}", output)
        self.assertIn("IPv4 Addresses: None assigned", output)
        self.assertIn("IPv6 Addresses: None assigned", output)
        self.assertIn("Status: Disconnected", output)
        self.assertIn("Speed: Unknown", output)
        self.assertIn("MTU: Unknown", output)
        self.assertIn("Duplex: Unknown", output)

    def test_incomplete_empty_collections_and_none_scalars_say_unknown(self) -> None:
        issues = (
            "system issue must not be rendered",
            "cpu issue must not be rendered",
            "memory issue must not be rendered",
            "storage issue must not be rendered",
            "gpu issue must not be rendered",
        )
        results = InventoryResults(
            system=DetectionResult.unavailable(SystemInfo(), issues[0]),
            cpu=DetectionResult.partial(CpuInfo(), issues[1]),
            memory=DetectionResult.unavailable(MemoryInfo(), issues[2]),
            storage=DetectionResult.partial(StorageInfo(), issues[3]),
            gpu=DetectionResult.unavailable(GpuInventory(), issues[4]),
            network=DetectionResult.unavailable(
                NetworkInfo(), "network issue must not be rendered"
            ),
        )

        output = render_inventory(results)

        self.assertEqual(output, EMPTY_COMPLETE_REPORT.replace("None detected", UNKNOWN))
        for forbidden in (
            *issues,
            "network issue must not be rendered",
            "partial",
            "unavailable",
            "PASS",
            "FAIL",
            "WARNING",
        ):
            self.assertNotIn(forbidden, output)

    def test_multiple_disks_partitions_and_gpus_keep_stable_input_order(self) -> None:
        results = complete_results(
            storage=StorageInfo(
                disks=(
                    DiskInfo(
                        name="disk-b",
                        partitions=(
                            PartitionInfo(name="part-b2", parent_name="disk-b"),
                            PartitionInfo(name="part-b1", parent_name="disk-b"),
                        ),
                    ),
                    DiskInfo(name="disk-a"),
                ),
                mounts=(MountInfo(mount_point="/z"), MountInfo(mount_point="/a")),
            ),
            gpu=GpuInventory(
                devices=(
                    GpuInfo(commercial_name="GPU B", address="0000:02:00.0"),
                    GpuInfo(commercial_name="GPU A", address="0000:01:00.0"),
                )
            ),
        )

        output = render_inventory(results)

        ordered_markers = (
            "    Disk 1:\n      Name: disk-b",
            "        Partition 1:\n          Name: part-b2",
            "        Partition 2:\n          Name: part-b1",
            "    Disk 2:\n      Name: disk-a",
            "    Mount 1:\n      Mount Point: /z",
            "    Mount 2:\n      Mount Point: /a",
            "    GPU 1:\n      Commercial Name: GPU B",
            "    GPU 2:\n      Commercial Name: GPU A",
        )
        positions = [output.index(marker) for marker in ordered_markers]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(output, render_inventory(results))

    def test_units_precision_and_zero_values_are_readable(self) -> None:
        results = complete_results(
            cpu=CpuInfo(
                current_frequency_hz=1_234_000,
                maximum_frequency_hz=3_600_000_000,
                caches=(CpuCacheInfo(level=0, kind="unified", size_bytes=1536),),
            ),
            memory=MemoryInfo(swap=SwapInfo(total_bytes=0, available_bytes=0, used_bytes=0)),
            storage=StorageInfo(
                disks=(DiskInfo(name="zero", size_bytes=0, logical_sector_size=512),),
                mounts=(
                    MountInfo(
                        mount_point="/empty",
                        total_bytes=0,
                        used_bytes=0,
                        available_bytes=0,
                    ),
                ),
            ),
            gpu=GpuInventory(
                devices=(
                    GpuInfo(
                        memory_total_bytes=0,
                        is_primary=False,
                        is_boot_vga=False,
                    ),
                )
            ),
        )

        output = render_inventory(results)

        for expected in (
            "Current Frequency: 1.23 MHz",
            "Maximum Frequency: 3.6 GHz",
            "Level: 0",
            "Size: 1.5 KiB",
            "Total: 0 B",
            "Size: 0 B",
            "Logical Sector Size: 512 B",
            "Memory Total: 0 B",
            "Primary: No",
            "Boot VGA: No",
        ):
            self.assertIn(expected, output)
        self.assertEqual(format_optional(0), "0")

    def test_long_strings_are_preserved_and_terminal_controls_are_escaped(self) -> None:
        long_value = "hardware-" + "x" * 50_000
        results = complete_results(
            system=SystemInfo(hostname="\x1b[31m\u202ehost", model=long_value),
            gpu=GpuInventory(devices=(GpuInfo(connectors=(long_value,)),)),
        )

        first = render_inventory(results)
        second = render_inventory(results)

        self.assertEqual(first, second)
        self.assertEqual(first.count(long_value), 2)
        self.assertNotIn("\x1b", first)
        self.assertIn(r"Hostname: \x1b[31m\u202ehost", first)
        self.assertNotIn("...", first)

    def test_very_large_values_do_not_overflow_presentation(self) -> None:
        value = 10**400
        results = complete_results(
            storage=StorageInfo(disks=(DiskInfo(name="huge", size_bytes=value),))
        )

        output = render_inventory(results)

        self.assertIn("Size:", output)
        self.assertIn("EiB", output)


if __name__ == "__main__":
    unittest.main()
