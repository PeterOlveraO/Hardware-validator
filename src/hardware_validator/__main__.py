"""Package command-line entry point."""

import argparse
from collections.abc import Sequence
import sys

from . import __version__
from .report import render_inventory
from .report import render_summary

REQUIRED_PSUTIL_VERSION = "7.2.1"


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hardware-validator",
        description=(
            "Print a non-invasive local Linux hardware inventory or run an "
            "explicit CPU test."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="show the complete technical inventory report",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    test_parser = commands.add_parser("test", help="run an explicit hardware test")
    test_commands = test_parser.add_subparsers(dest="test_component", required=True)
    cpu_parser = test_commands.add_parser(
        "cpu", help="run the moderate CPU load test"
    )
    cpu_parser.add_argument(
        "--duration",
        default=600,
        type=_duration_argument,
        metavar="DURATION",
        help="test duration from 10s through 24h (default: 10m)",
    )
    cpu_parser.add_argument(
        "--load",
        default=60,
        type=_load_argument,
        metavar="PERCENT",
        help="worker duty cycle from 10 through 90 (default: 60)",
    )
    cpu_parser.add_argument(
        "--max-temperature",
        default=85.0,
        type=_temperature_argument,
        metavar="CELSIUS",
        help="thermal safeguard from 50 through 100 C (default: 85)",
    )
    cpu_parser.add_argument(
        "--allow-no-temperature",
        action="store_true",
        help="run with a warning when no usable temperature sensor exists",
    )
    cpu_parser.add_argument(
        "--verbose",
        action="store_true",
        dest="test_verbose",
        help="show per-sample CPU, frequency, sensor, and worker details",
    )
    return parser


def _duration_argument(value: str) -> int:
    from .cpu_test.models import parse_duration

    try:
        return parse_duration(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _load_argument(value: str) -> int:
    from .cpu_test.models import normalize_load

    try:
        return normalize_load(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _temperature_argument(value: str) -> float:
    from .cpu_test.models import normalize_temperature

    try:
        return normalize_temperature(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def main(argv: Sequence[str] | None = None) -> int:
    """Validate runtime preconditions, detect hardware, and print the report."""

    arguments = _argument_parser().parse_args(argv)

    if sys.version_info < (3, 11):
        print("Hardware Validator requires Python 3.11 or newer.", file=sys.stderr)
        return 1
    if sys.platform != "linux":
        print("Hardware Validator requires Linux.", file=sys.stderr)
        return 1
    try:
        import psutil
    except ImportError:
        print(
            f"Hardware Validator requires psutil {REQUIRED_PSUTIL_VERSION}.",
            file=sys.stderr,
        )
        return 1
    installed_psutil_version = getattr(psutil, "__version__", None)
    if installed_psutil_version != REQUIRED_PSUTIL_VERSION:
        print(
            "Hardware Validator requires psutil "
            f"{REQUIRED_PSUTIL_VERSION}; found {installed_psutil_version or 'unknown'}.",
            file=sys.stderr,
        )
        return 1
    if arguments.command == "test":
        from .cpu_test.application import run_cpu_test_command
        from .cpu_test.models import CpuTestConfig

        config = CpuTestConfig(
            duration_seconds=arguments.duration,
            load_percent=arguments.load,
            maximum_temperature_celsius=arguments.max_temperature,
            allow_no_temperature=arguments.allow_no_temperature,
            verbose=bool(arguments.verbose or arguments.test_verbose),
        )
        try:
            return run_cpu_test_command(config)
        except KeyboardInterrupt:
            try:
                print("CPU test interrupted by Ctrl+C.", file=sys.stderr)
            except (OSError, UnicodeError, ValueError):
                pass
            return 130
        except Exception as error:
            safe_error = str(error).encode("unicode_escape").decode("ascii")
            try:
                print(f"Unable to run the CPU test: {safe_error}", file=sys.stderr)
            except (OSError, UnicodeError, ValueError):
                pass
            return 1
    from .collector import ContractViolationError
    from .collector import collect_inventory
    from .detectors import default_detectors

    try:
        results = collect_inventory(default_detectors())
        report = (
            render_inventory(results)
            if arguments.verbose
            else render_summary(results)
        )
    except ContractViolationError as error:
        print(f"Internal hardware inventory contract error: {error}", file=sys.stderr)
        return 1
    try:
        print(report)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Unable to write the hardware inventory: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
