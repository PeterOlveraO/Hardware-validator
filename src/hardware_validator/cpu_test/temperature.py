"""Conservative read-only local temperature monitoring."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import math
from pathlib import Path
import re

import psutil

from .models import TemperatureReading


TextReader = Callable[[Path], str]
_THERMAL_ZONE = re.compile(r"thermal_zone[0-9]+")
_TRIP_TYPE = re.compile(r"trip_point_([0-9]+)_type")
_CPU_ZONE_TYPES = {"cpu-thermal", "cpu_thermal", "x86_pkg_temp"}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        normalized = float(value)
    except OverflowError:
        return None
    return normalized if math.isfinite(normalized) else None


def _optional_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


@dataclass(frozen=True, slots=True)
class TemperatureReadResult:
    readings: tuple[TemperatureReading, ...]
    warnings: tuple[str, ...] = ()


class LocalTemperatureMonitor:
    """Read psutil sensors, with thermal sysfs as a non-overlapping fallback."""

    def __init__(
        self,
        *,
        sensors_temperatures: Callable[..., object] = psutil.sensors_temperatures,
        thermal_root: Path = Path("/sys/class/thermal"),
        read_text: TextReader = _read_text,
    ) -> None:
        self._sensors_temperatures = sensors_temperatures
        self._thermal_root = thermal_root
        self._read_text = read_text

    def read(self) -> TemperatureReadResult:
        """Return a deterministic round without writing or running applications."""

        warnings: list[str] = []
        psutil_readings = self._read_psutil(warnings)
        readings = psutil_readings or self._read_thermal(warnings)
        return TemperatureReadResult(
            readings=tuple(sorted(readings, key=lambda item: item.identity)),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def _read_psutil(self, warnings: list[str]) -> tuple[TemperatureReading, ...]:
        try:
            raw = self._sensors_temperatures(fahrenheit=False)
        except (OSError, NotImplementedError, AttributeError, psutil.Error):
            warnings.append("Unable to read psutil temperature sensors.")
            return ()
        if not isinstance(raw, Mapping):
            warnings.append("Malformed psutil temperature sensor data.")
            return ()

        readings: list[TemperatureReading] = []
        for raw_chip in sorted(raw, key=lambda item: str(item)):
            chip = _optional_name(raw_chip)
            if chip is None:
                warnings.append("Malformed psutil temperature sensor identity.")
                continue
            raw_entries = raw[raw_chip]
            if isinstance(raw_entries, (str, bytes)):
                warnings.append(f"Malformed temperature data for {chip}.")
                continue
            try:
                entries = tuple(raw_entries)
            except TypeError:
                warnings.append(f"Malformed temperature data for {chip}.")
                continue
            label_counts: dict[str, int] = {}
            for index, entry in enumerate(entries):
                label = _optional_name(getattr(entry, "label", None))
                identity_label = label or "unlabelled"
                occurrence = label_counts.get(identity_label, 0)
                label_counts[identity_label] = occurrence + 1
                identity = f"psutil:{chip}:{identity_label}:{occurrence}"
                current = _finite(getattr(entry, "current", None))
                if current is None:
                    warnings.append(f"Invalid temperature value for {chip}.")
                    continue
                critical = _finite(getattr(entry, "critical", None))
                readings.append(
                    TemperatureReading(
                        identity=identity,
                        source="psutil",
                        chip_name=chip,
                        label=label,
                        current_celsius=current,
                        critical_celsius=critical,
                        cpu_attributed=False,
                    )
                )
        return tuple(readings)

    def _read_thermal(self, warnings: list[str]) -> tuple[TemperatureReading, ...]:
        try:
            zones = tuple(
                sorted(
                    (
                        path
                        for path in self._thermal_root.iterdir()
                        if _THERMAL_ZONE.fullmatch(path.name)
                    ),
                    key=lambda path: int(path.name[12:]),
                )
            )
        except FileNotFoundError:
            return ()
        except OSError:
            warnings.append("Unable to enumerate thermal zones.")
            return ()

        readings: list[TemperatureReading] = []
        for zone in zones:
            current = self._read_millidegrees(zone / "temp", warnings)
            if current is None:
                continue
            zone_type = self._read_optional_text(zone / "type", warnings)
            critical = self._read_critical_limit(zone, warnings)
            readings.append(
                TemperatureReading(
                    identity=f"thermal:{zone.name}",
                    source="thermal",
                    chip_name=zone_type,
                    label=zone.name,
                    current_celsius=current,
                    critical_celsius=critical,
                    cpu_attributed=(
                        zone_type.casefold() in _CPU_ZONE_TYPES
                        if zone_type is not None
                        else False
                    ),
                )
            )
        return tuple(readings)

    def _read_critical_limit(
        self, zone: Path, warnings: list[str]
    ) -> float | None:
        try:
            entries = tuple(zone.iterdir())
        except OSError:
            return None
        values: list[float] = []
        for path in sorted(entries, key=lambda item: item.name):
            match = _TRIP_TYPE.fullmatch(path.name)
            if match is None:
                continue
            trip_type = self._read_optional_text(path, warnings)
            if trip_type is None or trip_type.casefold() != "critical":
                continue
            value = self._read_millidegrees(
                zone / f"trip_point_{match.group(1)}_temp", warnings
            )
            if value is not None:
                values.append(value)
        return min(values) if values else None

    def _read_optional_text(
        self, path: Path, warnings: list[str]
    ) -> str | None:
        try:
            return _optional_name(self._read_text(path))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError):
            warnings.append(f"Unable to read temperature source {path.name}.")
            return None

    def _read_millidegrees(
        self, path: Path, warnings: list[str]
    ) -> float | None:
        try:
            text = self._read_text(path).strip()
            value = float(text) / 1000
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, ValueError, OverflowError):
            warnings.append(f"Invalid temperature source {path.name}.")
            return None
        if not math.isfinite(value):
            warnings.append(f"Invalid temperature source {path.name}.")
            return None
        return value
