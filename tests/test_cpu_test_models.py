"""Tests for immutable CPU-test configuration and result models."""

from dataclasses import FrozenInstanceError
import math
import unittest

from hardware_validator.cpu_test import CpuTestConfig
from hardware_validator.cpu_test import CpuTestResult
from hardware_validator.cpu_test import CpuTestSample
from hardware_validator.cpu_test import CpuTestStatus
from hardware_validator.cpu_test import normalize_load
from hardware_validator.cpu_test import normalize_temperature
from hardware_validator.cpu_test import parse_duration


def result(status: CpuTestStatus = CpuTestStatus.COMPLETED) -> CpuTestResult:
    return CpuTestResult(
        status=status,
        reason="fixture result",
        requested_duration_seconds=10,
        actual_duration_seconds=1.5,
        requested_load_percent=20,
        worker_count=1,
        sample_count=0,
        utilization_average_percent=None,
        utilization_minimum_percent=None,
        utilization_maximum_percent=None,
        initial_temperature_celsius=None,
        maximum_temperature_celsius=None,
        initial_frequency_hz=None,
        minimum_frequency_hz=None,
        maximum_frequency_hz=None,
    )


class CpuTestModelTests(unittest.TestCase):
    def test_duration_units_and_boundaries(self) -> None:
        self.assertEqual(parse_duration("10s"), 10)
        self.assertEqual(parse_duration("10m"), 600)
        self.assertEqual(parse_duration("1h"), 3600)
        self.assertEqual(parse_duration("1440m"), 86_400)
        self.assertEqual(parse_duration("24h"), 86_400)

    def test_invalid_duration_formats_and_ranges(self) -> None:
        for value in (
            "9s",
            "25h",
            "0s",
            "+10s",
            "10",
            "10S",
            "1.5m",
            "1h30m",
            " 10s",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_duration(value)

    def test_load_boundaries_and_rejection(self) -> None:
        self.assertEqual(normalize_load("10"), 10)
        self.assertEqual(normalize_load(90), 90)
        for value in (9, 91, 100, True, 60.0, "1.5", " 60"):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                normalize_load(value)  # type: ignore[arg-type]

    def test_temperature_boundaries_and_non_finite_rejection(self) -> None:
        self.assertEqual(normalize_temperature("50"), 50.0)
        self.assertEqual(normalize_temperature("5e1"), 50.0)
        self.assertEqual(normalize_temperature("+50"), 50.0)
        self.assertEqual(normalize_temperature(85.5), 85.5)
        self.assertEqual(normalize_temperature(100), 100.0)
        for value in (49, 101, True, math.inf, math.nan, "nan"):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                normalize_temperature(value)  # type: ignore[arg-type]

    def test_configuration_defaults_and_normalization(self) -> None:
        self.assertEqual(CpuTestConfig(), CpuTestConfig.from_options())
        config = CpuTestConfig.from_options(
            duration="2m",
            load="25",
            maximum_temperature="70.5",
            allow_no_temperature=True,
            verbose=True,
        )
        self.assertEqual(config.duration_seconds, 120)
        self.assertEqual(config.load_percent, 25)
        self.assertEqual(config.maximum_temperature_celsius, 70.5)
        self.assertTrue(config.allow_no_temperature)
        self.assertTrue(config.verbose)

    def test_configuration_is_immutable_and_rejects_invalid_direct_values(self) -> None:
        config = CpuTestConfig()
        with self.assertRaises(FrozenInstanceError):
            config.load_percent = 20  # type: ignore[misc]
        for kwargs in (
            {"duration_seconds": 9},
            {"duration_seconds": 86_401},
            {"load_percent": 100},
            {"maximum_temperature_celsius": math.inf},
            {"allow_no_temperature": 1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises((TypeError, ValueError)):
                CpuTestConfig(**kwargs)  # type: ignore[arg-type]

    def test_sample_accepts_zero_and_rejects_non_finite_values(self) -> None:
        sample = CpuTestSample(
            elapsed_seconds=0.0,
            total_utilization_percent=0.0,
            workers_alive=1,
            worker_count=1,
        )
        self.assertEqual(sample.total_utilization_percent, 0.0)
        with self.assertRaises(ValueError):
            CpuTestSample(
                elapsed_seconds=1,
                total_utilization_percent=math.nan,
            )

    def test_every_public_test_status_is_independent(self) -> None:
        self.assertEqual(
            tuple(status.value for status in CpuTestStatus),
            ("completed", "cancelled", "temperature_limit", "error"),
        )
        for status in CpuTestStatus:
            self.assertIs(result(status).status, status)

    def test_result_unknown_statistics_require_zero_samples(self) -> None:
        self.assertIsNone(result().utilization_average_percent)
        with self.assertRaises(ValueError):
            CpuTestResult(
                status=CpuTestStatus.COMPLETED,
                reason="fixture",
                requested_duration_seconds=10,
                actual_duration_seconds=10,
                requested_load_percent=20,
                worker_count=1,
                sample_count=1,
                utilization_average_percent=None,
                utilization_minimum_percent=None,
                utilization_maximum_percent=None,
                initial_temperature_celsius=None,
                maximum_temperature_celsius=None,
                initial_frequency_hz=None,
                minimum_frequency_hz=None,
                maximum_frequency_hz=None,
            )


if __name__ == "__main__":
    unittest.main()
