"""Non-destructive Linux block device and mount detection."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import psutil

from ..collector import DetectionResult
from ..models import BlockDeviceKind
from ..models import DiskInfo
from ..models import MountInfo
from ..models import PartitionInfo
from ..models import StorageInfo

TextReader = Callable[[Path], str]
PathExists = Callable[[Path], bool]
DiskPartitions = Callable[..., Iterable[object]]
DiskUsage = Callable[[str], object]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip("\x00 \t\r\n")
    if not cleaned or cleaned.casefold() in {"unknown", "not specified"}:
        return None
    return cleaned


def _is_virtual(identity: Path) -> bool:
    parts = identity.parts
    return any(
        parts[index : index + 2] == ("devices", "virtual")
        for index in range(len(parts) - 1)
    )


@dataclass(frozen=True, slots=True)
class _BlockEntry:
    path: Path
    identity: Path
    has_partition_attribute: bool


@dataclass(frozen=True, slots=True)
class _MountRecord:
    info: MountInfo


class StorageDetector:
    """Read Linux storage metadata without opening block devices."""

    def __init__(
        self,
        *,
        sys_block_root: Path = Path("/sys/class/block"),
        disk_partitions: DiskPartitions = psutil.disk_partitions,
        disk_usage: DiskUsage = psutil.disk_usage,
        read_text: TextReader = _read_text,
        path_exists: PathExists = Path.exists,
    ) -> None:
        self._sys_block_root = sys_block_root
        self._disk_partitions = disk_partitions
        self._disk_usage = disk_usage
        self._read_text = read_text
        self._path_exists = path_exists

    def detect(self) -> DetectionResult[StorageInfo]:
        """Collect available block topology, metadata, and mounted filesystems."""

        issues: list[str] = []
        mounts, mounts_available = self._detect_mounts(issues)
        entries, block_devices_available = self._enumerate_entries(issues)

        partition_entries: list[_BlockEntry] = []
        disk_entries: list[_BlockEntry] = []
        identities = {entry.identity for entry in entries}
        for entry in entries:
            has_canonical_parent = entry.identity.parent in identities
            if entry.has_partition_attribute or has_canonical_parent:
                partition_entries.append(entry)
            else:
                disk_entries.append(entry)

        disk_identities = {entry.identity: entry for entry in disk_entries}
        partitions_by_parent: dict[Path, list[_BlockEntry]] = {}
        for partition in partition_entries:
            parent_identity = self._canonical_parent(
                partition.identity, disk_identities
            )
            if parent_identity is None:
                issues.append(f"Unable to identify parent of {partition.path.name}.")
                continue
            partitions_by_parent.setdefault(parent_identity, []).append(partition)

        disks = tuple(
            self._read_disk(
                entry,
                partitions_by_parent.get(entry.identity, ()),
                mounts,
                issues,
            )
            for entry in sorted(disk_entries, key=lambda item: item.path.name)
        )
        value = StorageInfo(
            disks=disks,
            mounts=tuple(record.info for record in mounts),
        )
        unique_issues = tuple(dict.fromkeys(issues))
        useful = bool(value.disks or value.mounts)
        if unique_issues:
            if useful:
                return DetectionResult.partial(value, *unique_issues)
            return DetectionResult.unavailable(value, *unique_issues)
        if useful:
            return DetectionResult.complete(value)
        if mounts_available or block_devices_available:
            return DetectionResult.complete(value)
        return DetectionResult.unavailable(value, "No storage information was available.")

    def _enumerate_entries(
        self, issues: list[str]
    ) -> tuple[tuple[_BlockEntry, ...], bool]:
        try:
            paths = sorted(self._sys_block_root.iterdir(), key=lambda path: path.name)
        except FileNotFoundError:
            issues.append("Block device sysfs is unavailable.")
            return (), False
        except OSError:
            issues.append("Unable to enumerate block devices.")
            return (), False

        entries: list[_BlockEntry] = []
        for path in paths:
            try:
                if not path.is_dir():
                    continue
            except OSError:
                issues.append(f"Unable to inspect block device {path.name}.")
                continue

            try:
                identity = path.resolve()
            except (OSError, RuntimeError):
                issues.append(f"Unable to resolve block device {path.name}.")
                identity = path.absolute()

            try:
                partition_text = self._read_text(path / "partition")
            except FileNotFoundError:
                has_partition_attribute = False
            except (OSError, UnicodeError):
                issues.append(f"Unable to read partition identity for {path.name}.")
                has_partition_attribute = False
            else:
                has_partition_attribute = True
                try:
                    partition_number = int(partition_text.strip())
                except ValueError:
                    partition_number = 0
                if partition_number <= 0:
                    issues.append(f"Malformed partition identity for {path.name}.")

            entries.append(
                _BlockEntry(
                    path=path,
                    identity=identity,
                    has_partition_attribute=has_partition_attribute,
                )
            )
        return tuple(entries), True

    @staticmethod
    def _canonical_parent(
        identity: Path, disks: dict[Path, _BlockEntry]
    ) -> Path | None:
        parent = identity.parent
        while parent != parent.parent:
            if parent in disks:
                return parent
            parent = parent.parent
        return None

    def _read_disk(
        self,
        entry: _BlockEntry,
        partitions: Iterable[_BlockEntry],
        mounts: tuple[_MountRecord, ...],
        issues: list[str],
    ) -> DiskInfo:
        name = entry.path.name
        rotational = self._read_bool(entry.path / "queue/rotational", name, issues)
        removable = self._read_bool(entry.path / "removable", name, issues)
        read_only = self._read_bool(entry.path / "ro", name, issues)
        subsystem = self._resolved_name(entry.path / "device/subsystem", name, issues)
        device_type = self._read_string(entry.path / "device/type", name, issues)
        transport = self._detect_transport(
            entry.path, entry.identity, subsystem, name, issues
        )
        kind = self._classify(
            identity=entry.identity,
            rotational=rotational,
            removable=removable,
            subsystem=subsystem,
            device_type=device_type,
            transport=transport,
        )

        partition_values = tuple(
            self._read_partition(partition, name, mounts, issues)
            for partition in sorted(partitions, key=lambda item: item.path.name)
        )
        return DiskInfo(
            name=name,
            device_path=self._device_path(name),
            vendor=self._read_string(entry.path / "device/vendor", name, issues),
            model=self._read_string(entry.path / "device/model", name, issues),
            serial_number=self._read_first_string(
                (entry.path / "device/serial", entry.path / "serial"), name, issues
            ),
            size_bytes=self._read_sectors(entry.path / "size", name, issues),
            rotational=rotational,
            transport=transport,
            removable=removable,
            kind=kind,
            read_only=read_only,
            logical_sector_size=self._read_positive_int(
                entry.path / "queue/logical_block_size", name, issues
            ),
            physical_sector_size=self._read_positive_int(
                entry.path / "queue/physical_block_size", name, issues
            ),
            partitions=partition_values,
        )

    def _read_partition(
        self,
        entry: _BlockEntry,
        parent_name: str,
        mounts: tuple[_MountRecord, ...],
        issues: list[str],
    ) -> PartitionInfo:
        name = entry.path.name
        device_path = self._device_path(name)
        matching_mounts = tuple(
            record.info
            for record in mounts
            if device_path is not None and record.info.source == device_path
        )
        filesystems = tuple(
            dict.fromkeys(
                mount.filesystem
                for mount in matching_mounts
                if mount.filesystem is not None
            )
        )
        return PartitionInfo(
            name=name,
            parent_name=parent_name,
            device_path=device_path,
            size_bytes=self._read_sectors(entry.path / "size", name, issues),
            read_only=self._read_bool(entry.path / "ro", name, issues),
            filesystem=filesystems[0] if filesystems else None,
            mount_points=tuple(
                sorted({mount.mount_point for mount in matching_mounts})
            ),
        )

    def _device_path(self, name: str) -> str | None:
        path = Path("/dev") / name
        return str(path) if self._path_exists(path) else None

    def _detect_mounts(
        self, issues: list[str]
    ) -> tuple[tuple[_MountRecord, ...], bool]:
        try:
            raw_mounts = tuple(self._disk_partitions(all=False))
        except (OSError, NotImplementedError):
            issues.append("Unable to enumerate mounted filesystems.")
            return (), False
        except TypeError:
            issues.append("Malformed mounted filesystem data.")
            return (), False

        records: list[_MountRecord] = []
        for raw_mount in raw_mounts:
            mount_point = getattr(raw_mount, "mountpoint", None)
            if not isinstance(mount_point, str) or not mount_point.strip():
                issues.append("Malformed mounted filesystem data.")
                continue
            raw_source = getattr(raw_mount, "device", None)
            source = _clean_text(raw_source) if isinstance(raw_source, str) else None
            if raw_source is not None and not isinstance(raw_source, str):
                issues.append("Malformed mounted filesystem data.")
            raw_filesystem = getattr(raw_mount, "fstype", None)
            filesystem = (
                _clean_text(raw_filesystem)
                if isinstance(raw_filesystem, str)
                else None
            )
            if raw_filesystem is not None and not isinstance(raw_filesystem, str):
                issues.append("Malformed mounted filesystem data.")

            total, used, available = self._read_usage(
                mount_point, source, filesystem, issues
            )
            info = MountInfo(
                mount_point=mount_point,
                source=source,
                filesystem=filesystem,
                total_bytes=total,
                used_bytes=used,
                available_bytes=available,
            )
            records.append(
                _MountRecord(info=info)
            )

        return (
            tuple(
                sorted(
                    records,
                    key=lambda record: (
                        record.info.mount_point,
                        record.info.source or "",
                        record.info.filesystem or "",
                    ),
                )
            ),
            True,
        )

    def _read_usage(
        self,
        mount_point: str,
        source: str | None,
        filesystem: str | None,
        issues: list[str],
    ) -> tuple[int | None, int | None, int | None]:
        normalized_filesystem = filesystem.casefold() if filesystem else ""
        remote_or_deferred = (
            normalized_filesystem == "autofs"
            or normalized_filesystem.startswith(("fuse.", "nfs", "cifs", "smb"))
            or (source is not None and (source.startswith("//") or ":" in source))
        )
        if remote_or_deferred:
            return None, None, None
        try:
            usage = self._disk_usage(mount_point)
        except (OSError, NotImplementedError):
            issues.append(f"Unable to read filesystem usage for {mount_point}.")
            return None, None, None

        values: list[int | None] = []
        malformed = False
        for attribute in ("total", "used", "free"):
            value = getattr(usage, attribute, None)
            if type(value) is not int or value < 0:
                values.append(None)
                malformed = True
            else:
                values.append(value)
        total, used, available = values
        if total is not None:
            if used is not None and used > total:
                used = None
                malformed = True
            if available is not None and available > total:
                available = None
                malformed = True
            if (
                used is not None
                and available is not None
                and used + available > total
            ):
                used = None
                available = None
                malformed = True
        if malformed:
            issues.append(f"Malformed filesystem usage for {mount_point}.")
        return total, used, available

    def _read_raw(
        self, path: Path, device_name: str, issues: list[str]
    ) -> str | None:
        try:
            return self._read_text(path)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError):
            issues.append(f"Unable to read storage attribute for {device_name}.")
            return None

    def _read_string(
        self, path: Path, device_name: str, issues: list[str]
    ) -> str | None:
        return _clean_text(self._read_raw(path, device_name, issues))

    def _read_first_string(
        self, paths: Iterable[Path], device_name: str, issues: list[str]
    ) -> str | None:
        for path in paths:
            value = self._read_string(path, device_name, issues)
            if value is not None:
                return value
        return None

    def _read_integer(
        self, path: Path, device_name: str, issues: list[str]
    ) -> int | None:
        raw_value = self._read_raw(path, device_name, issues)
        if raw_value is None:
            return None
        try:
            value = int(raw_value.strip())
        except ValueError:
            issues.append(f"Malformed storage attribute for {device_name}.")
            return None
        if value < 0:
            issues.append(f"Malformed storage attribute for {device_name}.")
            return None
        return value

    def _read_positive_int(
        self, path: Path, device_name: str, issues: list[str]
    ) -> int | None:
        value = self._read_integer(path, device_name, issues)
        if value == 0:
            issues.append(f"Malformed storage attribute for {device_name}.")
            return None
        return value

    def _read_sectors(
        self, path: Path, device_name: str, issues: list[str]
    ) -> int | None:
        sectors = self._read_integer(path, device_name, issues)
        return sectors * 512 if sectors is not None else None

    def _read_bool(
        self, path: Path, device_name: str, issues: list[str]
    ) -> bool | None:
        value = self._read_integer(path, device_name, issues)
        if value not in {None, 0, 1}:
            issues.append(f"Malformed storage attribute for {device_name}.")
            return None
        return bool(value) if value is not None else None

    def _resolved_name(
        self, path: Path, device_name: str, issues: list[str]
    ) -> str | None:
        try:
            return _clean_text(path.resolve(strict=True).name)
        except FileNotFoundError:
            return None
        except (OSError, RuntimeError):
            issues.append(f"Unable to inspect storage subsystem for {device_name}.")
            return None

    def _detect_transport(
        self,
        path: Path,
        identity: Path,
        subsystem: str | None,
        device_name: str,
        issues: list[str],
    ) -> str | None:
        normalized_subsystem = subsystem.casefold() if subsystem is not None else None
        if normalized_subsystem in {"nvme", "mmc", "usb", "virtio"}:
            return normalized_subsystem
        explicit = self._read_string(path / "device/transport", device_name, issues)
        protocol = self._read_string(path / "device/protocol", device_name, issues)
        evidence = explicit or protocol
        if evidence is not None:
            normalized = evidence.casefold()
            if normalized in {"ata", "serial ata", "sata"}:
                return "sata"
            return normalized
        ancestor_subsystems: set[str] = set()
        for ancestor in (identity, *identity.parents):
            try:
                ancestor_subsystem = (ancestor / "subsystem").resolve(
                    strict=True
                ).name.casefold()
            except FileNotFoundError:
                continue
            except (OSError, RuntimeError):
                issues.append(
                    f"Unable to inspect storage bus ancestry for {device_name}."
                )
                continue
            ancestor_subsystems.add(ancestor_subsystem)
        for bus in ("nvme", "mmc", "usb", "virtio", "ata", "scsi"):
            if bus in ancestor_subsystems:
                return "sata" if bus == "ata" else bus
        return None

    @staticmethod
    def _classify(
        *,
        identity: Path,
        rotational: bool | None,
        removable: bool | None,
        subsystem: str | None,
        device_type: str | None,
        transport: str | None,
    ) -> BlockDeviceKind | None:
        if _is_virtual(identity):
            return "virtual"
        if transport == "virtio":
            return "virtual"
        normalized_subsystem = subsystem.casefold() if subsystem is not None else None
        if normalized_subsystem == "nvme":
            return "nvme"
        if normalized_subsystem == "mmc" and device_type is not None:
            if device_type.casefold() == "mmc":
                return "emmc"
        if removable is True:
            return "removable"
        if transport not in {"sata", "sas", "scsi"}:
            return None
        if device_type is not None and device_type.casefold() not in {"0", "disk"}:
            return None
        if rotational is True:
            return "hdd"
        if rotational is False:
            return "ssd"
        return None
