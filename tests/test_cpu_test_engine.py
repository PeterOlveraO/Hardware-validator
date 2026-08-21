"""Deterministic tests for the CPU-test controller."""

import signal
import unittest

from hardware_validator.cpu_test.coordinator import WorkerCleanup
from hardware_validator.cpu_test.engine import ALLOW_NO_TEMPERATURE_WARNING
from hardware_validator.cpu_test.engine import CpuTestEngine
from hardware_validator.cpu_test.models import CpuTestConfig
from hardware_validator.cpu_test.models import CpuTestStatus
from hardware_validator.cpu_test.models import TemperatureReading
from hardware_validator.cpu_test.sources import CpuMetricSample
from hardware_validator.cpu_test.temperature import TemperatureReadResult


def temperature(
    value: float,
    *,
    identity: str = "sensor-a",
    critical: float | None = None,
) -> TemperatureReading:
    return TemperatureReading(
        identity=identity,
        source="psutil",
        chip_name="fixture-chip",
        label=identity,
        current_celsius=value,
        critical_celsius=critical,
    )


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def wait(self, seconds: float) -> None:
        self.value += max(seconds, 1.0)


class FakeCpuSource:
    def __init__(
        self,
        samples: tuple[float, ...] = (20.0,),
        *,
        worker_count: int = 1,
        frequency: float | None = None,
        on_sample=None,
    ) -> None:
        self.samples = samples
        self.worker_count = worker_count
        self.frequency = frequency
        self.prime_calls: list[bool] = []
        self.sample_calls = 0
        self.on_sample = on_sample

    def logical_processor_count(self) -> int:
        return self.worker_count

    def prime_utilization(self, *, per_processor: bool) -> None:
        self.prime_calls.append(per_processor)

    def read_frequency(self):
        return self.frequency, (self.frequency,)

    def sample(self, *, per_processor: bool) -> CpuMetricSample:
        if self.on_sample is not None:
            self.on_sample()
        value = self.samples[min(self.sample_calls, len(self.samples) - 1)]
        self.sample_calls += 1
        return CpuMetricSample(
            total_utilization_percent=value,
            per_processor_utilization_percent=(value,) if per_processor else None,
            aggregate_frequency_hz=self.frequency,
            per_processor_frequency_hz=(self.frequency,) if per_processor else None,
        )


class FakeTemperatureSource:
    def __init__(self, rounds: tuple[tuple[TemperatureReading, ...], ...]) -> None:
        self.rounds = rounds
        self.calls = 0

    def read(self) -> TemperatureReadResult:
        values = self.rounds[min(self.calls, len(self.rounds) - 1)]
        self.calls += 1
        return TemperatureReadResult(values)


class FakeGroup:
    def __init__(
        self,
        worker_count: int,
        *,
        fail_immediately: bool = False,
        cleanup: WorkerCleanup | None = None,
    ) -> None:
        self.worker_count = worker_count
        self.fail_immediately = fail_immediately
        self.cleanup_result = cleanup or WorkerCleanup((), True, ())
        self.released = False
        self.stop_requested = False
        self.cleaned = False

    def release(self) -> None:
        self.released = True

    def request_stop(self) -> None:
        self.stop_requested = True

    def alive_count(self) -> int:
        return self.worker_count

    def unexpected_exit(self):
        if self.fail_immediately and not self.stop_requested:
            return (1, 3)
        return None

    def cleanup(self) -> WorkerCleanup:
        self.cleaned = True
        self.stop_requested = True
        return self.cleanup_result


class FakeCoordinator:
    def __init__(self, group: FakeGroup) -> None:
        self.group = group
        self.calls: list[tuple[int, int]] = []

    def start(self, worker_count: int, load_percent: int) -> FakeGroup:
        self.calls.append((worker_count, load_percent))
        return self.group


class RecordingObserver:
    def __init__(self) -> None:
        self.ready = []
        self.samples = []
        self.worker_events: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def on_ready(self, ready) -> None:
        self.ready.append(ready)

    def on_sample(self, sample) -> None:
        self.samples.append(sample)

    def on_worker_event(self, message: str) -> None:
        self.worker_events.append(message)

    def on_warning(self, message: str) -> None:
        self.warnings.append(message)

    def on_error(self, message: str) -> None:
        self.errors.append(message)


def engine_fixture(
    *,
    cpu: FakeCpuSource | None = None,
    temperatures: FakeTemperatureSource | None = None,
    group: FakeGroup | None = None,
    pending_signal=lambda: None,
):
    clock = FakeClock()
    cpu_source = cpu or FakeCpuSource()
    temperature_source = temperatures or FakeTemperatureSource(
        ((temperature(40),),)
    )
    worker_group = group or FakeGroup(cpu_source.worker_count)
    coordinator = FakeCoordinator(worker_group)
    engine = CpuTestEngine(
        cpu_source=cpu_source,
        temperature_source=temperature_source,
        coordinator=coordinator,  # type: ignore[arg-type]
        monotonic=clock,
        wait=clock.wait,
        pending_signal=pending_signal,
    )
    return engine, cpu_source, coordinator, worker_group


