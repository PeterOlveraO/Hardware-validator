"""Non-invasive Linux GPU inventory from DRM and PCI sysfs data."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import re

from ..collector import DetectionResult
from ..models import AdapterType
from ..models import GpuInfo
from ..models import GpuInventory

TextReader = Callable[[Path], str]
PathResolver = Callable[[Path], Path]

_DRM_CARD = re.compile(r"card[0-9]+")
_DRM_CONNECTOR = re.compile(r"(card[0-9]+)-.+")
_PCI_ADDRESS = re.compile(
    r"(?:[0-9a-fA-F]{4}:)?[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]"
)
_PCI_CLASS = re.compile(r"0x([0-9a-fA-F]{6})")
_PCI_ID = re.compile(r"(?:0[xX])?([0-9a-fA-F]{4})")
_NON_NEGATIVE_INTEGER = re.compile(r"[0-9]+")
_BOARD_INFO = re.compile(r"type\s*:\s*(cem|oam|unknown)", re.IGNORECASE)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _resolve_path(path: Path) -> Path:
    return path.resolve(strict=True)


@dataclass(slots=True)
class _DeviceRecord:
    """Internal identity and source paths merged before attributes are read."""

    device_path: Path | None
    connectors: set[str] = field(default_factory=set)


class GpuDetector:
    """Read GPU identity from standard Linux DRM and PCI sysfs files."""

    def __init__(
        self,
        *,
        drm_root: Path = Path("/sys/class/drm"),
        pci_root: Path = Path("/sys/bus/pci/devices"),
        read_text: TextReader = _read_text,
        resolve_path: PathResolver = _resolve_path,
    ) -> None:
        self._drm_root = drm_root
        self._pci_root = pci_root
        self._read_text = read_text
        self._resolve_path = resolve_path

    def detect(self) -> DetectionResult[GpuInventory]:
        """Collect DRM cards and PCI display devices without running tools."""

        issues: list[str] = []
        records: dict[str, _DeviceRecord] = {}
        drm_entries, drm_available = self._list_directory(
            self._drm_root, "DRM devices", issues
        )

        for entry in drm_entries:
            if _DRM_CARD.fullmatch(entry.name) is None:
                continue
            device_path = self._backing_device(entry, issues)
            if device_path is None:
                issues.append("DRM card has no canonical backing device.")
                continue
            key = self._identity_key(device_path, entry)
            record = records.setdefault(key, _DeviceRecord(device_path))
            if record.device_path is None and device_path is not None:
                record.device_path = device_path

        pci_entries, pci_available = self._list_directory(
            self._pci_root, "PCI devices", issues
        )
        for entry in pci_entries:
            class_text = self._read_optional(entry / "class", "PCI class", issues)
            if class_text is None:
                continue
            class_match = _PCI_CLASS.fullmatch(class_text.strip())
            if class_match is None:
                issues.append("Malformed PCI class value.")
                continue
            if not class_match.group(1).lower().startswith("03"):
                continue
            device_path = self._resolve(entry, "PCI device path", issues)
            if device_path is None:
                continue
            records.setdefault(str(device_path), _DeviceRecord(device_path))

        for entry in drm_entries:
            if _DRM_CONNECTOR.fullmatch(entry.name) is None:
                continue
            device_path = self._backing_device(entry, issues)
            if device_path is None:
                continue
            record = records.get(str(device_path))
            if record is not None:
                record.connectors.add(entry.name)

        devices = tuple(
            self._make_gpu(records[key], issues) for key in sorted(records)
        )
        value = GpuInventory(devices=devices)
        unique_issues = tuple(dict.fromkeys(issues))
        if unique_issues:
            if devices:
                return DetectionResult.partial(value, *unique_issues)
            return DetectionResult.unavailable(value, *unique_issues)
        if not drm_available and not pci_available:
            return DetectionResult.unavailable(
                value, "No GPU discovery source was available."
            )
        return DetectionResult.complete(value)

    def _list_directory(
        self, root: Path, label: str, issues: list[str]
    ) -> tuple[tuple[Path, ...], bool]:
        try:
            return tuple(sorted(root.iterdir(), key=lambda path: path.name)), True
        except FileNotFoundError:
            return (), False
        except OSError:
            issues.append(f"Unable to list {label}.")
            return (), False

    def _resolve(
        self, path: Path, label: str, issues: list[str]
    ) -> Path | None:
        try:
            return self._resolve_path(path)
        except FileNotFoundError:
            return None
        except (OSError, RuntimeError):
            issues.append(f"Unable to resolve {label}.")
            return None

    def _backing_device(self, drm_entry: Path, issues: list[str]) -> Path | None:
        device_path = self._resolve(
            drm_entry / "device", "DRM backing device", issues
        )
        if device_path is None:
            return None
        if _DRM_CARD.fullmatch(device_path.name) is not None:
            return self._resolve(
                device_path / "device", "DRM backing device", issues
            )
        return device_path

    @staticmethod
    def _identity_key(device_path: Path | None, drm_card: Path) -> str:
        if device_path is not None:
            return str(device_path)
        return f"drm:{drm_card}"

    def _read_optional(
        self, path: Path, label: str, issues: list[str]
    ) -> str | None:
        try:
            return self._read_text(path)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError):
            issues.append(f"Unable to read {label}.")
            return None

    def _make_gpu(self, record: _DeviceRecord, issues: list[str]) -> GpuInfo:
        path = record.device_path
        if path is None:
            return GpuInfo(connectors=tuple(sorted(record.connectors)))

        vendor_id = self._read_id(path / "vendor", "GPU vendor ID", issues)
        device_id = self._read_id(path / "device", "GPU device ID", issues)
        driver = self._read_driver(path, issues)

        commercial_name = None
        adapter_type: AdapterType | None = None
        if driver == "amdgpu":
            commercial_name = self._read_name(path / "product_name", issues)
            adapter_type = self._read_amdgpu_adapter_type(path, issues)

        memory_total_bytes = None
        if driver == "amdgpu":
            memory_total_bytes = self._read_non_negative_integer(
                path / "mem_info_vram_total", "GPU VRAM total", issues
            )
        is_boot_vga = self._read_boot_vga(path, issues)
        address = path.name.lower() if _PCI_ADDRESS.fullmatch(path.name) else None

        return GpuInfo(
            commercial_name=commercial_name,
            vendor_id=vendor_id,
            device_id=device_id,
            adapter_type=adapter_type,
            memory_total_bytes=memory_total_bytes,
            driver=driver,
            address=address,
            is_primary=None,
            is_boot_vga=is_boot_vga,
            connectors=tuple(sorted(record.connectors)),
        )

    def _read_id(
        self, path: Path, label: str, issues: list[str]
    ) -> str | None:
        text = self._read_optional(path, label, issues)
        if text is None:
            return None
        match = _PCI_ID.fullmatch(text.strip())
        if match is None:
            issues.append(f"Malformed {label}.")
            return None
        return f"0x{match.group(1).lower()}"

    def _read_driver(self, path: Path, issues: list[str]) -> str | None:
        driver_path = self._resolve(path / "driver", "GPU driver", issues)
        if driver_path is None:
            return None
        return driver_path.name or None

    def _read_name(self, path: Path, issues: list[str]) -> str | None:
        text = self._read_optional(path, "amdgpu product name", issues)
        if text is None:
            return None
        name = text.strip()
        if not name:
            issues.append("Malformed amdgpu product name.")
            return None
        return name

    def _read_amdgpu_adapter_type(
        self, path: Path, issues: list[str]
    ) -> AdapterType | None:
        text = self._read_optional(path / "board_info", "amdgpu board info", issues)
        if text is None:
            return None
        match = _BOARD_INFO.fullmatch(text.strip())
        if match is None:
            issues.append("Malformed amdgpu board info.")
            return None
        if match.group(1).casefold() in {"cem", "oam"}:
            return "dedicated"
        return None

    def _read_non_negative_integer(
        self, path: Path, label: str, issues: list[str]
    ) -> int | None:
        text = self._read_optional(path, label, issues)
        if text is None:
            return None
        value = text.strip()
        if _NON_NEGATIVE_INTEGER.fullmatch(value) is None:
            issues.append(f"Malformed {label}.")
            return None
        return int(value)

    def _read_boot_vga(self, path: Path, issues: list[str]) -> bool | None:
        text = self._read_optional(path / "boot_vga", "GPU boot_vga", issues)
        if text is None:
            return None
        value = text.strip()
        if value == "0":
            return False
        if value == "1":
            return True
        issues.append("Malformed GPU boot_vga.")
        return None
