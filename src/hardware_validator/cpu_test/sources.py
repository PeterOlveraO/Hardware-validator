"""Injectable psutil CPU utilization and frequency source."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import math

import psutil


class CpuSourceError(RuntimeError):
    """Raised when a required CPU measurement is unavailable or malformed."""


@dataclass(frozen=True, slots=True)
class CpuMetricSample:
    total_utilization_percent: float
    per_processor_utilization_percent: tuple[float, ...] | None
    aggregate_frequency_hz: float | None
    per_processor_frequency_hz: tuple[float | None, ...] | None

    def __post_init__(self) -> None:
        total = _percentage(self.total_utilization_percent)
        if total is None:
            raise CpuSourceError("Total CPU utilization measurement is invalid.")
        if self.per_processor_utilization_percent is not None:
            if not isinstance(self.per_processor_utilization_percent, tuple) or any(
                _percentage(value) is None
                for value in self.per_processor_utilization_percent
            ):
                raise CpuSourceError("Per-processor utilization data is invalid.")
        if self.aggregate_frequency_hz is not None and (
            _positive_number(self.aggregate_frequency_hz) is None
        ):
            raise CpuSourceError("Aggregate frequency data is invalid.")
        if self.per_processor_frequency_hz is not None:
            if not isinstance(self.per_processor_frequency_hz, tuple) or any(
                value is not None and _positive_number(value) is None
                for value in self.per_processor_frequency_hz
            ):
                raise CpuSourceError("Per-processor frequency data is invalid.")


def _percentage(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        normalized = float(value)
    except OverflowError:
        return None
    return normalized if math.isfinite(normalized) and 0 <= normalized <= 100 else None


def _positive_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        normalized = float(value)
    except OverflowError:
        return None
    return normalized if math.isfinite(normalized) and normalized > 0 else None


class PsutilCpuSource:
    """Read non-blocking CPU metrics without changing processor configuration."""

    def __init__(
        self,
        *,
        cpu_count: Callable[..., object] = psutil.cpu_count,
        cpu_percent: Callable[..., object] = psutil.cpu_percent,
        cpu_frequency: Callable[..., object] = psutil.cpu_freq,
    ) -> None:
        self._cpu_count = cpu_count
        self._cpu_percent = cpu_percent
        self._cpu_frequency = cpu_frequency

    def logical_processor_count(self) -> int:
        try:
            value = self._cpu_count(logical=True)
        except (OSError, NotImplementedError, psutil.Error) as error:
            raise CpuSourceError("Unable to query logical processor count.") from error
        if type(value) is not int or value <= 0:
            raise CpuSourceError("Logical processor count is unavailable or invalid.")
        return value

    def prime_utilization(self, *, per_processor: bool) -> None:
        """Initialize psutil's non-blocking baseline and discard its values."""

        try:
            self._cpu_percent(interval=None, percpu=False)
            if per_processor:
                self._cpu_percent(interval=None, percpu=True)
        except (OSError, NotImplementedError, psutil.Error) as error:
            raise CpuSourceError("Unable to initialize CPU utilization measurement.") from error

    def read_frequency(
        self,
    ) -> tuple[float | None, tuple[float | None, ...] | None]:
        try:
            raw_entries = self._cpu_frequency(percpu=True)
        except Exception:
            return None, None
        if raw_entries is None or isinstance(raw_entries, (str, bytes)):
            return None, None
        try:
            entries: Iterable[object] = tuple(raw_entries)  # type: ignore[arg-type]
        except Exception:
            return None, None

        values: list[float | None] = []
        usable: list[float] = []
        for entry in entries:
            try:
                current_mhz = _positive_number(getattr(entry, "current", None))
                current_hz = (
                    current_mhz * 1_000_000
                    if current_mhz is not None
                    else None
                )
            except Exception:
                current_hz = None
            if current_hz is not None and not math.isfinite(current_hz):
                current_hz = None
            values.append(current_hz)
            if current_hz is not None:
                usable.append(current_hz)
        if not values:
            return None, ()
        try:
            aggregate = math.fsum(usable) / len(usable) if usable else None
        except OverflowError:
            aggregate = None
        return aggregate, tuple(values)

    def sample(self, *, per_processor: bool) -> CpuMetricSample:
        try:
            raw_total = self._cpu_percent(interval=None, percpu=False)
        except (OSError, NotImplementedError, psutil.Error) as error:
            raise CpuSourceError("Unable to measure total CPU utilization.") from error
        total = _percentage(raw_total)
        if total is None:
            raise CpuSourceError("Total CPU utilization measurement is invalid.")

        per_cpu: tuple[float, ...] | None = None
        if per_processor:
            try:
                raw_per_cpu = self._cpu_percent(interval=None, percpu=True)
                values = tuple(raw_per_cpu)  # type: ignore[arg-type]
            except (OSError, NotImplementedError, psutil.Error, TypeError):
                values = ()
            parsed = tuple(_percentage(value) for value in values)
            if parsed and all(value is not None for value in parsed):
                per_cpu = tuple(value for value in parsed if value is not None)

        aggregate_frequency, per_cpu_frequency = self.read_frequency()
        return CpuMetricSample(
            total_utilization_percent=total,
            per_processor_utilization_percent=per_cpu,
            aggregate_frequency_hz=aggregate_frequency,
            per_processor_frequency_hz=per_cpu_frequency,
        )
