"""CPU test controller with injectable clocks, sources, and workers."""

from collections.abc import Callable
import math
import signal
import time
from typing import Protocol

from .coordinator import WorkerCoordinator
from .coordinator import WorkerGroup
from .coordinator import WorkerStartError
from .models import CpuTestConfig
from .models import CpuTestReady
from .models import CpuTestResult
from .models import CpuTestSample
from .models import CpuTestStatus
from .models import MonitoredTemperature
from .models import TemperatureReading
from .sources import CpuMetricSample
from .sources import CpuSourceError
from .sources import PsutilCpuSource
from .statistics import NumericAccumulator
from .statistics import TemperatureAccumulators
from .temperature import LocalTemperatureMonitor
from .temperature import TemperatureReadResult


ALLOW_NO_TEMPERATURE_WARNING = (
    "No usable temperature sensor is available; Hardware Validator thermal "
    "protection is disabled for this run."
)


class CpuSource(Protocol):
    def logical_processor_count(self) -> int: ...

    def prime_utilization(self, *, per_processor: bool) -> None: ...

    def read_frequency(
        self,
    ) -> tuple[float | None, tuple[float | None, ...] | None]: ...

    def sample(self, *, per_processor: bool) -> CpuMetricSample: ...


class TemperatureSource(Protocol):
    def read(self) -> TemperatureReadResult: ...


class Coordinator(Protocol):
    def start(self, worker_count: int, load_percent: int) -> WorkerGroup: ...


class CpuTestObserver(Protocol):
    def on_ready(self, ready: CpuTestReady) -> None: ...

    def on_sample(self, sample: CpuTestSample) -> None: ...

    def on_worker_event(self, message: str) -> None: ...

    def on_warning(self, message: str) -> None: ...

    def on_error(self, message: str) -> None: ...


class NullCpuTestObserver:
    def on_ready(self, ready: CpuTestReady) -> None:
        pass

    def on_sample(self, sample: CpuTestSample) -> None:
        pass

    def on_worker_event(self, message: str) -> None:
        pass

    def on_warning(self, message: str) -> None:
        pass

    def on_error(self, message: str) -> None:
        pass


class _CheckedClock:
    def __init__(self, monotonic: Callable[[], float]) -> None:
        self._monotonic = monotonic
        self._last: float | None = None

    def now(self) -> float:
        value = self._monotonic()
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError("Monotonic clock returned an invalid value.")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise RuntimeError("Monotonic clock returned a non-finite value.")
        if self._last is not None and normalized < self._last:
            raise RuntimeError("Monotonic clock moved backwards.")
        self._last = normalized
        return normalized


def _monitored(
    reading: TemperatureReading, configured_limit: float
) -> MonitoredTemperature:
    effective = (
        min(configured_limit, reading.critical_celsius)
        if reading.critical_celsius is not None
        else configured_limit
    )
    return MonitoredTemperature(
        identity=reading.identity,
        chip_name=reading.chip_name,
        label=reading.label,
        current_celsius=reading.current_celsius,
        effective_limit_celsius=effective,
        cpu_attributed=reading.cpu_attributed,
    )


