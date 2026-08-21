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
        description="Print a non-invasive local Linux hardware inventory.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="show the complete technical inventory report",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


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
