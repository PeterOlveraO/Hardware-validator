"""Plain-text CPU-test progress and result presentation."""

import sys
from typing import TextIO
import unicodedata

from .models import CpuTestConfig
from .models import CpuTestReady
from .models import CpuTestResult
from .models import CpuTestSample


UNKNOWN = "Unknown"


def _plain_text(value: object) -> str:
    characters: list[str] = []
    for character in str(value):
        code_point = ord(character)
        if unicodedata.category(character) in {"Cc", "Cf", "Cs"}:
            characters.append(
                f"\\x{code_point:02x}"
                if code_point <= 255
                else (
                    f"\\u{code_point:04x}"
                    if code_point <= 0xFFFF
                    else f"\\U{code_point:08x}"
                )
            )
        else:
            characters.append(character)
    return "".join(characters)


def _number(value: float | None, unit: str, digits: int = 1) -> str:
    return UNKNOWN if value is None else f"{value:.{digits}f} {unit}"


def _frequency(value: float | None) -> str:
    if value is None:
        return UNKNOWN
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f} GHz"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} MHz"
    return f"{value:.0f} Hz"


class CpuTestConsoleReporter:
    """Write configuration/progress to stdout and diagnostics to stderr."""

    def __init__(
        self,
        config: CpuTestConfig,
        *,
        processor_model: str | None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self._config = config
        self._processor_model = processor_model
        self._stdout = stdout or sys.stdout
        self._stderr = stderr or sys.stderr
        self._next_compact_update = 5.0
        self._sample_sequence = 0
        self._last_sample_elapsed: float | None = None
        self._temperature_ranges: dict[str, tuple[float, float, float]] = {}
        self._monitored_limits: dict[str, float] = {}

    def on_ready(self, ready: CpuTestReady) -> None:
        for reading in ready.temperatures:
            self._temperature_ranges[reading.identity] = (
                reading.current_celsius,
                reading.current_celsius,
                reading.current_celsius,
            )
            self._monitored_limits[
                reading.identity
            ] = reading.effective_limit_celsius
        print("CPU Test", file=self._stdout)
        print(
            f"  Processor Model: {_plain_text(self._processor_model) if self._processor_model else UNKNOWN}",
            file=self._stdout,
        )
        print(
            f"  Requested Duration: {self._config.duration_seconds} seconds",
            file=self._stdout,
        )
        print(
            f"  Requested Load: {self._config.load_percent}% duty cycle",
            file=self._stdout,
        )
        print(f"  Workers: {ready.worker_count}", file=self._stdout)
        print(
            f"  Thermal Limit: {self._config.maximum_temperature_celsius:g} C",
            file=self._stdout,
        )
        print(
            "  Thermal Monitoring: "
            + ("Active" if ready.thermal_monitoring_active else "Unavailable"),
            file=self._stdout,
        )
        lower_limits = tuple(
            reading
            for reading in ready.temperatures
            if reading.effective_limit_celsius
            < self._config.maximum_temperature_celsius
        )
        if lower_limits:
            print("  Lower Sensor Effective Limits:", file=self._stdout)
            for reading in lower_limits:
                print(
                    f"    {_plain_text(reading.identity)}: "
                    f"{reading.effective_limit_celsius:.1f} C",
                    file=self._stdout,
                )

    def on_sample(self, sample: CpuTestSample) -> None:
        self._sample_sequence += 1
        interval = (
            None
            if self._last_sample_elapsed is None
            else sample.elapsed_seconds - self._last_sample_elapsed
        )
        self._last_sample_elapsed = sample.elapsed_seconds
        for reading in sample.temperatures:
            previous = self._temperature_ranges.get(reading.identity)
            if previous is None:
                self._temperature_ranges[reading.identity] = (
                    reading.current_celsius,
                    reading.current_celsius,
                    reading.current_celsius,
                )
            else:
                self._temperature_ranges[reading.identity] = (
                    min(previous[0], reading.current_celsius),
                    max(previous[1], reading.current_celsius),
                    reading.current_celsius,
                )
        if not self._config.verbose and sample.elapsed_seconds < self._next_compact_update:
            return
        remaining = max(
            0.0, self._config.duration_seconds - sample.elapsed_seconds
        )
        current_temperature = max(
            (item.current_celsius for item in sample.temperatures), default=None
        )
        print(
            "Progress: "
            f"elapsed={sample.elapsed_seconds:.1f}s "
            f"remaining={remaining:.1f}s "
            f"cpu={sample.total_utilization_percent:.1f}% "
            f"frequency={_frequency(sample.aggregate_frequency_hz)} "
            f"temperature={_number(current_temperature, 'C')} "
            f"workers={sample.workers_alive}/{sample.worker_count}",
            file=self._stdout,
        )
        if not self._config.verbose:
            self._next_compact_update = sample.elapsed_seconds + 5.0
            return
        rate = None if interval in {None, 0} else 1 / interval
        print(
            "  Sample: "
            f"sequence={self._sample_sequence} "
            f"interval={_number(interval, 's', 2)} "
            f"rate={_number(rate, 'samples/s', 2)}",
            file=self._stdout,
        )
        per_cpu = sample.per_processor_utilization_percent
        print(
            "  Per-processor utilization: "
            + (
                ", ".join(f"CPU {index}={value:.1f}%" for index, value in enumerate(per_cpu))
                if per_cpu is not None
                else UNKNOWN
            ),
            file=self._stdout,
        )
        print(
            f"  Aggregate frequency: {_frequency(sample.aggregate_frequency_hz)}",
            file=self._stdout,
        )
        if sample.per_processor_frequency_hz is not None:
            print(
                "  Per-processor frequencies: "
                + ", ".join(
                    f"CPU {index}={_frequency(value)}"
                    for index, value in enumerate(
                        sample.per_processor_frequency_hz
                    )
                ),
                file=self._stdout,
            )
        if sample.temperatures:
            print("  Monitored temperature sensors:", file=self._stdout)
        elif self._monitored_limits:
            print("  Monitored temperature sensors:", file=self._stdout)
        else:
            print("  Monitored temperature sensors: Unknown", file=self._stdout)
        current = {reading.identity: reading for reading in sample.temperatures}
        for identity in sorted(self._monitored_limits):
            minimum, maximum, last = self._temperature_ranges[identity]
            reading = current.get(identity)
            current_text = (
                f"{reading.current_celsius:.1f} C"
                if reading is not None
                else "Unavailable"
            )
            print(
                "    "
                f"{_plain_text(identity)}: {current_text} "
                f"(effective limit {self._monitored_limits[identity]:.1f} C, "
                f"min {minimum:.1f} C, max {maximum:.1f} C, "
                f"last {last:.1f} C)",
                file=self._stdout,
            )

    def on_worker_event(self, message: str) -> None:
        if self._config.verbose:
            print(f"Worker event: {_plain_text(message)}", file=self._stdout)

    def on_warning(self, message: str) -> None:
        print(f"Warning: {_plain_text(message)}", file=self._stderr)

    def on_error(self, message: str) -> None:
        prefix = "Interrupted" if "Interrupt" in message else "Error"
        print(f"{prefix}: {_plain_text(message)}", file=self._stderr)


def render_cpu_test_result(result: CpuTestResult) -> str:
    """Render a final result without health or benchmark claims."""

    lines = [
        "CPU Test Result",
        f"  Status: {result.status.value}",
        f"  Reason: {_plain_text(result.reason)}",
        f"  Requested Duration: {result.requested_duration_seconds} seconds",
        f"  Actual Duration: {result.actual_duration_seconds:.2f} seconds",
        f"  Requested Load: {result.requested_load_percent}% duty cycle",
        f"  Workers: {result.worker_count}",
        f"  Valid Samples: {result.sample_count}",
        f"  Average Utilization: {_number(result.utilization_average_percent, '%')}",
        f"  Minimum Utilization: {_number(result.utilization_minimum_percent, '%')}",
        f"  Maximum Utilization: {_number(result.utilization_maximum_percent, '%')}",
        f"  Initial Temperature: {_number(result.initial_temperature_celsius, 'C')}",
        f"  Maximum Temperature: {_number(result.maximum_temperature_celsius, 'C')}",
        f"  Initial Frequency: {_frequency(result.initial_frequency_hz)}",
        f"  Minimum Frequency: {_frequency(result.minimum_frequency_hz)}",
        f"  Maximum Frequency: {_frequency(result.maximum_frequency_hz)}",
    ]
    if result.warnings:
        lines.append("  Warnings:")
        lines.extend(f"    - {_plain_text(item)}" for item in result.warnings)
    else:
        lines.append("  Warnings: None")
    lines.append(
        "  This exercise does not certify CPU health or long-term reliability."
    )
    return "\n".join(lines)
