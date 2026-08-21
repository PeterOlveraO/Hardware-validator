"""Tests for Linux RAM, swap, and EDAC memory detection."""

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from hardware_validator.collector import DetectionStatus
from hardware_validator.detectors.memory import MemoryDetector


def memory_values(
    *, total: object = 1_000, available: object = 400, free: object = 200
) -> SimpleNamespace:
    return SimpleNamespace(total=total, available=available, free=free, used=999)


def swap_values(
    *, total: object = 500, used: object = 200, free: object = 300
) -> SimpleNamespace:
    return SimpleNamespace(total=total, used=used, free=free)


class MemoryDetectorTests(unittest.TestCase):
    def detector(
        self,
        root: Path,
        *,
        virtual_memory: object | None = None,
        swap_memory: object | None = None,
        read_text: object | None = None,
    ) -> MemoryDetector:
        return MemoryDetector(
            virtual_memory=virtual_memory or (lambda: memory_values()),  # type: ignore[arg-type]
            swap_memory=swap_memory or (lambda: swap_values()),  # type: ignore[arg-type]
            edac_root=root,
            read_text=read_text or (lambda path: path.read_text(encoding="utf-8")),  # type: ignore[arg-type]
        )

    def test_complete_ram_and_swap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self.detector(Path(directory) / "missing-edac").detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(result.value.total_bytes, 1_000)
        self.assertEqual(result.value.available_bytes, 400)
        self.assertEqual(result.value.used_bytes, 600)
        self.assertEqual(result.value.free_bytes, 200)
        self.assertEqual(result.value.swap.total_bytes, 500)
        self.assertEqual(result.value.swap.available_bytes, 300)
        self.assertEqual(result.value.swap.used_bytes, 200)

    def test_no_swap_is_valid_complete_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            detector = self.detector(
                Path(directory) / "missing-edac",
                swap_memory=lambda: swap_values(total=0, used=0, free=0),
            )
            result = detector.detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(result.value.swap.total_bytes, 0)
        self.assertEqual(result.value.swap.available_bytes, 0)
        self.assertEqual(result.value.swap.used_bytes, 0)

    def test_malformed_psutil_values_are_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            detector = self.detector(
                Path(directory) / "missing-edac",
                virtual_memory=lambda: memory_values(total=100, available=101),
                swap_memory=lambda: swap_values(total=10, used=7, free=4),
            )
            result = detector.detect()

        self.assertIs(result.status, DetectionStatus.UNAVAILABLE)
        self.assertIsNone(result.value.total_bytes)
        self.assertIsNone(result.value.swap.total_bytes)
        self.assertEqual(
            result.issues,
            ("Malformed RAM information.", "Malformed swap information."),
        )

    def test_missing_module_fields_remain_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dimm = root / "mc0" / "dimm0"
            dimm.mkdir(parents=True)
            (dimm / "size").write_text("256\n", encoding="utf-8")

            result = self.detector(root).detect()

        self.assertIs(result.status, DetectionStatus.COMPLETE)
        self.assertEqual(len(result.value.modules), 1)
        module = result.value.modules[0]
        self.assertIsNone(module.locator)
        self.assertIsNone(module.manufacturer)
        self.assertIsNone(module.part_number)
        self.assertIsNone(module.serial_number)
        self.assertIsNone(module.memory_type)
        self.assertIsNone(module.speed_mt_s)

    def test_malformed_module_size_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dimm = root / "mc0" / "dimm0"
            dimm.mkdir(parents=True)
            (dimm / "dimm_label").write_text("DIMM_A1\n", encoding="utf-8")
            (dimm / "size").write_text("8 GiB\n", encoding="utf-8")

            result = self.detector(root).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertEqual(result.value.modules[0].locator, "DIMM_A1")
        self.assertIsNone(result.value.modules[0].size_bytes)
        self.assertEqual(result.issues, ("Malformed EDAC memory module size.",))

    def test_module_permission_errors_are_partial(self) -> None:
        def denied(_: Path) -> str:
            raise PermissionError("fixture")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "mc0" / "dimm0").mkdir(parents=True)

            result = self.detector(root, read_text=denied).detect()

        self.assertIs(result.status, DetectionStatus.PARTIAL)
        self.assertEqual(len(result.value.modules), 1)
        self.assertIsNone(result.value.modules[0].locator)
        self.assertEqual(
            result.issues, ("Unable to read EDAC memory module data.",)
        )

    def test_edac_mib_conversion_and_order_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for controller, label, size in (
                ("mc1", "DIMM_B1", "1024"),
                ("mc0", "DIMM_A1", "2048"),
            ):
                dimm = root / controller / "dimm0"
                dimm.mkdir(parents=True)
                (dimm / "dimm_location").write_text(label, encoding="utf-8")
                (dimm / "size").write_text(size, encoding="utf-8")

            result = self.detector(root).detect()

        self.assertEqual(
            tuple(module.locator for module in result.value.modules),
            ("DIMM_A1", "DIMM_B1"),
        )
        self.assertEqual(result.value.modules[0].size_bytes, 2048 * 1024**2)
        self.assertEqual(result.value.modules[1].size_bytes, 1024 * 1024**2)


if __name__ == "__main__":
    unittest.main()