def _valid_frequency(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        normalized = float(value)
    except OverflowError:
        return None
    return normalized if math.isfinite(normalized) and normalized > 0 else None


class CpuTestEngine:
    """Coordinate one bounded load worker per logical processor."""

    def __init__(
        self,
        *,
        cpu_source: CpuSource | None = None,
        temperature_source: TemperatureSource | None = None,
        coordinator: Coordinator | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wait: Callable[[float], None] = time.sleep,
        pending_signal: Callable[[], int | None] = lambda: None,
    ) -> None:
        self._cpu_source = cpu_source or PsutilCpuSource()
        self._temperature_source = temperature_source or LocalTemperatureMonitor()
        self._monotonic = monotonic
        self._wait = wait
        self._pending_signal = pending_signal
        self._coordinator = coordinator or WorkerCoordinator(monotonic=monotonic)

    def run(
        self,
        config: CpuTestConfig,
        observer: CpuTestObserver | None = None,
    ) -> CpuTestResult:
        if not isinstance(config, CpuTestConfig):
            raise TypeError("config must be CpuTestConfig")
        output = observer or NullCpuTestObserver()
        clock = _CheckedClock(self._monotonic)
        warnings: list[str] = []
        utilization = NumericAccumulator()
        runtime_frequency = NumericAccumulator()
        temperature_statistics = TemperatureAccumulators(())
        initial_temperature: float | None = None
        initial_frequency: float | None = None
        worker_count = 0
        group: WorkerGroup | None = None
        start_time: float | None = None
        stop_time: float | None = None
        status: CpuTestStatus | None = None
        reason = "CPU test did not start."

        def add_warning(message: str) -> None:
            if message not in warnings:
                warnings.append(message)
                output.on_warning(message)

        def select(
            selected_status: CpuTestStatus,
            selected_reason: str,
            timestamp: float | None = None,
        ) -> None:
            nonlocal status, reason, stop_time
            if status is not None:
                return
            status = selected_status
            reason = selected_reason
            stop_time = timestamp
            if group is not None:
                group.request_stop()
            if selected_status is not CpuTestStatus.COMPLETED:
                output.on_error(selected_reason)

        try:
            worker_count = self._cpu_source.logical_processor_count()
            temperature_round = self._temperature_source.read()
            for warning in temperature_round.warnings:
                add_warning(warning)
            initial_monitored = tuple(
                _monitored(reading, config.maximum_temperature_celsius)
                for reading in temperature_round.readings
            )
            initial_monitored = tuple(
                sorted(initial_monitored, key=lambda item: item.identity)
            )
            temperature_statistics = TemperatureAccumulators(initial_monitored)
            output.on_ready(
                CpuTestReady(
                    worker_count=worker_count,
                    thermal_monitoring_active=bool(initial_monitored),
                    temperatures=initial_monitored,
                )
            )
            if initial_monitored:
                initial_temperature = max(
                    item.current_celsius for item in initial_monitored
                )
                hot = next(
                    (
                        item
                        for item in initial_monitored
                        if item.current_celsius >= item.effective_limit_celsius
                    ),
                    None,
                )
                if hot is not None:
                    select(
                        CpuTestStatus.TEMPERATURE_LIMIT,
                        f"Monitored temperature sensor {hot.identity} is already at its effective limit.",
                    )
            elif config.allow_no_temperature:
                add_warning(ALLOW_NO_TEMPERATURE_WARNING)
            else:
                select(
                    CpuTestStatus.ERROR,
                    "No usable temperature measurement is available.",
                )

            if status is None:
                pending = self._pending_signal()
                if pending == signal.SIGINT:
                    select(CpuTestStatus.CANCELLED, "Interrupted by Ctrl+C.")
                elif pending == signal.SIGTERM:
                    select(CpuTestStatus.CANCELLED, "Interrupted by SIGTERM.")

            if status is None:
                self._cpu_source.prime_utilization(per_processor=config.verbose)
                initial_frequency, _ = self._cpu_source.read_frequency()
                initial_frequency = _valid_frequency(initial_frequency)
                group = self._coordinator.start(
                    worker_count, config.load_percent
                )
                output.on_worker_event(f"{worker_count} CPU load workers are ready.")

                pending = self._pending_signal()
                if pending == signal.SIGINT:
                    select(CpuTestStatus.CANCELLED, "Interrupted by Ctrl+C.")
                elif pending == signal.SIGTERM:
                    select(CpuTestStatus.CANCELLED, "Interrupted by SIGTERM.")
                else:
                    start_time = clock.now()
                    group.release()
                    output.on_worker_event("CPU load workers started.")

            if status is None and group is not None and start_time is not None:
                deadline = start_time + config.duration_seconds
                next_sample = start_time + 1.0
                last_temperature_time = start_time
                while status is None:
                    pending = self._pending_signal()
                    if pending == signal.SIGINT:
                        select(
                            CpuTestStatus.CANCELLED,
                            "Interrupted by Ctrl+C.",
                            clock.now(),
                        )
                        break
                    if pending == signal.SIGTERM:
                        select(
                            CpuTestStatus.CANCELLED,
                            "Interrupted by SIGTERM.",
                            clock.now(),
                        )
                        break

                    failed_worker = group.unexpected_exit()
                    if failed_worker is not None:
                        exit_code = failed_worker[1]
                        select(
                            CpuTestStatus.ERROR,
                            f"CPU load worker {failed_worker[0]} exited unexpectedly with code {exit_code}.",
                            clock.now(),
                        )
                        break

                    now = clock.now()
                    if now >= next_sample and next_sample <= deadline:
                        metrics = self._cpu_source.sample(
                            per_processor=config.verbose
                        )
                        pending = self._pending_signal()
                        if pending in {signal.SIGINT, signal.SIGTERM}:
                            select(
                                CpuTestStatus.CANCELLED,
                                (
                                    "Interrupted by Ctrl+C."
                                    if pending == signal.SIGINT
                                    else "Interrupted by SIGTERM."
                                ),
                                clock.now(),
                            )
                            break
                        failed_worker = group.unexpected_exit()
                        if failed_worker is not None:
                            select(
                                CpuTestStatus.ERROR,
                                f"CPU load worker {failed_worker[0]} exited unexpectedly with code {failed_worker[1]}.",
                                clock.now(),
                            )
                            break
                        utilization.add(metrics.total_utilization_percent)
                        if metrics.aggregate_frequency_hz is not None:
                            runtime_frequency.add(metrics.aggregate_frequency_hz)
                        monitored_values: tuple[MonitoredTemperature, ...] = ()
                        round_warnings: tuple[str, ...] = ()
                        if initial_monitored:
                            current_round = self._temperature_source.read()
                            round_warnings = current_round.warnings
                            sample_time = clock.now()
                            pending = self._pending_signal()
                            if pending in {signal.SIGINT, signal.SIGTERM}:
                                select(
                                    CpuTestStatus.CANCELLED,
                                    (
                                        "Interrupted by Ctrl+C."
                                        if pending == signal.SIGINT
                                        else "Interrupted by SIGTERM."
                                    ),
                                    sample_time,
                                )
                                for warning in round_warnings:
                                    add_warning(warning)
                                break
                            failed_worker = group.unexpected_exit()
                            if failed_worker is not None:
                                select(
                                    CpuTestStatus.ERROR,
                                    f"CPU load worker {failed_worker[0]} exited unexpectedly with code {failed_worker[1]}.",
                                    sample_time,
                                )
                                for warning in round_warnings:
                                    add_warning(warning)
                                break
                            if sample_time - last_temperature_time > 2.0:
                                select(
                                    CpuTestStatus.ERROR,
                                    "Temperature monitoring exceeded the two-second safety interval.",
                                    sample_time,
                                )
                                for warning in round_warnings:
                                    add_warning(warning)
                                break
                            current_by_identity = {
                                reading.identity: reading
                                for reading in current_round.readings
                            }
                            expected = {
                                reading.identity: reading
                                for reading in initial_monitored
                            }
                            monitored_values = tuple(
                                MonitoredTemperature(
                                    identity=identity,
                                    chip_name=initial.chip_name,
                                    label=initial.label,
                                    current_celsius=current_by_identity[
                                        identity
                                    ].current_celsius,
                                    effective_limit_celsius=initial.effective_limit_celsius,
                                    cpu_attributed=initial.cpu_attributed,
                                )
                                for identity, initial in sorted(expected.items())
                                if identity in current_by_identity
                            )
                            if not monitored_values:
                                select(
                                    CpuTestStatus.ERROR,
                                    "All monitored temperature sensors were lost.",
                                    sample_time,
                                )
                                for warning in round_warnings:
                                    add_warning(warning)
                                break
                            last_temperature_time = sample_time
                            missing = tuple(
                                identity
                                for identity in sorted(expected)
                                if identity not in current_by_identity
                            )
                            if missing:
                                round_warnings = (
                                    *round_warnings,
                                    "Some monitored temperature sensors are unavailable: "
                                    + ", ".join(missing)
                                    + ".",
                                )
                        else:
                            sample_time = clock.now()
                        temperature_statistics.add(monitored_values)
                        hot = next(
                            (
                                item
                                for item in monitored_values
                                if item.current_celsius
                                >= item.effective_limit_celsius
                            ),
                            None,
                        )
                        if hot is not None:
                            select(
                                CpuTestStatus.TEMPERATURE_LIMIT,
                                f"Monitored temperature sensor {hot.identity} reached its effective limit.",
                                sample_time,
                            )
                        for warning in round_warnings:
                            add_warning(warning)
                        sample = CpuTestSample(
                            elapsed_seconds=sample_time - start_time,
                            total_utilization_percent=metrics.total_utilization_percent,
                            per_processor_utilization_percent=(
                                metrics.per_processor_utilization_percent
                                if config.verbose
                                else None
                            ),
                            aggregate_frequency_hz=metrics.aggregate_frequency_hz,
                            per_processor_frequency_hz=(
                                metrics.per_processor_frequency_hz
                                if config.verbose
                                else None
                            ),
                            temperatures=monitored_values,
                            workers_alive=group.alive_count(),
                            worker_count=worker_count,
                        )
                        output.on_sample(sample)
                        if status is not None:
                            break
                        pending = self._pending_signal()
                        if pending in {signal.SIGINT, signal.SIGTERM}:
                            select(
                                CpuTestStatus.CANCELLED,
                                (
                                    "Interrupted by Ctrl+C."
                                    if pending == signal.SIGINT
                                    else "Interrupted by SIGTERM."
                                ),
                                clock.now(),
                            )
                            break
                        failed_worker = group.unexpected_exit()
                        if failed_worker is not None:
                            select(
                                CpuTestStatus.ERROR,
                                f"CPU load worker {failed_worker[0]} exited unexpectedly with code {failed_worker[1]}.",
                                clock.now(),
                            )
                            break
                        now = sample_time
                        next_sample = sample_time + 1.0

                    if now >= deadline:
                        select(
                            CpuTestStatus.COMPLETED,
                            "Requested duration elapsed.",
                            now,
                        )
                        break
                    wait_until = min(deadline, next_sample, now + 0.1)
                    self._wait(max(0.0, wait_until - now))
        except KeyboardInterrupt:
            timestamp = clock.now() if start_time is not None else None
            select(CpuTestStatus.CANCELLED, "Interrupted by Ctrl+C.", timestamp)
        except WorkerStartError as error:
            for warning in error.warnings:
                add_warning(warning)
            pending = self._pending_signal()
            if pending == signal.SIGINT:
                select(CpuTestStatus.CANCELLED, "Interrupted by Ctrl+C.")
            elif pending == signal.SIGTERM:
                select(CpuTestStatus.CANCELLED, "Interrupted by SIGTERM.")
            else:
                select(CpuTestStatus.ERROR, str(error))
        except (CpuSourceError, OSError, RuntimeError, ValueError) as error:
            timestamp = None
            if start_time is not None:
                try:
                    timestamp = clock.now()
                except RuntimeError:
                    timestamp = start_time
            select(CpuTestStatus.ERROR, str(error), timestamp)
        except Exception as error:
            timestamp = None
            if start_time is not None:
                try:
                    timestamp = clock.now()
                except RuntimeError:
                    timestamp = start_time
            select(
                CpuTestStatus.ERROR,
                f"Internal CPU test error: {type(error).__name__}.",
                timestamp,
            )
        finally:
            if group is not None:
                output.on_worker_event("Cooperative worker stop was requested.")
                try:
                    cleanup = group.cleanup()
                except Exception as error:
                    status = CpuTestStatus.ERROR
                    reason = f"Unable to clean up CPU load workers: {type(error).__name__}."
                    output.on_error(reason)
                    cleanup = None
                if cleanup is None:
                    pass
                else:
                    for warning in cleanup.warnings:
                        add_warning(warning)
                        if warning.startswith("Worker "):
                            output.on_worker_event(warning)
                    if cleanup.failed_exit_codes:
                        add_warning(
                            "Workers exited with non-zero codes: "
                            + ", ".join(
                                f"{index}={code}"
                                for index, code in cleanup.failed_exit_codes
                            )
                            + "."
                        )
                    if not cleanup.all_reaped:
                        status = CpuTestStatus.ERROR
                        reason = "Unable to reap every CPU load worker."
                        output.on_error(reason)
                    elif status is CpuTestStatus.COMPLETED and (
                        cleanup.warnings or cleanup.failed_exit_codes
                    ):
                        status = CpuTestStatus.ERROR
                        reason = "CPU load workers required abnormal cleanup."
                        output.on_error(reason)
                output.on_worker_event("CPU load workers exited and were reaped.")

        if status is None:
            status = CpuTestStatus.ERROR
            reason = "CPU test ended without a result."
            output.on_error(reason)
        actual_duration = 0.0
        if start_time is not None:
            selected_stop = stop_time if stop_time is not None else start_time
            actual_duration = max(0.0, selected_stop - start_time)
        return CpuTestResult(
            status=status,
            reason=reason,
            requested_duration_seconds=config.duration_seconds,
            actual_duration_seconds=actual_duration,
            requested_load_percent=config.load_percent,
            worker_count=worker_count,
            sample_count=utilization.count,
            utilization_average_percent=utilization.average,
            utilization_minimum_percent=utilization.minimum,
            utilization_maximum_percent=utilization.maximum,
            initial_temperature_celsius=initial_temperature,
            maximum_temperature_celsius=temperature_statistics.maximum(),
            initial_frequency_hz=initial_frequency,
            minimum_frequency_hz=runtime_frequency.minimum,
            maximum_frequency_hz=runtime_frequency.maximum,
            warnings=tuple(warnings),
            temperature_statistics=temperature_statistics.snapshots(),
        )
