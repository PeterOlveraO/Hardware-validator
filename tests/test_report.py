"""Tests for deterministic inventory presentation."""

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
from hardware_validator.models import PartitionInfo
from hardware_validator.models import StorageInfo
from hardware_validator.models import SwapInfo
from hardware_validator.models import SystemInfo
from hardware_validator.report import UNKNOWN
from hardware_validator.report import format_optional
from hardware_validator.report import render_inventory


def complete_results(
    *,
    system: SystemInfo = SystemInfo(),
    cpu: CpuInfo = CpuInfo(),
    memory: MemoryInfo = MemoryInfo(),
    storage: StorageInfo = StorageInfo(),
    gpu: GpuInventory = GpuInventory(),
) -> InventoryResults:
    return InventoryResults(
        system=DetectionResult.complete(system),
        cpu=DetectionResult.complete(cpu),
        memory=DetectionResult.complete(memory),
        storage=DetectionResult.complete(storage),
        gpu=DetectionResult.complete(gpu),
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
      Devices: None detected"""
)


class ReportTests(unittest.TestCase):
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
                        - HDMI-A-1"""
            ),
        )

    def test_complete_empty_collections_say_none_detected(self) -> None:
        self.assertEqual(render_inventory(complete_results()), EMPTY_COMPLETE_REPORT)

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
        )

        output = render_inventory(results)

        self.assertEqual(output, EMPTY_COMPLETE_REPORT.replace("None detected", UNKNOWN))
        for forbidden in (*issues, "partial", "unavailable", "PASS", "FAIL", "WARNING"):
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
