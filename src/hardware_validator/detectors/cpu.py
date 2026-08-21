"""Informational Linux CPU detection."""

from collections.abc import Callable
import math
import os
from pathlib import Path
import re

import psutil

from ..collector import DetectionResult
from ..models import CpuCacheInfo
from ..models import CpuInfo

TextReader = Callable[[Path], str]
CpuCount = Callable[..., int | None]
CpuFrequency = Callable[..., object]

_CPU_DIRECTORY = re.compile(r"cpu([0-9]+)")
_CACHE_DIRECTORY = re.compile(r"index([0-9]+)")
_CACHE_SIZE = re.compile(r"([0-9]+)([KMG]?)", re.IGNORECASE)
_CACHE_KINDS = {
    "data": "data",
    "instruction": "instruction",
    "unified": "unified",
}
_CACHE_KIND_ORDER = {"data": 0, "instruction": 1, "unified": 2}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.casefold() in {"unknown", "not specified"}:
        return None
    return cleaned


def _append_distinct(values: list[str], candidates: list[str]) -> None:
    seen = set(values)
    for candidate in candidates:
        if candidate not in seen:
            values.append(candidate)
            seen.add(candidate)


def _parse_cpuinfo(
    content: str,
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    bool,
]:
    model_names: list[str] = []
    vendor_ids: list[str] = []
    capabilities: list[str] = []
    malformed = False

    for raw_block in re.split(r"\n\s*\n", content.strip()):
        fields: dict[str, str] = {}
        for raw_line in raw_block.splitlines():
            if ":" not in raw_line:
                if raw_line.strip():
                    malformed = True
                continue
            key, value = raw_line.split(":", 1)
            key = key.strip().casefold()
            if not key:
                malformed = True
                continue
            fields[key] = value.strip()

        model = _clean_text(fields.get("model name"))
        processor = _clean_text(fields.get("processor"))
        if model is None and processor is not None and not processor.isdecimal():
            model = processor
        if model is None:
            model = _clean_text(fields.get("cpu part"))
        vendor = _clean_text(fields.get("vendor_id"))
        if vendor is None:
            vendor = _clean_text(fields.get("cpu implementer"))
        if model is not None:
            _append_distinct(model_names, [model])
        if vendor is not None:
            _append_distinct(vendor_ids, [vendor])
        feature_text = fields.get("flags") or fields.get("features")
        if feature_text:
            _append_distinct(capabilities, feature_text.split())

    return (
        tuple(model_names),
        tuple(vendor_ids),
        tuple(capabilities),
        malformed,
    )


def _parse_cpu_list(value: str) -> tuple[int, ...] | None:
    cpu_ids: set[int] = set()
    try:
        for section in value.strip().split(","):
            if not section:
                return None
            if "-" in section:
                first_text, last_text = section.split("-", 1)
                first = int(first_text)
                last = int(last_text)
                if first < 0 or last < first:
                    return None
                cpu_ids.update(range(first, last + 1))
            else:
                cpu_id = int(section)
                if cpu_id < 0:
                    return None
                cpu_ids.add(cpu_id)
    except ValueError:
        return None
    return tuple(sorted(cpu_ids)) if cpu_ids else None


def _parse_cache_size(value: str) -> int | None:
    match = _CACHE_SIZE.fullmatch(value.strip())
    if match is None:
        return None
    multiplier = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}
    return int(match.group(1)) * multiplier[match.group(2).upper()]


