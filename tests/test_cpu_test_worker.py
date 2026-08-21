"""Tests for bounded load cycles and worker cleanup."""

import pickle
import unittest

from hardware_validator.cpu_test.coordinator import WorkerGroup
from hardware_validator.cpu_test.coordinator import WorkerCoordinator
from hardware_validator.cpu_test.coordinator import WorkerStartError
from hardware_validator.cpu_test.worker import cpu_load_worker
from hardware_validator.cpu_test.worker import run_load_cycles


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeEvent:
    def __init__(
        self,
        *,
        set_initially: bool = False,
        clock: FakeClock | None = None,
        stop_on_wait: bool = False,
    ) -> None:
        self.value = set_initially
        self.clock = clock
        self.stop_on_wait = stop_on_wait
        self.waits: list[float | None] = []

    def is_set(self) -> bool:
        return self.value

    def set(self) -> None:
        self.value = True

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout)
        if timeout is not None and self.clock is not None:
            self.clock.advance(timeout)
        if self.stop_on_wait:
            self.value = True
        return self.value


class FakeProcess:
    def __init__(self, *, responsive: bool, survives_terminate: bool = False) -> None:
        self.alive = True
        self.responsive = responsive
        self.survives_terminate = survives_terminate
        self.exitcode: int | None = None
        self.join_calls: list[float | None] = []
        self.terminated = False
        self.killed = False

    def start(self) -> None:
        pass

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout)
        if self.responsive or self.killed or (self.terminated and not self.survives_terminate):
            self.alive = False
            self.exitcode = -9 if self.killed else (-15 if self.terminated else 0)

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class CpuTestWorkerTests(unittest.TestCase):
    def test_worker_target_is_spawn_picklable_and_module_level(self) -> None:
        self.assertEqual(
            cpu_load_worker.__module__, "hardware_validator.cpu_test.worker"
        )
        self.assertTrue(pickle.dumps(cpu_load_worker))

    def test_requested_duty_cycle_alternates_work_and_interruptible_wait(self) -> None:
        clock = FakeClock()
        start = FakeEvent(set_initially=True)
        stop = FakeEvent(clock=clock)
        ready = FakeEvent()
        work_calls = 0

        def work(value: float) -> float:
            nonlocal work_calls
            work_calls += 1
            clock.advance(0.01)
            return value + 1

        run_load_cycles(
            60,
            start,
            stop,
            ready,
            monotonic=clock,
            work_chunk=work,
            cycle_seconds=0.1,
            maximum_cycles=1,
        )

        self.assertTrue(ready.is_set())
        self.assertGreaterEqual(work_calls, 6)
        self.assertAlmostEqual(stop.waits[-1] or 0, 0.04, places=6)

    def test_stop_is_observed_during_work(self) -> None:
        clock = FakeClock()
        start = FakeEvent(set_initially=True)
        stop = FakeEvent(clock=clock)
        ready = FakeEvent()

        def work(value: float) -> float:
            clock.advance(0.01)
            stop.set()
            return value

        run_load_cycles(
            90,
            start,
            stop,
            ready,
            monotonic=clock,
            work_chunk=work,
        )
        self.assertTrue(stop.is_set())
        self.assertEqual(stop.waits, [])

    def test_stop_interrupts_wait_and_closed_start_gate(self) -> None:
        clock = FakeClock()
        stop = FakeEvent(clock=clock, stop_on_wait=True)

        def work(value: float) -> float:
            clock.advance(0.01)
            return value

        run_load_cycles(
            50,
            FakeEvent(set_initially=True),
            stop,
            FakeEvent(),
            monotonic=clock,
            work_chunk=work,
        )
        self.assertTrue(stop.waits)

        closed_stop = FakeEvent(stop_on_wait=True)
        run_load_cycles(
            50, FakeEvent(set_initially=False), closed_stop, FakeEvent()
        )
        self.assertEqual(closed_stop.waits, [0.01])

    def test_cleanup_uses_forced_actions_only_for_unresponsive_workers(self) -> None:
        responsive = FakeProcess(responsive=True)
        stubborn = FakeProcess(responsive=False, survives_terminate=True)
        group = WorkerGroup(
            (responsive, stubborn),
            FakeEvent(),
            FakeEvent(),
            monotonic=lambda: 0.0,
        )

        cleanup = group.cleanup(cooperative_timeout=0)

        self.assertFalse(responsive.terminated)
        self.assertFalse(responsive.killed)
        self.assertTrue(stubborn.terminated)
        self.assertTrue(stubborn.killed)
        self.assertTrue(cleanup.all_reaped)
        self.assertIn("Worker 2 required forced kill.", cleanup.warnings)

    def test_partial_start_failure_cleans_already_started_workers(self) -> None:
        events: list[FakeEvent] = []
        started = FakeProcess(responsive=True)

        def event_factory() -> FakeEvent:
            event = FakeEvent()
            events.append(event)
            return event

        calls = 0

        def process_factory(index, load, start, stop, ready):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("fixture start failure")
            ready.set()
            return started

        coordinator = WorkerCoordinator(
            event_factory=event_factory,
            process_factory=process_factory,
            monotonic=lambda: 0.0,
            wait=lambda _seconds: None,
        )

        with self.assertRaises(WorkerStartError):
            coordinator.start(2, 20)

        self.assertFalse(started.is_alive())
        self.assertTrue(events[1].is_set())


if __name__ == "__main__":
    unittest.main()
