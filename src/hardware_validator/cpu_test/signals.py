"""Minimal main-process signal recording kept separate from test execution."""

from collections.abc import Iterator
from contextlib import contextmanager
import signal
from types import FrameType


class SignalController:
    """Record SIGINT/SIGTERM; cleanup remains in regular control flow."""

    def __init__(self) -> None:
        self._pending: int | None = None

    def _record(self, signum: int, _frame: FrameType | None) -> None:
        if self._pending is None:
            self._pending = signum

    def pending(self) -> int | None:
        return self._pending

    @contextmanager
    def installed(self) -> Iterator[None]:
        previous_interrupt = signal.getsignal(signal.SIGINT)
        previous_termination = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGINT, self._record)
        signal.signal(signal.SIGTERM, self._record)
        try:
            yield
        finally:
            signal.signal(signal.SIGINT, previous_interrupt)
            signal.signal(signal.SIGTERM, previous_termination)
