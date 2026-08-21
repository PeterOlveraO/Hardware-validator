"""Informational Linux system, baseboard, and firmware detection."""

from collections.abc import Callable
import os
from pathlib import Path
import shlex

from ..collector import DetectionResult
from ..models import BaseboardInfo
from ..models import BiosInfo
from ..models import SystemInfo

TextReader = Callable[[Path], str]

_DMI_PLACEHOLDERS = {
    "unknown",
    "not specified",
    "default string",
    "to be filled by o.e.m.",
}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip("\x00 \t\r\n")
    if not cleaned or cleaned.casefold() in _DMI_PLACEHOLDERS:
        return None
    return cleaned


def _first_clean(*values: str | None) -> str | None:
    for value in values:
        cleaned = _clean_text(value)
        if cleaned is not None:
            return cleaned
    return None


def _parse_os_release(content: str) -> tuple[dict[str, str], bool]:
    values: dict[str, str] = {}
    malformed = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            malformed = True
            continue
        key, raw_value = line.split("=", 1)
        if not key or not key.replace("_", "").isalnum():
            malformed = True
            continue
        try:
            parsed = shlex.split(raw_value, comments=False, posix=True)
        except ValueError:
            malformed = True
            continue
        if len(parsed) > 1:
            malformed = True
            continue
        values[key] = parsed[0] if parsed else ""
    return values, malformed


class SystemDetector:
    """Read system identity from standard Linux files and APIs."""

    def __init__(
        self,
        *,
        os_release_path: Path = Path("/etc/os-release"),
        os_release_fallback_path: Path = Path("/usr/lib/os-release"),
        dmi_root: Path = Path("/sys/class/dmi/id"),
        device_tree_model_path: Path = Path("/sys/firmware/devicetree/base/model"),
        uname: Callable[[], os.uname_result] = os.uname,
        read_text: TextReader = _read_text,
    ) -> None:
        self._os_release_path = os_release_path
        self._os_release_fallback_path = os_release_fallback_path
        self._dmi_root = dmi_root
        self._device_tree_model_path = device_tree_model_path
        self._uname = uname
        self._read_text = read_text

    def detect(self) -> DetectionResult[SystemInfo]:
        """Collect available system information without invoking applications."""

        issues: list[str] = []

        distribution = None
        distribution_version = None
        for os_release_path in (
            self._os_release_path,
            self._os_release_fallback_path,
        ):
            try:
                os_release, malformed = _parse_os_release(
                    self._read_text(os_release_path)
                )
            except FileNotFoundError:
                continue
            except (OSError, UnicodeError):
                issues.append("Unable to read os-release.")
                continue
            distribution = _first_clean(
                os_release.get("NAME"), os_release.get("ID")
            )
            distribution_version = _first_clean(
                os_release.get("VERSION_ID"), os_release.get("VERSION")
            )
            if malformed:
                issues.append("Malformed os-release data.")
            break

        kernel_version = None
        hostname = None
        architecture = None
        try:
            uname = self._uname()
            kernel_version = _clean_text(uname.release)
            hostname = _clean_text(uname.nodename)
            architecture = _clean_text(uname.machine)
        except OSError:
            issues.append("Unable to query system identity.")

        def read_optional(path: Path, label: str) -> str | None:
            try:
                return _clean_text(self._read_text(path))
            except FileNotFoundError:
                return None
            except (OSError, UnicodeError):
                issues.append(f"Unable to read {label}.")
                return None

        manufacturer = read_optional(self._dmi_root / "sys_vendor", "system vendor")
        model = read_optional(self._dmi_root / "product_name", "system model")
        if model is None:
            model = read_optional(self._device_tree_model_path, "Device Tree model")

        baseboard = BaseboardInfo(
            manufacturer=read_optional(
                self._dmi_root / "board_vendor", "baseboard vendor"
            ),
            model=read_optional(self._dmi_root / "board_name", "baseboard model"),
            version=read_optional(
                self._dmi_root / "board_version", "baseboard version"
            ),
        )
        bios = BiosInfo(
            manufacturer=read_optional(
                self._dmi_root / "bios_vendor", "firmware vendor"
            ),
            version=read_optional(
                self._dmi_root / "bios_version", "firmware version"
            ),
            release_date=read_optional(
                self._dmi_root / "bios_date", "firmware release date"
            ),
        )
        value = SystemInfo(
            distribution=distribution,
            distribution_version=distribution_version,
            kernel_version=kernel_version,
            hostname=hostname,
            manufacturer=manufacturer,
            model=model,
            architecture=architecture,
            baseboard=baseboard,
            bios=bios,
        )
        useful = any(
            (
                distribution,
                distribution_version,
                kernel_version,
                hostname,
                manufacturer,
                model,
                architecture,
                baseboard.manufacturer,
                baseboard.model,
                baseboard.version,
                bios.manufacturer,
                bios.version,
                bios.release_date,
            )
        )
        if issues:
            if useful:
                return DetectionResult.partial(value, *dict.fromkeys(issues))
            return DetectionResult.unavailable(value, *dict.fromkeys(issues))
        if useful:
            return DetectionResult.complete(value)
        return DetectionResult.unavailable(value, "No system information was available.")
