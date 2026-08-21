"""Tests for non-destructive Linux storage detection."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hardware_validator.collector import DetectionStatus
from hardware_validator.detectors.storage import StorageDetector


class StorageFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.class_root = root / "sys/class/block"
        self.class_root.mkdir(parents=True)

    def add_device(
        self,
        name: str,
        *,
        virtual: bool = False,
        attributes: dict[str, str] | None = None,
        subsystem: str | None = None,
    ) -> Path:
        base = "devices/virtual/block" if virtual else "devices/platform/block"
        target = self.root / base / name
        target.mkdir(parents=True)
        (self.class_root / name).symlink_to(target, target_is_directory=True)
        for relative_path, value in (attributes or {}).items():
            path = target / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        if subsystem is not None:
            subsystem_target = self.root / "sys/bus" / subsystem
            subsystem_target.mkdir(parents=True, exist_ok=True)
            device = target / "device"
            device.mkdir(exist_ok=True)
            (device / "subsystem").symlink_to(
                subsystem_target, target_is_directory=True
            )
        return target

    def add_partition(
        self,
        disk_target: Path,
        name: str,
        *,
        attributes: dict[str, str] | None = None,
    ) -> Path:
        target = disk_target / name
        target.mkdir()
        (self.class_root / name).symlink_to(target, target_is_directory=True)
        values = {"partition": "1", **(attributes or {})}
        for relative_path, value in values.items():
            path = target / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        return target


def mount(device: str, mountpoint: str, filesystem: str = "ext4") -> object:
    return SimpleNamespace(device=device, mountpoint=mountpoint, fstype=filesystem)


def usage(total: int = 1000, used: int = 400, free: int = 600) -> object:
    return SimpleNamespace(total=total, used=used, free=free)


class StorageDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.fixture = StorageFixture(Path(self.temporary_directory.name))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def detector(
        self,
        *,
        mounts: tuple[object, ...] = (),
        disk_usage=lambda _mount_point: usage(),
        read_text=None,
    ) -> StorageDetector:
        return StorageDetector(
            sys_block_root=self.fixture.class_root,
            disk_partitions=lambda *, all: mounts,
            disk_usage=disk_usage,
            read_text=read_text or (lambda path: path.read_text(encoding="utf-8")),
            path_exists=lambda _path: True,
        )

    def test_detects_sata_hdd_and_ssd_metadata(self) -> None:
        common = {
            "device/protocol": "ATA\n",
            "removable": "0\n",
            "ro": "0\n",
            "queue/logical_block_size": "512\n",
            "queue/physical_block_size": "4096\n",
        }
        self.fixture.add_device(
            "sda",
            attributes={
                **common,
                "size": "8\n",
                "queue/rotational": "1\n",
                "device/vendor": " ATA Corp. \n",
                "device/model": "Spinning Disk\n",
                "device/serial": "SER-HDD\n",
            },
        )
        self.fixture.add_device(
            "sdb",
            attributes={**common, "size": "16", "queue/rotational": "0"},
        )

        result = self.detector().detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        hdd, ssd = result.value.disks
        self.assertEqual((hdd.kind, ssd.kind), ("hdd", "ssd"))
        self.assertEqual((hdd.transport, ssd.transport), ("sata", "sata"))
        self.assertEqual(hdd.size_bytes, 8 * 512)
        self.assertEqual(hdd.vendor, "ATA Corp.")
        self.assertEqual(hdd.serial_number, "SER-HDD")
        self.assertEqual(hdd.logical_sector_size, 512)
        self.assertEqual(hdd.physical_sector_size, 4096)

    def test_classifies_nvme_emmc_virtual_removable_and_unknown(self) -> None:
        self.fixture.add_device(
            "fast0",
            attributes={"removable": "0", "queue/rotational": "0"},
            subsystem="nvme",
        )
        self.fixture.add_device(
            "flash0",
            attributes={
                "removable": "0",
                "queue/rotational": "0",
                "device/type": "MMC\n",
            },
            subsystem="mmc",
        )
        self.fixture.add_device(
            "loop0",
            virtual=True,
            attributes={"removable": "0", "queue/rotational": "0"},
        )
        self.fixture.add_device(
            "usbdrive",
            attributes={"removable": "1", "queue/rotational": "0"},
            subsystem="usb",
        )
        self.fixture.add_device(
            "mystery", attributes={"queue/rotational": "0"}
        )

        result = self.detector().detect()
        kinds = {disk.name: disk.kind for disk in result.value.disks}

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(kinds["fast0"], "nvme")
        self.assertEqual(kinds["flash0"], "emmc")
        self.assertEqual(kinds["loop0"], "virtual")
        self.assertEqual(kinds["usbdrive"], "removable")
        self.assertIsNone(kinds["mystery"])

    def test_mmc_subsystem_without_mmc_type_is_not_guessed_as_emmc(self) -> None:
        self.fixture.add_device(
            "card0",
            attributes={"removable": "0"},
            subsystem="mmc",
        )

        disk = self.detector().detect().value.disks[0]

        self.assertIsNone(disk.kind)
        self.assertEqual(disk.transport, "mmc")

    def test_associates_partitions_by_canonical_parent_and_exact_source(self) -> None:
        disk_target = self.fixture.add_device(
            "whole",
            attributes={"size": "100", "queue/rotational": "1"},
        )
        self.fixture.add_partition(
            disk_target,
            "unrelated-name",
            attributes={"size": "40", "ro": "0"},
        )
        self.fixture.add_partition(
            disk_target,
            "unused-slice",
            attributes={"size": "60", "ro": "1"},
        )
        mounts = (
            mount("/dev/unrelated-name", "/media/My Drive", "xfs"),
            mount("unrelated-name", r"/media/escaped\040name", "xfs"),
            mount("/dev/unrelated-name-extra", "/wrong", "ext4"),
        )

        result = self.detector(mounts=mounts).detect()
        disk = result.value.disks[0]
        mounted, unmounted = disk.partitions

        self.assertEqual(disk.name, "whole")
        self.assertEqual(mounted.parent_name, "whole")
        self.assertEqual(mounted.size_bytes, 40 * 512)
        self.assertEqual(mounted.filesystem, "xfs")
        self.assertEqual(
            mounted.mount_points,
            ("/media/My Drive",),
        )
        self.assertEqual(unmounted.mount_points, ())
        self.assertIsNone(unmounted.filesystem)
        self.assertEqual(
            tuple(item.mount_point for item in result.value.mounts),
            ("/media/My Drive", r"/media/escaped\040name", "/wrong"),
        )

    def test_mounts_are_deterministic_and_include_usage(self) -> None:
        self.fixture.add_device("disk")
        mounts = (
            mount("tmpfs", "/z", "tmpfs"),
            mount("/dev/disk", "/a path", "ext4"),
        )
        requested: list[str] = []

        def disk_usage(mount_point: str) -> object:
            requested.append(mount_point)
            return usage(total=200, used=50, free=150)

        result = self.detector(mounts=mounts, disk_usage=disk_usage).detect()

        self.assertEqual(requested, ["/z", "/a path"])
        self.assertEqual(
            tuple(item.mount_point for item in result.value.mounts),
            ("/a path", "/z"),
        )
        self.assertEqual(result.value.mounts[0].total_bytes, 200)
        self.assertEqual(result.value.mounts[0].used_bytes, 50)
        self.assertEqual(result.value.mounts[0].available_bytes, 150)

    def test_missing_optional_attributes_are_normal(self) -> None:
        self.fixture.add_device("visible")

        result = self.detector().detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        disk = result.value.disks[0]
        self.assertEqual(disk.device_path, "/dev/visible")
        self.assertIsNone(disk.size_bytes)
        self.assertIsNone(disk.rotational)
        self.assertIsNone(disk.kind)

    def test_permission_errors_keep_partial_device_and_mount_data(self) -> None:
        target = self.fixture.add_device(
            "restricted",
            attributes={"size": "10", "queue/rotational": "0"},
        )
        denied_path = target / "size"

        def read_text(path: Path) -> str:
            if path.resolve() == denied_path:
                raise PermissionError("fixture")
            return path.read_text(encoding="utf-8")

        def denied_usage(_mount_point: str) -> object:
            raise PermissionError("fixture")

        result = self.detector(
            mounts=(mount("/dev/restricted", "/restricted"),),
            disk_usage=denied_usage,
            read_text=read_text,
        ).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertIsNone(result.value.disks[0].size_bytes)
        self.assertEqual(result.value.mounts[0].mount_point, "/restricted")
        self.assertIsNone(result.value.mounts[0].total_bytes)

    def test_malformed_attributes_are_partial_not_fatal(self) -> None:
        self.fixture.add_device(
            "broken",
            attributes={
                "size": "not-a-number",
                "ro": "2",
                "queue/logical_block_size": "0",
            },
        )

        result = self.detector().detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        disk = result.value.disks[0]
        self.assertIsNone(disk.size_bytes)
        self.assertIsNone(disk.read_only)
        self.assertIsNone(disk.logical_sector_size)

    def test_remote_mount_usage_is_not_queried(self) -> None:
        self.fixture.add_device("disk")

        def unexpected_usage(_mount_point: str) -> object:
            self.fail("remote usage must not be queried")

        result = self.detector(
            mounts=(mount("server:/data", "/network", "nfs4"),),
            disk_usage=unexpected_usage,
        ).detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertIsNone(result.value.mounts[0].total_bytes)

    def test_impossible_mount_usage_is_partial(self) -> None:
        self.fixture.add_device("disk")

        result = self.detector(
            mounts=(mount("/dev/disk", "/disk"),),
            disk_usage=lambda _mount_point: usage(total=100, used=80, free=30),
        ).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertIsNone(result.value.mounts[0].used_bytes)
        self.assertIsNone(result.value.mounts[0].available_bytes)


if __name__ == "__main__":
    unittest.main()
