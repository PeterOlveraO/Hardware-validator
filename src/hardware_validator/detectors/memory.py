"""Informational Linux RAM, swap, and EDAC module detection."""

from collections.abc import Callable, Iterable
from pathlib import Path
import re

import psutil

from ..collector import DetectionResult
from ..models import MemoryInfo
from ..models import MemoryModuleInfo
from ..models import SwapInfo

MemoryReader = Callable[[], object]
TextReader = Callable[[Path], str]
DirectoryLister = Callable[[Path], Iterable[Path]]

_MC_DIRECTORY = re.compile(r"mc[0-9]+")
_DIMM_DIRECTORY = re.compile(r"dimm[0-9]+")
_MIB = 1024**2
_UNKNOWN_TEXT = {"unknown", "not specified"}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _list_directory(path: Path) -> Iterable[Path]:
    return path.iterdir()


def _clean_text(value: str) -> str | None:
    cleaned = value.strip("\x00 \t\r\n")
    if not cleaned or cleaned.casefold() in _UNKNOWN_TEXT:
        return None
    return cleaned


def _integer_attribute(value: object, name: str) -> int | None:
    attribute = getattr(value, name, None)
    if type(attribute) is not int:
        return None
    return attribute


class MemoryDetector:
    """Read current memory capacities without allocating or measuring load."""

    def __init__(
        self,
        *,
        virtual_memory: MemoryReader = psutil.virtual_memory,
        swap_memory: MemoryReader = psutil.swap_memory,
        edac_root: Path = Path("/sys/devices/system/edac/mc"),
        read_text: TextReader = _read_text,
        list_directory: DirectoryLister = _list_directory,
    ) -> None:
        self._virtual_memory = virtual_memory
        self._swap_memory = swap_memory
        self._edac_root = edac_root
        self._read_text = read_text
        self._list_directory = list_directory

    def detect(self) -> DetectionResult[MemoryInfo]:
        """Collect one observation; RAM used is total minus available."""

        issues: list[str] = []
        total, available, used, free, ram_available = self._detect_ram(issues)
        swap, swap_available = self._detect_swap(issues)
        modules = self._detect_modules(issues)

        value = MemoryInfo(
            total_bytes=total,
            available_bytes=available,
            used_bytes=used,
            free_bytes=free,
            modules=modules,
            swap=swap,
        )
        useful = ram_available or swap_available or bool(modules)
        unique_issues = tuple(dict.fromkeys(issues))
        if unique_issues:
            if useful:
                return DetectionResult.partial(value, *unique_issues)
            return DetectionResult.unavailable(value, *unique_issues)
        if useful:
            return DetectionResult.complete(value)
        return DetectionResult.unavailable(value, "No memory information was available.")

    def _detect_ram(
        self, issues: list[str]
    ) -> tuple[int | None, int | None, int | None, int | None, bool]:
        try:
            observation = self._virtual_memory()
        except (OSError, NotImplementedError):
            issues.append("Unable to query RAM information.")
            return None, None, None, None, False

        total = _integer_attribute(observation, "total")
        available = _integer_attribute(observation, "available")
        free = _integer_attribute(observation, "free")
        if (
            total is None
            or available is None
            or free is None
            or total <= 0
            or not 0 <= available <= total
            or not 0 <= free <= total
        ):
            issues.append("Malformed RAM information.")
            return None, None, None, None, False

        # psutil's used field is platform-dependent; the model uses total - available.
        return total, available, total - available, free, True

    def _detect_swap(
        self, issues: list[str]
    ) -> tuple[SwapInfo, bool]:
        try:
            observation = self._swap_memory()
        except (OSError, NotImplementedError):
            issues.append("Unable to query swap information.")
            return SwapInfo(), False

        total = _integer_attribute(observation, "total")
        free = _integer_attribute(observation, "free")
        used = _integer_attribute(observation, "used")
        if (
            total is None
            or free is None
            or used is None
            or total < 0
            or not 0 <= free <= total
            or not 0 <= used <= total
            or used != total - free
        ):
            issues.append("Malformed swap information.")
            return SwapInfo(), False

        return SwapInfo(
            total_bytes=total,
            available_bytes=free,
            used_bytes=used,
        ), True

    def _detect_modules(self, issues: list[str]) -> tuple[MemoryModuleInfo, ...]:
        controllers = self._directories(
            self._edac_root,
            _MC_DIRECTORY,
            "Unable to enumerate EDAC memory controllers.",
            issues,
        )
        modules: list[MemoryModuleInfo] = []
        for controller in controllers:
            dimms = self._directories(
                controller,
                _DIMM_DIRECTORY,
                "Unable to enumerate EDAC memory modules.",
                issues,
            )
            for dimm in dimms:
                module = self._read_module(dimm, issues)
                if module is not None:
                    modules.append(module)
        return tuple(modules)

    def _directories(
        self,
        root: Path,
        pattern: re.Pattern[str],
        issue: str,
        issues: list[str],
    ) -> tuple[Path, ...]:
        try:
            entries = self._list_directory(root)
            return tuple(
                sorted(
                    (
                        entry
                        for entry in entries
                        if pattern.fullmatch(entry.name) and entry.is_dir()
                    ),
                    key=lambda entry: str(entry),
                )
            )
        except FileNotFoundError:
            return ()
        except OSError:
            issues.append(issue)
            return ()

    def _read_module(
        self, root: Path, issues: list[str]
    ) -> MemoryModuleInfo | None:
        locator = self._read_optional_text(root / "dimm_location", issues)
        if locator is None:
            locator = self._read_optional_text(root / "dimm_label", issues)
        memory_type = self._read_optional_text(root / "dimm_mem_type", issues)

        size_bytes = None
        try:
            size_text = self._read_text(root / "size").strip()
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError):
            issues.append("Unable to read EDAC memory module data.")
        else:
            try:
                size_mib = int(size_text)
            except ValueError:
                issues.append("Malformed EDAC memory module size.")
            else:
                if size_mib < 0 or str(size_mib) != size_text:
                    issues.append("Malformed EDAC memory module size.")
                else:
                    # The Linux EDAC ABI documents the size file in MiB.
                    size_bytes = size_mib * _MIB

        return MemoryModuleInfo(
            locator=locator,
            manufacturer=None,
            part_number=None,
            serial_number=None,
            size_bytes=size_bytes,
            memory_type=memory_type,
            speed_mt_s=None,
        )

    def _read_optional_text(self, path: Path, issues: list[str]) -> str | None:
        try:
            return _clean_text(self._read_text(path))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError):
            issues.append("Unable to read EDAC memory module data.")
            return None
