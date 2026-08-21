"""Tests for conservative local temperature monitoring."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hardware_validator.cpu_test.temperature import LocalTemperatureMonitor


def sensor(
    current: object,
    *,
    label: object = "Package",
    high: object = None,
    critical: object = None,
) -> object:
    return SimpleNamespace(
        current=current,
        label=label,
        high=high,
        critical=critical,
    )


class CpuTestTemperatureTests(unittest.TestCase):
    def test_psutil_readings_preserve_names_and_use_deterministic_order(self) -> None:
        monitor = LocalTemperatureMonitor(
            sensors_temperatures=lambda *, fahrenheit: {
                "z-chip": [sensor(50, label="Second")],
                "a-chip": [sensor(40, label="First", critical=90)],
            },
            thermal_root=Path("/missing"),
        )

        result = monitor.read()

        self.assertEqual(
            tuple(item.chip_name for item in result.readings),
            ("a-chip", "z-chip"),
        )
        self.assertEqual(result.readings[0].label, "First")
        self.assertEqual(result.readings[0].critical_celsius, 90.0)
        self.assertFalse(result.readings[0].cpu_attributed)

    def test_only_non_finite_or_unusable_values_are_ignored(self) -> None:
        monitor = LocalTemperatureMonitor(
            sensors_temperatures=lambda **_kwargs: {
                "chip": [
                    sensor(0, label="zero"),
                    sensor(float("nan"), label="bad"),
                    sensor(True, label="bool"),
                    sensor("50", label="text"),
                ]
            },
            thermal_root=Path("/missing"),
        )

        result = monitor.read()

        self.assertEqual(len(result.readings), 1)
        self.assertEqual(result.readings[0].current_celsius, 0.0)
        self.assertIn("Invalid temperature value for chip.", result.warnings)

    def test_empty_psutil_source_falls_back_to_thermal_sysfs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            later = root / "thermal_zone10"
            earlier = root / "thermal_zone2"
            for zone, zone_type, value in (
                (later, "other", "51000"),
                (earlier, "x86_pkg_temp", "42000"),
            ):
                zone.mkdir()
                (zone / "type").write_text(zone_type, encoding="utf-8")
                (zone / "temp").write_text(value, encoding="utf-8")
            (earlier / "trip_point_1_type").write_text(
                "hot", encoding="utf-8"
            )
            (earlier / "trip_point_1_temp").write_text(
                "70000", encoding="utf-8"
            )
            (earlier / "trip_point_0_type").write_text(
                "critical", encoding="utf-8"
            )
            (earlier / "trip_point_0_temp").write_text(
                "80000", encoding="utf-8"
            )

            result = LocalTemperatureMonitor(
                sensors_temperatures=lambda **_kwargs: {}, thermal_root=root
            ).read()

        self.assertEqual(
            tuple(item.identity for item in result.readings),
            ("thermal:thermal_zone10", "thermal:thermal_zone2"),
        )
        package = next(item for item in result.readings if item.label == "thermal_zone2")
        self.assertEqual(package.current_celsius, 42.0)
        self.assertEqual(package.critical_celsius, 80.0)
        self.assertTrue(package.cpu_attributed)

    def test_missing_sources_return_known_empty_round(self) -> None:
        result = LocalTemperatureMonitor(
            sensors_temperatures=lambda **_kwargs: {},
            thermal_root=Path("/definitely/missing"),
        ).read()
        self.assertEqual(result.readings, ())

    def test_psutil_source_prevents_cross_source_duplicate_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            zone = Path(directory) / "thermal_zone0"
            zone.mkdir()
            (zone / "temp").write_text("90000", encoding="utf-8")
            result = LocalTemperatureMonitor(
                sensors_temperatures=lambda **_kwargs: {
                    "chip": [sensor(40, label="package")]
                },
                thermal_root=Path(directory),
            ).read()

        self.assertEqual(len(result.readings), 1)
        self.assertEqual(result.readings[0].source, "psutil")
        self.assertEqual(result.readings[0].current_celsius, 40.0)

    def test_duplicate_label_identity_does_not_shift_when_one_value_is_invalid(self) -> None:
        rounds = iter(
            (
                {
                    "chip": [
                        sensor(40, label="duplicate"),
                        sensor(50, label="duplicate"),
                    ]
                },
                {
                    "chip": [
                        sensor(float("nan"), label="duplicate"),
                        sensor(51, label="duplicate"),
                    ]
                },
            )
        )
        monitor = LocalTemperatureMonitor(
            sensors_temperatures=lambda **_kwargs: next(rounds),
            thermal_root=Path("/missing"),
        )
        first = monitor.read()
        second = monitor.read()
        self.assertEqual(
            tuple(item.identity for item in first.readings),
            ("psutil:chip:duplicate:0", "psutil:chip:duplicate:1"),
        )
        self.assertEqual(
            tuple(item.identity for item in second.readings),
            ("psutil:chip:duplicate:1",),
        )


if __name__ == "__main__":
    unittest.main()