class CpuDetector:
    """Read CPU identity and topology without measuring CPU performance."""

    def __init__(
        self,
        *,
        cpuinfo_path: Path = Path("/proc/cpuinfo"),
        sys_cpu_root: Path = Path("/sys/devices/system/cpu"),
        uname: Callable[[], os.uname_result] = os.uname,
        cpu_count: CpuCount = psutil.cpu_count,
        cpu_frequency: CpuFrequency = psutil.cpu_freq,
        read_text: TextReader = _read_text,
    ) -> None:
        self._cpuinfo_path = cpuinfo_path
        self._sys_cpu_root = sys_cpu_root
        self._uname = uname
        self._cpu_count = cpu_count
        self._cpu_frequency = cpu_frequency
        self._read_text = read_text

    def detect(self) -> DetectionResult[CpuInfo]:
        """Collect CPU data from procfs, sysfs, uname, and psutil."""

        issues: list[str] = []

        try:
            (
                model_names,
                vendor_ids,
                capabilities,
                malformed,
            ) = _parse_cpuinfo(self._read_text(self._cpuinfo_path))
        except FileNotFoundError:
            model_names, vendor_ids, capabilities = (
                (),
                (),
                (),
            )
        except (OSError, UnicodeError):
            issues.append("Unable to read CPU information.")
            model_names, vendor_ids, capabilities = (
                (),
                (),
                (),
            )
        else:
            if malformed:
                issues.append("Malformed CPU information.")

        try:
            architecture = _clean_text(self._uname().machine)
        except OSError:
            issues.append("Unable to query CPU architecture.")
            architecture = None
        logical_processor_count = self._detect_count(True, issues)
        physical_core_count = self._detect_count(False, issues)
        physical_package_count, present_cpu_ids = self._detect_packages(issues)
        current_frequency_hz, maximum_frequency_hz = self._detect_frequencies(issues)
        caches = self._detect_caches(present_cpu_ids, issues)

        value = CpuInfo(
            model_names=model_names,
            vendor_ids=vendor_ids,
            architecture=architecture,
            physical_package_count=physical_package_count,
            physical_core_count=physical_core_count,
            logical_processor_count=logical_processor_count,
            current_frequency_hz=current_frequency_hz,
            maximum_frequency_hz=maximum_frequency_hz,
            caches=caches,
            capabilities=capabilities,
        )
        useful = any(
            (
                model_names,
                vendor_ids,
                architecture,
                physical_package_count,
                physical_core_count,
                logical_processor_count,
                current_frequency_hz,
                maximum_frequency_hz,
                caches,
                capabilities,
            )
        )
        unique_issues = tuple(dict.fromkeys(issues))
        if unique_issues:
            if useful:
                return DetectionResult.partial(value, *unique_issues)
            return DetectionResult.unavailable(value, *unique_issues)
        if useful:
            return DetectionResult.complete(value)
        return DetectionResult.unavailable(value, "No CPU information was available.")

    def _detect_count(self, logical: bool, issues: list[str]) -> int | None:
        label = "logical processor count" if logical else "physical core count"
        try:
            count = self._cpu_count(logical=logical)
        except (OSError, NotImplementedError):
            issues.append(f"Unable to query {label}.")
            return None
        if count is None:
            return None
        if type(count) is not int or count <= 0:
            issues.append(f"Invalid {label}.")
            return None
        return count

    def _detect_packages(
        self, issues: list[str]
    ) -> tuple[int | None, tuple[int, ...] | None]:
        try:
            present_text = self._read_text(self._sys_cpu_root / "present")
        except FileNotFoundError:
            return None, None
        except (OSError, UnicodeError):
            issues.append("Unable to read present CPU topology.")
            return None, None

        present_cpu_ids = _parse_cpu_list(present_text)
        if present_cpu_ids is None:
            issues.append("Malformed present CPU topology.")
            return None, None

        package_ids: set[int] = set()
        complete = True
        for cpu_id in present_cpu_ids:
            path = self._sys_cpu_root / f"cpu{cpu_id}" / "topology/physical_package_id"
            try:
                package_text = self._read_text(path).strip()
            except FileNotFoundError:
                issues.append("Incomplete physical package topology.")
                complete = False
                continue
            except (OSError, UnicodeError):
                issues.append("Unable to read physical package topology.")
                complete = False
                continue
            try:
                package_id = int(package_text)
            except ValueError:
                issues.append("Malformed physical package topology.")
                complete = False
                continue
            if package_id < 0:
                issues.append("Malformed physical package topology.")
                complete = False
                continue
            package_ids.add(package_id)

        if not complete or not package_ids:
            return None, present_cpu_ids
        return len(package_ids), present_cpu_ids

    def _detect_frequencies(
        self, issues: list[str]
    ) -> tuple[int | None, int | None]:
        try:
            entries = self._cpu_frequency(percpu=True)
        except (OSError, NotImplementedError):
            issues.append("Unable to query CPU frequencies.")
            return None, None
        if entries is None:
            return None, None
        try:
            frequency_entries = tuple(entries)
        except TypeError:
            issues.append("Malformed CPU frequency data.")
            return None, None

        current_values: list[float] = []
        maximum_values: list[float] = []
        malformed = False
        for entry in frequency_entries:
            has_frequency = False
            for attribute, values in (
                ("current", current_values),
                ("max", maximum_values),
            ):
                raw_value = getattr(entry, attribute, None)
                if raw_value is None:
                    continue
                has_frequency = True
                if (
                    isinstance(raw_value, bool)
                    or not isinstance(raw_value, (int, float))
                    or not math.isfinite(raw_value)
                ):
                    malformed = True
                    continue
                if raw_value > 0:
                    values.append(float(raw_value))
            if not has_frequency:
                malformed = True
        if malformed:
            issues.append("Malformed CPU frequency data.")

        current_hz = None
        if current_values:
            current_hz = round(math.fsum(current_values) / len(current_values) * 1_000_000)
        maximum_hz = None
        if maximum_values:
            maximum_hz = round(max(maximum_values) * 1_000_000)
        return current_hz, maximum_hz

    def _detect_caches(
        self, present_cpu_ids: tuple[int, ...] | None, issues: list[str]
    ) -> tuple[CpuCacheInfo, ...]:
        cpu_directories = self._cpu_directories(present_cpu_ids, issues)
        caches: set[CpuCacheInfo] = set()
        for cpu_directory in cpu_directories:
            cache_root = cpu_directory / "cache"
            try:
                index_directories = sorted(
                    (
                        path
                        for path in cache_root.iterdir()
                        if path.is_dir() and _CACHE_DIRECTORY.fullmatch(path.name)
                    ),
                    key=lambda path: int(path.name[5:]),
                )
            except FileNotFoundError:
                continue
            except OSError:
                issues.append("Unable to enumerate CPU caches.")
                continue
            for index_directory in index_directories:
                cache = self._read_cache(index_directory, issues)
                if cache is not None:
                    caches.add(cache)

        return tuple(
            sorted(
                caches,
                key=lambda cache: (
                    cache.level,
                    _CACHE_KIND_ORDER[cache.kind],
                    cache.size_bytes is None,
                    cache.size_bytes or 0,
                ),
            )
        )

    def _cpu_directories(
        self, present_cpu_ids: tuple[int, ...] | None, issues: list[str]
    ) -> tuple[Path, ...]:
        if present_cpu_ids is not None:
            return tuple(self._sys_cpu_root / f"cpu{cpu_id}" for cpu_id in present_cpu_ids)
        try:
            return tuple(
                sorted(
                    (
                        path
                        for path in self._sys_cpu_root.iterdir()
                        if path.is_dir() and _CPU_DIRECTORY.fullmatch(path.name)
                    ),
                    key=lambda path: int(path.name[3:]),
                )
            )
        except FileNotFoundError:
            return ()
        except OSError:
            issues.append("Unable to enumerate CPUs for cache detection.")
            return ()

    def _read_cache(
        self, index_directory: Path, issues: list[str]
    ) -> CpuCacheInfo | None:
        try:
            level_text = self._read_text(index_directory / "level").strip()
            kind_text = self._read_text(index_directory / "type").strip().casefold()
        except FileNotFoundError:
            issues.append("Incomplete CPU cache data.")
            return None
        except (OSError, UnicodeError):
            issues.append("Unable to read CPU cache data.")
            return None

        try:
            level = int(level_text)
        except ValueError:
            issues.append("Malformed CPU cache data.")
            return None
        kind = _CACHE_KINDS.get(kind_text)
        if level < 0 or kind is None:
            issues.append("Malformed CPU cache data.")
            return None

        try:
            size_text = self._read_text(index_directory / "size")
        except FileNotFoundError:
            size_bytes = None
        except (OSError, UnicodeError):
            issues.append("Unable to read CPU cache size.")
            size_bytes = None
        else:
            size_bytes = _parse_cache_size(size_text)
            if size_bytes is None:
                issues.append("Malformed CPU cache data.")
                return None
        return CpuCacheInfo(level=level, kind=kind, size_bytes=size_bytes)
