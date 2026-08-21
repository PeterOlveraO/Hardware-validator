"""Tests for Linux CPU identity and topology detection."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hardware_validator.collector import DetectionStatus
from hardware_validator.detectors.cpu import CpuDetector
from hardware_validator.models import CpuCacheInfo


def fixture_uname(machine: str = "x86_64") -> os.uname_result:
    return os.uname_result(("Linux", "test-host", "6.8.0", "#1", machine))


class CpuDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name)
        self.cpuinfo_path = self.root / "cpuinfo"
        self.sys_cpu_root = self.root / "sys-cpu"
        self.sys_cpu_root.mkdir()

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def detector(self, **overrides: object) -> CpuDetector:
        arguments: dict[str, object] = {
            "cpuinfo_path": self.cpuinfo_path,
            "sys_cpu_root": self.sys_cpu_root,
            "uname": fixture_uname,
            "cpu_count": lambda *, logical: 8 if logical else 4,
            "cpu_frequency": lambda *, percpu: None,
        }
        arguments.update(overrides)
        return CpuDetector(**arguments)  # type: ignore[arg-type]

    def write_package(self, cpu_id: int, package_id: int) -> None:
        topology = self.sys_cpu_root / f"cpu{cpu_id}" / "topology"
        topology.mkdir(parents=True, exist_ok=True)
        (topology / "physical_package_id").write_text(
            str(package_id), encoding="utf-8"
        )

    def write_cache(
        self,
        cpu_id: int,
        index: int,
        *,
        level: str,
        kind: str,
        size: str | None,
    ) -> None:
        root = self.sys_cpu_root / f"cpu{cpu_id}" / "cache" / f"index{index}"
        root.mkdir(parents=True, exist_ok=True)
        (root / "level").write_text(level, encoding="utf-8")
        (root / "type").write_text(kind, encoding="utf-8")
        if size is not None:
            (root / "size").write_text(size, encoding="utf-8")

    def test_detects_x86_identity_counts_and_deduplicated_capabilities(self) -> None:
        self.cpuinfo_path.write_text(
            """processor : 0
vendor_id : GenuineIntel
model name : Fixture CPU
flags : fpu sse sse2

processor : 1
vendor_id : GenuineIntel
model name : Fixture CPU
flags : fpu sse2 avx
""",
            encoding="utf-8",
        )

        result = self.detector().detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(result.value.model_names, ("Fixture CPU",))
        self.assertEqual(result.value.vendor_ids, ("GenuineIntel",))
        self.assertEqual(result.value.architecture, "x86_64")
        self.assertEqual(result.value.logical_processor_count, 8)
        self.assertEqual(result.value.physical_core_count, 4)
        self.assertEqual(result.value.capabilities, ("fpu", "sse", "sse2", "avx"))

    def test_detects_arm_implementer_part_architecture_and_features(self) -> None:
        self.cpuinfo_path.write_text(
            """processor : 0
Features : fp asimd evtstrm
CPU implementer : 0x41
CPU architecture : 8
CPU part : 0xd03

processor : 1
Features : fp asimd
CPU implementer : 0x41
CPU architecture : 8
CPU part : 0xd03
""",
            encoding="utf-8",
        )

        result = self.detector(uname=lambda: fixture_uname("aarch64")).detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(result.value.model_names, ("0xd03",))
        self.assertEqual(result.value.vendor_ids, ("0x41",))
        self.assertEqual(result.value.architecture, "aarch64")
        self.assertEqual(result.value.capabilities, ("fp", "asimd", "evtstrm"))

    def test_counts_multiple_packages_only_with_complete_present_topology(self) -> None:
        (self.sys_cpu_root / "present").write_text("0-3", encoding="utf-8")
        for cpu_id, package_id in enumerate((0, 0, 7, 7)):
            self.write_package(cpu_id, package_id)

        result = self.detector().detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(result.value.physical_package_count, 2)

    def test_does_not_report_package_count_from_incomplete_topology(self) -> None:
        (self.sys_cpu_root / "present").write_text("0-3", encoding="utf-8")
        for cpu_id, package_id in enumerate((0, 0, 1)):
            self.write_package(cpu_id, package_id)

        result = self.detector().detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertIsNone(result.value.physical_package_count)
        self.assertIn("Incomplete physical package topology.", result.issues)

    def test_missing_sources_with_no_useful_values_are_unavailable(self) -> None:
        empty_uname = lambda: fixture_uname("")
        no_count = lambda *, logical: None

        result = self.detector(uname=empty_uname, cpu_count=no_count).detect()

        self.assertIs(result.status, DetectionStatus.UNAVAILABLE)
        self.assertEqual(result.issues, ("No CPU information was available.",))

    def test_unreadable_reader_preserves_independent_data_and_is_partial(self) -> None:
        def unreadable(_path: Path) -> str:
            raise PermissionError("fixture")

        result = self.detector(read_text=unreadable).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertEqual(result.value.architecture, "x86_64")
        self.assertIn("Unable to read CPU information.", result.issues)
        self.assertIn("Unable to read present CPU topology.", result.issues)

    def test_malformed_cache_is_ignored_and_marks_partial(self) -> None:
        (self.sys_cpu_root / "present").write_text("0", encoding="utf-8")
        self.write_cache(0, 0, level="1", kind="Data", size="not-a-size")

        result = self.detector().detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertEqual(result.value.caches, ())
        self.assertIn("Malformed CPU cache data.", result.issues)

    def test_caches_are_deduplicated_and_deterministically_ordered(self) -> None:
        (self.sys_cpu_root / "present").write_text("0-1", encoding="utf-8")
        for cpu_id in (0, 1):
            self.write_cache(cpu_id, 2, level="3", kind="Unified", size="8M")
            self.write_cache(cpu_id, 1, level="1", kind="Instruction", size="32K")
            self.write_cache(cpu_id, 0, level="1", kind="Data", size="48K")

        result = self.detector().detect()

        self.assertEqual(
            result.value.caches,
            (
                CpuCacheInfo(level=1, kind="data", size_bytes=48 * 1024),
                CpuCacheInfo(level=1, kind="instruction", size_bytes=32 * 1024),
                CpuCacheInfo(level=3, kind="unified", size_bytes=8 * 1024**2),
            ),
        )

    def test_frequency_mhz_is_converted_to_hz(self) -> None:
        frequencies = (
            SimpleNamespace(current=1250.0, max=3800.0),
            SimpleNamespace(current=1750.0, max=4000.0),
        )

        result = self.detector(
            cpu_frequency=lambda *, percpu: frequencies
        ).detect()

        self.assertEqual(result.value.current_frequency_hz, 1_500_000_000)
        self.assertEqual(result.value.maximum_frequency_hz, 4_000_000_000)

    def test_physical_count_is_not_replaced_by_logical_count(self) -> None:
        count = lambda *, logical: 6 if logical else None

        result = self.detector(cpu_count=count).detect()

        self.assertEqual(result.value.logical_processor_count, 6)
        self.assertIsNone(result.value.physical_core_count)


if __name__ == "__main__":
    unittest.main()
