"""Typed immutable models for the CPU load-test engine."""

from dataclasses import dataclass
from enum import Enum
import math
import re
from typing import Literal, TypeAlias


class CpuTestStatus(str, Enum):
    """Public outcomes independent from inventory detection states."""

    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TEMPERATURE_LIMIT = "temperature_limit"
    ERROR = "error"


TemperatureSource: TypeAlias = Literal["psutil", "thermal"]

_DURATION = re.compile(r"([1-9][0-9]*)([smh])")
_UNSIGNED_INTEGER = re.compile(r"[0-9]+")


def _finite_number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    try:
        normalized = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be finite") from error
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    return normalized


def _optional_finite(name: str, value: float | None, *, positive: bool = False) -> None:
    if value is None:
        return
    normalized = _finite_number(name, value)
    if positive and normalized <= 0:
        raise ValueError(f"{name} must be positive or None")


def _non_empty_string(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def parse_duration(value: str) -> int:
    """Parse the normative integer duration syntax into seconds."""

    if not isinstance(value, str):
        raise TypeError("duration must be a string")
    match = _DURATION.fullmatch(value)
    if match is None:
        raise ValueError("duration must be an integer followed by s, m, or h")
    multiplier = {"s": 1, "m": 60, "h": 3600}[match.group(2)]
    seconds = int(match.group(1)) * multiplier
    if not 10 <= seconds <= 86_400:
        raise ValueError("duration must be between 10 seconds and 24 hours")
    return seconds


def normalize_load(value: int | str) -> int:
    """Normalize a requested worker duty-cycle percentage."""

    if isinstance(value, str):
        if _UNSIGNED_INTEGER.fullmatch(value) is None:
            raise ValueError("load must be an integer")
        normalized = int(value)
    elif type(value) is int:
        normalized = value
    else:
        raise TypeError("load must be an integer")
    if not 10 <= normalized <= 90:
        raise ValueError("load must be between 10 and 90")
    return normalized


def normalize_temperature(value: int | float | str) -> float:
    """Normalize the configurable thermal safeguard in degrees Celsius."""

    if isinstance(value, str):
        try:
            normalized = float(value)
        except (ValueError, OverflowError) as error:
            raise ValueError("maximum temperature must be a finite number") from error
        if not value:
            raise ValueError("maximum temperature must be a finite number")
    else:
        normalized = _finite_number("maximum temperature", value)
    if not math.isfinite(normalized) or not 50 <= normalized <= 100:
        raise ValueError("maximum temperature must be between 50 and 100")
    return normalized


@dataclass(frozen=True, slots=True)
class CpuTestConfig:
    duration_seconds: int = 600
    load_percent: int = 60
    maximum_temperature_celsius: float = 85.0
    allow_no_temperature: bool = False
    verbose: bool = False

    def __post_init__(self) -> None:
        if type(self.duration_seconds) is not int or not 10 <= self.duration_seconds <= 86_400:
            raise ValueError("duration_seconds must be between 10 and 86400")
        if type(self.load_percent) is not int or not 10 <= self.load_percent <= 90:
            raise ValueError("load_percent must be between 10 and 90")
        temperature = _finite_number(
            "maximum_temperature_celsius", self.maximum_temperature_celsius
        )
        if not 50 <= temperature <= 100:
            raise ValueError(
                "maximum_temperature_celsius must be between 50 and 100"
            )
        if type(self.allow_no_temperature) is not bool:
            raise TypeError("allow_no_temperature must be bool")
        if type(self.verbose) is not bool:
            raise TypeError("verbose must be bool")

    @classmethod
    def from_options(
        cls,
        *,
        duration: str = "10m",
        load: int | str = 60,
        maximum_temperature: int | float | str = 85,
        allow_no_temperature: bool = False,
        verbose: bool = False,
    ) -> "CpuTestConfig":
        return cls(
            duration_seconds=parse_duration(duration),
            load_percent=normalize_load(load),
            maximum_temperature_celsius=normalize_temperature(
                maximum_temperature
            ),
            allow_no_temperature=allow_no_temperature,
            verbose=verbose,
        )


@dataclass(frozen=True, slots=True)
class TemperatureReading:
    identity: str
    source: TemperatureSource
    chip_name: str | None
    label: str | None
    current_celsius: float
    critical_celsius: float | None = None
    cpu_attributed: bool = False

    def __post_init__(self) -> None:
        _non_empty_string("identity", self.identity)
        if self.source not in {"psutil", "thermal"}:
            raise ValueError("source must be psutil or thermal")
        for name in ("chip_name", "label"):
            value = getattr(self, name)
            if value is not None:
                _non_empty_string(name, value)
        _finite_number("current_celsius", self.current_celsius)
        _optional_finite("critical_celsius", self.critical_celsius)
        if type(self.cpu_attributed) is not bool:
            raise TypeError("cpu_attributed must be bool")


@dataclass(frozen=True, slots=True)
class MonitoredTemperature:
    identity: str
    chip_name: str | None
    label: str | None
    current_celsius: float
    effective_limit_celsius: float
    cpu_attributed: bool = False

    def __post_init__(self) -> None:
        _non_empty_string("identity", self.identity)
        for name in ("chip_name", "label"):
            value = getattr(self, name)
            if value is not None:
                _non_empty_string(name, value)
        _finite_number("current_celsius", self.current_celsius)
        _finite_number("effective_limit_celsius", self.effective_limit_celsius)
        if type(self.cpu_attributed) is not bool:
            raise TypeError("cpu_attributed must be bool")


@dataclass(frozen=True, slots=True)
class TemperatureStatistics:
    identity: str
    chip_name: str | None
    label: str | None
    effective_limit_celsius: float
    minimum_celsius: float
    maximum_celsius: float
    last_celsius: float
    cpu_attributed: bool = False

    def __post_init__(self) -> None:
        _non_empty_string("identity", self.identity)
        for name in ("chip_name", "label"):
            value = getattr(self, name)
            if value is not None:
                _non_empty_string(name, value)
        values = (
            self.effective_limit_celsius,
            self.minimum_celsius,
            self.maximum_celsius,
            self.last_celsius,
        )
        for value in values:
            _finite_number("temperature statistic", value)
        if self.minimum_celsius > self.maximum_celsius:
            raise ValueError("minimum_celsius cannot exceed maximum_celsius")
        if not self.minimum_celsius <= self.last_celsius <= self.maximum_celsius:
            raise ValueError("last_celsius must be within the observed range")
        if type(self.cpu_attributed) is not bool:
            raise TypeError("cpu_attributed must be bool")


@dataclass(frozen=True, slots=True)
class CpuTestSample:
    elapsed_seconds: float
    total_utilization_percent: float
    per_processor_utilization_percent: tuple[float, ...] | None = None
    aggregate_frequency_hz: float | None = None
    per_processor_frequency_hz: tuple[float | None, ...] | None = None
    temperatures: tuple[MonitoredTemperature, ...] = ()
    workers_alive: int = 0
    worker_count: int = 0

    def __post_init__(self) -> None:
        elapsed = _finite_number("elapsed_seconds", self.elapsed_seconds)
        utilization = _finite_number(
            "total_utilization_percent", self.total_utilization_percent
        )
        if elapsed < 0:
            raise ValueError("elapsed_seconds must be non-negative")
        if not 0 <= utilization <= 100:
            raise ValueError("total_utilization_percent must be between 0 and 100")
        if self.per_processor_utilization_percent is not None:
            if not isinstance(self.per_processor_utilization_percent, tuple):
                raise TypeError("per_processor_utilization_percent must be a tuple")
            for value in self.per_processor_utilization_percent:
                normalized = _finite_number("per-processor utilization", value)
                if not 0 <= normalized <= 100:
                    raise ValueError("per-processor utilization must be 0 through 100")
        _optional_finite(
            "aggregate_frequency_hz", self.aggregate_frequency_hz, positive=True
        )
        if self.per_processor_frequency_hz is not None:
            if not isinstance(self.per_processor_frequency_hz, tuple):
                raise TypeError("per_processor_frequency_hz must be a tuple")
            for value in self.per_processor_frequency_hz:
                _optional_finite("per-processor frequency", value, positive=True)
        if not isinstance(self.temperatures, tuple) or not all(
            isinstance(item, MonitoredTemperature) for item in self.temperatures
        ):
            raise TypeError("temperatures must be a tuple of MonitoredTemperature")
        for name in ("workers_alive", "worker_count"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.workers_alive > self.worker_count:
            raise ValueError("workers_alive cannot exceed worker_count")


@dataclass(frozen=True, slots=True)
class CpuTestReady:
    worker_count: int
    thermal_monitoring_active: bool
    temperatures: tuple[MonitoredTemperature, ...]

    def __post_init__(self) -> None:
        if type(self.worker_count) is not int or self.worker_count <= 0:
            raise ValueError("worker_count must be positive")
        if type(self.thermal_monitoring_active) is not bool:
            raise TypeError("thermal_monitoring_active must be bool")
        if not isinstance(self.temperatures, tuple) or not all(
            isinstance(item, MonitoredTemperature) for item in self.temperatures
        ):
            raise TypeError("temperatures must be a tuple of MonitoredTemperature")


@dataclass(frozen=True, slots=True)
class CpuTestResult:
    status: CpuTestStatus
    reason: str
    requested_duration_seconds: int
    actual_duration_seconds: float
    requested_load_percent: int
    worker_count: int
    sample_count: int
    utilization_average_percent: float | None
    utilization_minimum_percent: float | None
    utilization_maximum_percent: float | None
    initial_temperature_celsius: float | None
    maximum_temperature_celsius: float | None
    initial_frequency_hz: float | None
    minimum_frequency_hz: float | None
    maximum_frequency_hz: float | None
    warnings: tuple[str, ...] = ()
    temperature_statistics: tuple[TemperatureStatistics, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, CpuTestStatus):
            raise TypeError("status must be CpuTestStatus")
        _non_empty_string("reason", self.reason)
        if type(self.requested_duration_seconds) is not int or self.requested_duration_seconds <= 0:
            raise ValueError("requested_duration_seconds must be positive")
        actual = _finite_number("actual_duration_seconds", self.actual_duration_seconds)
        if actual < 0:
            raise ValueError("actual_duration_seconds must be non-negative")
        if type(self.requested_load_percent) is not int or not 10 <= self.requested_load_percent <= 90:
            raise ValueError("requested_load_percent must be between 10 and 90")
        for name in ("worker_count", "sample_count"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in (
            "utilization_average_percent",
            "utilization_minimum_percent",
            "utilization_maximum_percent",
            "initial_temperature_celsius",
            "maximum_temperature_celsius",
        ):
            _optional_finite(name, getattr(self, name))
        for name in (
            "initial_frequency_hz",
            "minimum_frequency_hz",
            "maximum_frequency_hz",
        ):
            _optional_finite(name, getattr(self, name), positive=True)
        utilization_values = (
            self.utilization_average_percent,
            self.utilization_minimum_percent,
            self.utilization_maximum_percent,
        )
        if self.sample_count == 0 and any(value is not None for value in utilization_values):
            raise ValueError("utilization statistics require at least one sample")
        if self.sample_count > 0 and any(value is None for value in utilization_values):
            raise ValueError("utilization statistics are required when samples exist")
        if any(
            value is not None and not 0 <= value <= 100
            for value in utilization_values
        ):
            raise ValueError("utilization statistics must be between 0 and 100")
        if (
            self.utilization_minimum_percent is not None
            and self.utilization_average_percent is not None
            and self.utilization_maximum_percent is not None
            and not self.utilization_minimum_percent
            <= self.utilization_average_percent
            <= self.utilization_maximum_percent
        ):
            raise ValueError("utilization statistics are inconsistent")
        if not isinstance(self.warnings, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in self.warnings
        ):
            raise TypeError("warnings must be a tuple of non-empty strings")
        if not isinstance(self.temperature_statistics, tuple) or not all(
            isinstance(item, TemperatureStatistics)
            for item in self.temperature_statistics
        ):
            raise TypeError(
                "temperature_statistics must be a tuple of TemperatureStatistics"
            )
