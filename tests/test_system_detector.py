"""Tests for Linux system identity detection."""

import os
import tempfile
import unittest
from pathlib import Path

from hardware_validator.collector import DetectionStatus
from hardware_validator.detectors.system import SystemDetector


def fixture_uname(
    *, hostname: str = "test-host", release: str = "6.8.0", machine: str = "x86_64"
) -> os.uname_result:
    return os.uname_result(("Linux", hostname, release, "#1", machine))


class SystemDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name)
        self.dmi_root = self.root / "dmi"
        self.dmi_root.mkdir()

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def detector(self, **overrides: object) -> SystemDetector:
        arguments: dict[str, object] = {
            "os_release_path": self.root / "os-release",
            "os_release_fallback_path": self.root / "usr-lib-os-release",
            "dmi_root": self.dmi_root,
            "device_tree_model_path": self.root / "device-tree-model",
            "uname": fixture_uname,
        }
        arguments.update(overrides)
        return SystemDetector(**arguments)  # type: ignore[arg-type]

    def test_detects_distribution_system_baseboard_and_bios(self) -> None:
        (self.root / "os-release").write_text(
            'NAME="Fixture Linux"\nVERSION_ID="24.04"\n', encoding="utf-8"
        )
        values = {
            "sys_vendor": "Framework",
            "product_name": "Laptop 13",
            "board_vendor": "Framework",
            "board_name": "FRANBMCP",
            "board_version": "A7",
            "bios_vendor": "INSYDE",
            "bios_version": "03.05",
            "bios_date": "01/02/2026",
        }
        for name, value in values.items():
            (self.dmi_root / name).write_text(value, encoding="utf-8")

        result = self.detector().detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(result.value.distribution, "Fixture Linux")
        self.assertEqual(result.value.distribution_version, "24.04")
        self.assertEqual(result.value.kernel_version, "6.8.0")
        self.assertEqual(result.value.architecture, "x86_64")
        self.assertEqual(result.value.hostname, "test-host")
        self.assertEqual(result.value.manufacturer, "Framework")
        self.assertEqual(result.value.model, "Laptop 13")
        self.assertEqual(result.value.baseboard.model, "FRANBMCP")
        self.assertEqual(result.value.baseboard.version, "A7")
        self.assertEqual(result.value.bios.manufacturer, "INSYDE")
        self.assertEqual(result.value.bios.release_date, "01/02/2026")

    def test_uses_clean_os_release_and_device_tree_fallbacks(self) -> None:
        (self.root / "os-release").write_text(
            "NAME=unknown\nID=fixture\nVERSION_ID=unknown\nVERSION=rolling\n",
            encoding="utf-8",
        )
        (self.dmi_root / "product_name").write_text(
            "To Be Filled By O.E.M.\n", encoding="utf-8"
        )
        (self.root / "device-tree-model").write_text(
            "Fixture ARM Board\x00", encoding="utf-8"
        )

        result = self.detector().detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(result.value.distribution, "fixture")
        self.assertEqual(result.value.distribution_version, "rolling")
        self.assertEqual(result.value.model, "Fixture ARM Board")

    def test_missing_optional_files_are_normal_but_no_data_is_unavailable(self) -> None:
        empty_uname = lambda: fixture_uname(hostname="", release="", machine="")

        result = self.detector(uname=empty_uname).detect()

        self.assertIs(result.status, DetectionStatus.UNAVAILABLE)
        self.assertEqual(result.issues, ("No system information was available.",))

    def test_unreadable_reader_returns_partial_data_without_crashing(self) -> None:
        def unreadable(_path: Path) -> str:
            raise PermissionError("fixture")

        result = self.detector(read_text=unreadable).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertEqual(result.value.architecture, "x86_64")
        self.assertIn("Unable to read os-release.", result.issues)
        self.assertIn("Unable to read system vendor.", result.issues)

    def test_malformed_os_release_preserves_valid_fields_and_is_partial(self) -> None:
        (self.root / "os-release").write_text(
            'NAME="Fixture Linux"\nBROKEN LINE\nVERSION_ID="unterminated\n',
            encoding="utf-8",
        )

        result = self.detector().detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertEqual(result.value.distribution, "Fixture Linux")
        self.assertIsNone(result.value.distribution_version)
        self.assertEqual(result.issues, ("Malformed os-release data.",))

    def test_uses_standard_os_release_fallback(self) -> None:
        (self.root / "usr-lib-os-release").write_text(
            'NAME="Fallback Linux"\nVERSION_ID="1"\n', encoding="utf-8"
        )

        result = self.detector().detect()

        self.assertEqual(result.value.distribution, "Fallback Linux")
        self.assertEqual(result.value.distribution_version, "1")


if __name__ == "__main__":
    unittest.main()
