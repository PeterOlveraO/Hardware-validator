"""Spawn-compatible bounded CPU load worker."""

from collections.abc import Callable
import signal
import time
from typing import Protocol


class EventLike(Protocol):
    def is_set(self) -> bool: ...

    def set(self) -> None: ...

    def wait(self, timeout: float | None = None) -> bool: ...


def _arithmetic_chunk(value: float) -> float:
    """Perform fixed-size scalar work without allocating workload buffers."""

    for _ in range(128):
        value = (value * 1.000_000_119 + 0.000_000_357) % 1_000_003.0
    return value


def run_load_cycles(
    load_percent: int,
    start_gate: EventLike,
    stop_event: EventLike,
    ready_event: EventLike,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    work_chunk: Callable[[float], float] = _arithmetic_chunk,
    cycle_seconds: float = 0.1,
    maximum_cycles: int | None = None,
) -> None:
    """Run an approximate duty cycle; it does not promise exact utilization."""

    ready_event.set()
    while not start_gate.is_set():
        if stop_event.wait(0.01):
            return

    work_seconds = cycle_seconds * load_percent / 100
    value = 1.0
    cycles = 0
    while not stop_event.is_set():
        cycle_start = monotonic()
        work_deadline = cycle_start + work_seconds
        cycle_deadline = cycle_start + cycle_seconds
        while monotonic() < work_deadline:
            value = work_chunk(value)
            if stop_event.is_set():
                return
        remaining = cycle_deadline - monotonic()
        if remaining > 0 and stop_event.wait(remaining):
            return
        cycles += 1
        if maximum_cycles is not None and cycles >= maximum_cycles:
            return


def cpu_load_worker(
    load_percent: int,
    start_gate: EventLike,
    stop_event: EventLike,
    ready_event: EventLike,
) -> None:
    """Multiprocessing entry point; intentionally defined at module level."""

    signal.signal(signal.SIGINT, signal.SIG_IGN)
    run_load_cycles(load_percent, start_gate, stop_event, ready_event)
