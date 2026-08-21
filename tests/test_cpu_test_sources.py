"""Tests for CPU measurement and incremental statistics."""

from types import SimpleNamespace
import unittest

from hardware_validator.cpu_test.sources import CpuSourceError
from hardware_validator.cpu_test.sources import CpuMetricSample
from hardware_validator.cpu_test.sources import PsutilCpuSource
from hardware_validator.cpu_test.statistics import NumericAccumulator


class CpuTestSourceTests(unittest.TestCase):
    def test_logical_processor_count_requires_positive_integer(self) -> None:
        self.assertEqual(
            PsutilCpuSource(cpu_count=lambda *, logical: 1).logical_processor_count(),
            1,
        )
        for value in (None, 0, -1, True, 1.0, "1"):
            with self.subTest(value=value), self.assertRaises(CpuSourceError):
                PsutilCpuSource(
                    cpu_count=lambda *, logical, value=value: value
                ).logical_processor_count()

    def test_utilization_is_primed_and_first_values_are_discarded(self) -> None:
        calls: list[tuple[object, object]] = []

        def cpu_percent(*, interval: object, percpu: object) -> object:
            calls.append((interval, percpu))
            return (99.0,) if percpu else 99.0

        source = PsutilCpuSource(cpu_percent=cpu_percent)
        source.prime_utilization(per_processor=True)

        self.assertEqual(calls, [(None, False), (None, True)])

    def test_sample_accepts_zero_and_converts_frequency_to_hertz(self) -> None:
        values = iter((0.0, (0.0, 50.0)))
        source = PsutilCpuSource(
            cpu_percent=lambda **_kwargs: next(values),
            cpu_frequency=lambda *, percpu: (
                SimpleNamespace(current=1000.0),
                SimpleNamespace(current=2000.0),
                SimpleNamespace(current=0.0),
            ),
        )

        sample = source.sample(per_processor=True)

        self.assertEqual(sample.total_utilization_percent, 0.0)
        self.assertEqual(sample.per_processor_utilization_percent, (0.0, 50.0))
        self.assertEqual(sample.aggregate_frequency_hz, 1_500_000_000.0)
        self.assertEqual(
            sample.per_processor_frequency_hz,
            (1_000_000_000.0, 2_000_000_000.0, None),
        )

    def test_invalid_total_utilization_is_required_measurement_error(self) -> None:
        for value in (-1, 101, True, float("nan"), "20"):
            with self.subTest(value=value), self.assertRaises(CpuSourceError):
                PsutilCpuSource(
                    cpu_percent=lambda **_kwargs: value
                ).sample(per_processor=False)

    def test_missing_frequency_is_nonfatal(self) -> None:
        sample = PsutilCpuSource(
            cpu_percent=lambda **_kwargs: 20.0,
            cpu_frequency=lambda **_kwargs: None,
        ).sample(per_processor=False)
        self.assertIsNone(sample.aggregate_frequency_hz)
        self.assertIsNone(sample.per_processor_frequency_hz)

    def test_malformed_optional_frequency_is_unknown(self) -> None:
        class BrokenFrequency:
            @property
            def current(self):
                raise ValueError("fixture")

        source = PsutilCpuSource(
            cpu_percent=lambda **_kwargs: 20.0,
            cpu_frequency=lambda **_kwargs: (BrokenFrequency(),),
        )
        sample = source.sample(per_processor=False)
        self.assertIsNone(sample.aggregate_frequency_hz)
        self.assertEqual(sample.per_processor_frequency_hz, (None,))

    def test_metric_sample_rejects_non_finite_or_out_of_range_values(self) -> None:
        for value in (float("nan"), -1, 101):
            with self.subTest(value=value), self.assertRaises(CpuSourceError):
                CpuMetricSample(value, None, None, None)

    def test_statistics_with_zero_one_and_multiple_samples(self) -> None:
        values = NumericAccumulator()
        self.assertEqual(values.count, 0)
        self.assertIsNone(values.average)
        self.assertIsNone(values.minimum)
        self.assertIsNone(values.maximum)

        values.add(0.0)
        self.assertEqual((values.average, values.minimum, values.maximum), (0, 0, 0))
        values.add(50.0)
        values.add(100.0)
        self.assertEqual(values.count, 3)
        self.assertEqual(values.average, 50.0)
        self.assertEqual(values.minimum, 0.0)
        self.assertEqual(values.maximum, 100.0)


if __name__ == "__main__":
    unittest.main()
