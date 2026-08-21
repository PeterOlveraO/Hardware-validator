"""Multiprocessing worker lifecycle and bounded cleanup."""

from collections.abc import Callable
from dataclasses import dataclass
import multiprocessing
import time
from typing import Protocol

from .worker import EventLike
from .worker import cpu_load_worker


class ProcessLike(Protocol):
    exitcode: int | None

    def start(self) -> None: ...

    def is_alive(self) -> bool: ...

    def join(self, timeout: float | None = None) -> None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


EventFactory = Callable[[], EventLike]
ProcessFactory = Callable[[int, int, EventLike, EventLike, EventLike], ProcessLike]


class WorkerStartError(RuntimeError):
    def __init__(self, message: str, warnings: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.warnings = warnings


@dataclass(frozen=True, slots=True)
class WorkerCleanup:
    warnings: tuple[str, ...]
    all_reaped: bool
    failed_exit_codes: tuple[tuple[int, int], ...]


class WorkerGroup:
    """Own a fixed set of workers and their cooperative synchronization."""

    def __init__(
        self,
        processes: tuple[ProcessLike, ...],
        start_gate: EventLike,
        stop_event: EventLike,
        *,
        monotonic: Callable[[], float],
    ) -> None:
        self._processes = processes
        self._start_gate = start_gate
        self._stop_event = stop_event
        self._monotonic = monotonic
        self._stop_requested = False

    @property
    def worker_count(self) -> int:
        return len(self._processes)

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    def release(self) -> None:
        self._start_gate.set()

    def request_stop(self) -> None:
        self._stop_requested = True
        self._stop_event.set()

    def alive_count(self) -> int:
        return sum(process.is_alive() for process in self._processes)

    def unexpected_exit(self) -> tuple[int, int | None] | None:
        if self._stop_requested:
            return None
        for index, process in enumerate(self._processes, start=1):
            if not process.is_alive():
                return index, process.exitcode
        return None

    def cleanup(self, *, cooperative_timeout: float = 5.0) -> WorkerCleanup:
        """Stop, terminate only if needed, and reap every process."""

        self.request_stop()
        warnings: list[str] = []

        def is_alive(index: int, process: ProcessLike) -> bool:
            try:
                return process.is_alive()
            except (OSError, RuntimeError) as error:
                warnings.append(
                    f"Unable to inspect worker {index}: {type(error).__name__}."
                )
                return True

        def join(index: int, process: ProcessLike, timeout: float | None) -> None:
            try:
                process.join(timeout)
            except (OSError, RuntimeError) as error:
                warnings.append(
                    f"Unable to join worker {index}: {type(error).__name__}."
                )

        deadline = self._monotonic() + cooperative_timeout
        for index, process in enumerate(self._processes, start=1):
            remaining = max(0.0, deadline - self._monotonic())
            if is_alive(index, process) and remaining > 0:
                join(index, process, remaining)
            else:
                join(index, process, 0)

        for index, process in enumerate(self._processes, start=1):
            if not is_alive(index, process):
                continue
            warnings.append(
                f"Worker {index} required forced termination after timeout."
            )
            try:
                process.terminate()
            except (OSError, RuntimeError) as error:
                warnings.append(
                    f"Unable to terminate worker {index}: {type(error).__name__}."
                )
            join(index, process, 1.0)

        for index, process in enumerate(self._processes, start=1):
            if not is_alive(index, process):
                continue
            warnings.append(f"Worker {index} required forced kill.")
            try:
                process.kill()
            except (OSError, RuntimeError) as error:
                warnings.append(
                    f"Unable to kill worker {index}: {type(error).__name__}."
                )
            join(index, process, 1.0)

        for index, process in enumerate(self._processes, start=1):
            if is_alive(index, process):
                warnings.append(
                    f"Worker {index} required a final blocking reap after kill."
                )
                join(index, process, None)

        all_reaped = all(
            not is_alive(index, process)
            for index, process in enumerate(self._processes, start=1)
        )
        failures = tuple(
            (index, process.exitcode)
            for index, process in enumerate(self._processes, start=1)
            if process.exitcode not in {None, 0}
        )
        return WorkerCleanup(
            warnings=tuple(dict.fromkeys(warnings)),
            all_reaped=all_reaped,
            failed_exit_codes=failures,  # type: ignore[arg-type]
        )


class WorkerCoordinator:
    """Create one spawn-compatible worker per logical processor."""

    def __init__(
        self,
        *,
        event_factory: EventFactory | None = None,
        process_factory: ProcessFactory | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wait: Callable[[float], None] = time.sleep,
        startup_timeout: float = 5.0,
    ) -> None:
        context = multiprocessing.get_context("spawn")
        self._event_factory = event_factory or context.Event
        self._process_factory = process_factory or (
            lambda index, load, start, stop, ready: context.Process(
                target=cpu_load_worker,
                args=(load, start, stop, ready),
                name=f"hardware-validator-cpu-{index}",
                daemon=False,
            )
        )
        self._monotonic = monotonic
        self._wait = wait
        self._startup_timeout = startup_timeout

    def start(self, worker_count: int, load_percent: int) -> WorkerGroup:
        start_gate = self._event_factory()
        stop_event = self._event_factory()
        ready_events = tuple(self._event_factory() for _ in range(worker_count))
        processes: list[ProcessLike] = []
        group = WorkerGroup(
            tuple(processes), start_gate, stop_event, monotonic=self._monotonic
        )
        try:
            for index, ready_event in enumerate(ready_events, start=1):
                process = self._process_factory(
                    index, load_percent, start_gate, stop_event, ready_event
                )
                process.start()
                processes.append(process)
                group = WorkerGroup(
                    tuple(processes),
                    start_gate,
                    stop_event,
                    monotonic=self._monotonic,
                )
            deadline = self._monotonic() + self._startup_timeout
            while not all(event.is_set() for event in ready_events):
                failed = group.unexpected_exit()
                if failed is not None:
                    raise WorkerStartError(
                        f"CPU load worker {failed[0]} exited during startup."
                    )
                if self._monotonic() >= deadline:
                    raise WorkerStartError(
                        "CPU load workers did not become ready before timeout."
                    )
                self._wait(0.01)
            failed = group.unexpected_exit()
            if failed is not None:
                raise WorkerStartError(
                    f"CPU load worker {failed[0]} exited during startup."
                )
        except BaseException as error:
            cleanup = group.cleanup()
            cleanup_warnings = list(cleanup.warnings)
            if not cleanup.all_reaped:
                cleanup_warnings.append(
                    "Unable to reap every CPU load worker after startup failure."
                )
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            message = (
                str(error)
                if isinstance(error, WorkerStartError)
                else f"Unable to start CPU load worker {len(processes) + 1}."
            )
            raise WorkerStartError(message, tuple(cleanup_warnings)) from error
        return group