class CpuTestEngineTests(unittest.TestCase):
    def test_normal_completion_primes_and_calculates_statistics(self) -> None:
        cpu = FakeCpuSource((0.0, 50.0, 100.0), frequency=2_000_000_000.0)
        engine, _, coordinator, group = engine_fixture(cpu=cpu)
        observer = RecordingObserver()

        result = engine.run(CpuTestConfig(duration_seconds=10), observer)

        self.assertIs(result.status, CpuTestStatus.COMPLETED)
        self.assertEqual(cpu.prime_calls, [False])
        self.assertEqual(coordinator.calls, [(1, 60)])
        self.assertTrue(group.released)
        self.assertTrue(group.cleaned)
        self.assertEqual(result.sample_count, 10)
        self.assertEqual(result.utilization_minimum_percent, 0.0)
        self.assertEqual(result.utilization_maximum_percent, 100.0)
        self.assertEqual(result.utilization_average_percent, 85.0)
        self.assertEqual(result.actual_duration_seconds, 10.0)
        self.assertEqual(result.initial_frequency_hz, 2_000_000_000.0)
        self.assertEqual(result.minimum_frequency_hz, 2_000_000_000.0)

    def test_verbose_primes_and_records_per_processor_values(self) -> None:
        engine, cpu, _, _ = engine_fixture()
        observer = RecordingObserver()
        engine.run(CpuTestConfig(duration_seconds=10, verbose=True), observer)
        self.assertEqual(cpu.prime_calls, [True])
        self.assertEqual(observer.samples[0].per_processor_utilization_percent, (20.0,))

    def test_missing_temperature_requires_explicit_override(self) -> None:
        no_temperature = FakeTemperatureSource(((),))
        engine, cpu, coordinator, _ = engine_fixture(temperatures=no_temperature)

        result = engine.run(CpuTestConfig(duration_seconds=10))

        self.assertIs(result.status, CpuTestStatus.ERROR)
        self.assertEqual(result.sample_count, 0)
        self.assertEqual(cpu.prime_calls, [])
        self.assertEqual(coordinator.calls, [])

    def test_allow_no_temperature_warns_and_completes(self) -> None:
        engine, _, _, _ = engine_fixture(
            temperatures=FakeTemperatureSource(((),))
        )
        observer = RecordingObserver()

        result = engine.run(
            CpuTestConfig(duration_seconds=10, allow_no_temperature=True),
            observer,
        )

        self.assertIs(result.status, CpuTestStatus.COMPLETED)
        self.assertIn(ALLOW_NO_TEMPERATURE_WARNING, result.warnings)
        self.assertIn(ALLOW_NO_TEMPERATURE_WARNING, observer.warnings)
        self.assertIsNone(result.initial_temperature_celsius)

    def test_temperature_at_limit_before_start_creates_no_workers(self) -> None:
        engine, _, coordinator, _ = engine_fixture(
            temperatures=FakeTemperatureSource(((temperature(85),),))
        )

        result = engine.run(CpuTestConfig(duration_seconds=10))

        self.assertIs(result.status, CpuTestStatus.TEMPERATURE_LIMIT)
        self.assertEqual(result.actual_duration_seconds, 0.0)
        self.assertEqual(coordinator.calls, [])

    def test_critical_limit_lower_than_configured_stops_runtime(self) -> None:
        temperatures = FakeTemperatureSource(
            (
                (temperature(60, critical=70),),
                (temperature(70, critical=70),),
            )
        )
        engine, _, _, group = engine_fixture(temperatures=temperatures)

        result = engine.run(CpuTestConfig(duration_seconds=10))

        self.assertIs(result.status, CpuTestStatus.TEMPERATURE_LIMIT)
        self.assertEqual(result.sample_count, 1)
        self.assertTrue(group.stop_requested)
        self.assertEqual(
            result.temperature_statistics[0].effective_limit_celsius, 70
        )

    def test_complete_sensor_loss_is_error(self) -> None:
        engine, _, _, group = engine_fixture(
            temperatures=FakeTemperatureSource(((temperature(40),), ()))
        )

        result = engine.run(CpuTestConfig(duration_seconds=10))

        self.assertIs(result.status, CpuTestStatus.ERROR)
        self.assertIn("lost", result.reason)
        self.assertTrue(group.cleaned)

    def test_partial_sensor_loss_warns_but_retains_monitoring(self) -> None:
        temperatures = FakeTemperatureSource(
            (
                (
                    temperature(40, identity="a"),
                    temperature(41, identity="b"),
                ),
                (temperature(42, identity="a"),),
            )
        )
        engine, _, _, _ = engine_fixture(temperatures=temperatures)
        result = engine.run(CpuTestConfig(duration_seconds=10))
        self.assertIs(result.status, CpuTestStatus.COMPLETED)
        self.assertTrue(
            any("sensor-b" in warning or "b" in warning for warning in result.warnings)
        )

    def test_temperature_round_over_two_seconds_stops_with_error(self) -> None:
        clock = FakeClock()

        class SlowTemperatureSource(FakeTemperatureSource):
            def read(self) -> TemperatureReadResult:
                result = super().read()
                if self.calls > 1:
                    clock.value += 2.1
                return result

        cpu = FakeCpuSource()
        group = FakeGroup(1)
        engine = CpuTestEngine(
            cpu_source=cpu,
            temperature_source=SlowTemperatureSource(((temperature(40),),)),
            coordinator=FakeCoordinator(group),  # type: ignore[arg-type]
            monotonic=clock,
            wait=clock.wait,
        )
        result = engine.run(CpuTestConfig(duration_seconds=10))
        self.assertIs(result.status, CpuTestStatus.ERROR)
        self.assertIn("two-second", result.reason)
        self.assertEqual(result.sample_count, 1)

    def test_new_sensor_does_not_replace_preflight_sensor(self) -> None:
        engine, _, _, _ = engine_fixture(
            temperatures=FakeTemperatureSource(
                ((temperature(40, identity="old"),), (temperature(40, identity="new"),))
            )
        )
        result = engine.run(CpuTestConfig(duration_seconds=10))
        self.assertIs(result.status, CpuTestStatus.ERROR)

    def test_worker_failure_is_typed_error_and_every_worker_is_cleaned(self) -> None:
        group = FakeGroup(1, fail_immediately=True)
        engine, _, _, _ = engine_fixture(group=group)

        result = engine.run(CpuTestConfig(duration_seconds=10))

        self.assertIs(result.status, CpuTestStatus.ERROR)
        self.assertIn("code 3", result.reason)
        self.assertTrue(group.cleaned)

    def test_worker_exit_during_sampling_is_not_reported_as_completed(self) -> None:
        group = FakeGroup(1)
        cpu = FakeCpuSource(on_sample=lambda: setattr(group, "fail_immediately", True))
        engine, _, _, _ = engine_fixture(cpu=cpu, group=group)
        result = engine.run(CpuTestConfig(duration_seconds=10))
        self.assertIs(result.status, CpuTestStatus.ERROR)
        self.assertIn("exited unexpectedly", result.reason)

    def test_worker_failure_wins_over_concurrent_thermal_limit(self) -> None:
        group = FakeGroup(1)
        cpu = FakeCpuSource(on_sample=lambda: setattr(group, "fail_immediately", True))
        engine, _, _, _ = engine_fixture(
            cpu=cpu,
            group=group,
            temperatures=FakeTemperatureSource(
                ((temperature(40),), (temperature(90),))
            ),
        )
        result = engine.run(CpuTestConfig(duration_seconds=10))
        self.assertIs(result.status, CpuTestStatus.ERROR)
        self.assertIn("worker", result.reason)

    def test_ctrl_c_and_sigterm_are_cancelled_after_worker_start(self) -> None:
        for selected_signal, reason in (
            (signal.SIGINT, "Ctrl+C"),
            (signal.SIGTERM, "SIGTERM"),
        ):
            with self.subTest(selected_signal=selected_signal):
                pending = iter((None, None, selected_signal))
                group = FakeGroup(1)
                engine, _, _, _ = engine_fixture(
                    group=group, pending_signal=lambda: next(pending)
                )
                result = engine.run(CpuTestConfig(duration_seconds=10))
                self.assertIs(result.status, CpuTestStatus.CANCELLED)
                self.assertIn(reason, result.reason)
                self.assertTrue(group.cleaned)

    def test_abnormal_cleanup_changes_normal_completion_to_error(self) -> None:
        group = FakeGroup(
            1,
            cleanup=WorkerCleanup(
                ("Worker 1 required forced termination after timeout.",),
                True,
                ((1, -15),),
            ),
        )
        engine, _, _, _ = engine_fixture(group=group)
        result = engine.run(CpuTestConfig(duration_seconds=10))
        self.assertIs(result.status, CpuTestStatus.ERROR)
        self.assertIn("abnormal cleanup", result.reason)

    def test_unreaped_worker_is_an_error_result(self) -> None:
        group = FakeGroup(1, cleanup=WorkerCleanup((), False, ()))
        engine, _, _, _ = engine_fixture(group=group)
        result = engine.run(CpuTestConfig(duration_seconds=10))
        self.assertIs(result.status, CpuTestStatus.ERROR)
        self.assertIn("reap", result.reason)

    def test_sensor_and_sample_order_are_deterministic(self) -> None:
        temperatures = FakeTemperatureSource(
            (
                (temperature(41, identity="z"), temperature(40, identity="a")),
            )
        )
        engine, _, _, _ = engine_fixture(temperatures=temperatures)
        observer = RecordingObserver()
        result = engine.run(CpuTestConfig(duration_seconds=10), observer)
        self.assertEqual(
            tuple(item.identity for item in result.temperature_statistics),
            ("a", "z"),
        )
        self.assertEqual(
            [sample.elapsed_seconds for sample in observer.samples],
            sorted(sample.elapsed_seconds for sample in observer.samples),
        )


if __name__ == "__main__":
    unittest.main()
