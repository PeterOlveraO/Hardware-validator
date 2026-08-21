"""Application adapter joining the CPU-test engine to terminal streams."""

from contextlib import AbstractContextManager
import sys
from typing import TextIO

from ..detectors.cpu import CpuDetector
from .engine import CpuTestEngine
from .models import CpuTestConfig
from .models import CpuTestStatus
from .presentation import CpuTestConsoleReporter
from .presentation import render_cpu_test_result
from .signals import SignalController


def _processor_model() -> str | None:
    result = CpuDetector().detect()
    return result.value.model_names[0] if result.value.model_names else None


def run_cpu_test_command(
    config: CpuTestConfig,
    *,
    engine: CpuTestEngine | None = None,
    processor_model: str | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    signal_controller: SignalController | None = None,
) -> int:
    """Run the test, render its result, and map its public outcome to an exit code."""

    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    signals = signal_controller or SignalController()
    context: AbstractContextManager[None] = signals.installed()
    with context:
        reporter = CpuTestConsoleReporter(
            config,
            processor_model=(
                processor_model
                if processor_model is not None
                else _processor_model()
            ),
            stdout=output,
            stderr=errors,
        )
        active_engine = engine or CpuTestEngine(pending_signal=signals.pending)
        result = active_engine.run(config, reporter)
        print(render_cpu_test_result(result), file=output)
    if result.status is CpuTestStatus.COMPLETED:
        return 0
    if (
        result.status is CpuTestStatus.CANCELLED
        and result.reason == "Interrupted by Ctrl+C."
    ):
        return 130
    return 1
