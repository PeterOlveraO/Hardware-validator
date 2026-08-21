"""Incremental CPU-test statistics independent from presentation."""

from dataclasses import dataclass

from .models import MonitoredTemperature
from .models import TemperatureStatistics


@dataclass(slots=True)
class NumericAccumulator:
    count: int = 0
    total: float = 0.0
    minimum: float | None = None
    maximum: float | None = None

    def add(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    @property
    def average(self) -> float | None:
        return self.total / self.count if self.count else None


@dataclass(slots=True)
class _TemperatureAccumulator:
    identity: str
    chip_name: str | None
    label: str | None
    effective_limit_celsius: float
    minimum_celsius: float
    maximum_celsius: float
    last_celsius: float
    cpu_attributed: bool

    def add(self, value: float) -> None:
        self.minimum_celsius = min(self.minimum_celsius, value)
        self.maximum_celsius = max(self.maximum_celsius, value)
        self.last_celsius = value

    def snapshot(self) -> TemperatureStatistics:
        return TemperatureStatistics(
            identity=self.identity,
            chip_name=self.chip_name,
            label=self.label,
            effective_limit_celsius=self.effective_limit_celsius,
            minimum_celsius=self.minimum_celsius,
            maximum_celsius=self.maximum_celsius,
            last_celsius=self.last_celsius,
            cpu_attributed=self.cpu_attributed,
        )


class TemperatureAccumulators:
    def __init__(self, initial: tuple[MonitoredTemperature, ...]) -> None:
        self._values = {
            reading.identity: _TemperatureAccumulator(
                identity=reading.identity,
                chip_name=reading.chip_name,
                label=reading.label,
                effective_limit_celsius=reading.effective_limit_celsius,
                minimum_celsius=reading.current_celsius,
                maximum_celsius=reading.current_celsius,
                last_celsius=reading.current_celsius,
                cpu_attributed=reading.cpu_attributed,
            )
            for reading in initial
        }

    @property
    def identities(self) -> tuple[str, ...]:
        return tuple(sorted(self._values))

    def add(self, readings: tuple[MonitoredTemperature, ...]) -> None:
        for reading in readings:
            accumulator = self._values.get(reading.identity)
            if accumulator is not None:
                accumulator.add(reading.current_celsius)

    def snapshots(self) -> tuple[TemperatureStatistics, ...]:
        return tuple(self._values[key].snapshot() for key in sorted(self._values))

    def maximum(self) -> float | None:
        snapshots = self.snapshots()
        return max((item.maximum_celsius for item in snapshots), default=None)
