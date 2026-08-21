"""Tests for non-invasive Linux GPU detection."""

import tempfile
import unittest
from pathlib import Path

from hardware_validator.collector import DetectionStatus
from hardware_validator.detectors.gpu import GpuDetector


class GpuFixture:
    def __init__(self, root: Path) -> None:
        self.drm = root / "drm"
        self.pci = root / "pci"
        self.drivers = root / "drivers"
        self.drm.mkdir()
        self.pci.mkdir()
        self.drivers.mkdir()

    def add_pci(
        self,
        address: str,
        *,
        class_code: str = "0x030000",
        vendor: str | None = None,
        device: str | None = None,
        driver: str | None = None,
        attributes: dict[str, str] | None = None,
    ) -> Path:
        path = self.pci / address
        path.mkdir()
        (path / "class").write_text(class_code, encoding="utf-8")
        if vendor is not None:
            (path / "vendor").write_text(vendor, encoding="utf-8")
        if device is not None:
            (path / "device").write_text(device, encoding="utf-8")
        if driver is not None:
            driver_path = self.drivers / driver
            driver_path.mkdir(exist_ok=True)
            (path / "driver").symlink_to(driver_path, target_is_directory=True)
        for name, value in (attributes or {}).items():
            (path / name).write_text(value, encoding="utf-8")
        return path

    def add_card(self, name: str, device: Path | None) -> Path:
        card = self.drm / name
        card.mkdir()
        if device is not None:
            (card / "device").symlink_to(device, target_is_directory=True)
        return card

    def add_connector(self, name: str, card: Path) -> None:
        connector = self.drm / name
        connector.mkdir()
        (connector / "device").symlink_to(card, target_is_directory=True)

    def detector(self, **kwargs: object) -> GpuDetector:
        return GpuDetector(drm_root=self.drm, pci_root=self.pci, **kwargs)


class GpuDetectorTests(unittest.TestCase):
    def test_igpu_has_unknown_classification_without_explicit_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = GpuFixture(Path(temporary))
            device = fixture.add_pci(
                "0000:00:02.0",
                vendor="0x8086",
                device="0x46a6",
                driver="i915",
                attributes={"boot_vga": "1"},
            )
            fixture.add_card("card0", device)

            result = fixture.detector().detect()

            self.assertIs(result.status, DetectionStatus.COMPLETE)
            self.assertEqual(len(result.value.devices), 1)
            gpu = result.value.devices[0]
            self.assertEqual(gpu.vendor_id, "0x8086")
            self.assertEqual(gpu.driver, "i915")
            self.assertEqual(gpu.address, "0000:00:02.0")
            self.assertIsNone(gpu.is_primary)
            self.assertTrue(gpu.is_boot_vga)
            self.assertIsNone(gpu.adapter_type)

    def test_amdgpu_documented_board_evidence_establishes_dedicated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = GpuFixture(Path(temporary))
            device = fixture.add_pci(
                "0000:03:00.0",
                vendor="0X1002",
                device="73BF",
                driver="amdgpu",
                attributes={
                    "board_info": "type : CEM\n",
                    "product_name": "Instinct Fixture\n",
                    "mem_info_vram_total": "17179869184\n",
                    "boot_vga": "0",
                },
            )
            fixture.add_card("card2", device)

            gpu = fixture.detector().detect().value.devices[0]

            self.assertEqual(gpu.adapter_type, "dedicated")
            self.assertEqual(gpu.commercial_name, "Instinct Fixture")
            self.assertEqual(gpu.memory_total_bytes, 17_179_869_184)
            self.assertIsNone(gpu.is_primary)
            self.assertFalse(gpu.is_boot_vga)
            self.assertEqual(gpu.vendor_id, "0x1002")
            self.assertEqual(gpu.device_id, "0x73bf")

    def test_multiple_gpus_are_merged_and_ordered_by_canonical_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = GpuFixture(Path(temporary))
            later = fixture.add_pci("0000:05:00.0", vendor="0x10de")
            earlier = fixture.add_pci("0000:00:02.0", vendor="0x8086")
            fixture.add_card("card0", later)
            fixture.add_card("card8", earlier)

            devices = fixture.detector().detect().value.devices

            self.assertEqual(
                tuple(device.address for device in devices),
                ("0000:00:02.0", "0000:05:00.0"),
            )

    def test_unnamed_pci_ids_are_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = GpuFixture(Path(temporary))
            fixture.add_pci("0000:01:00.0", vendor="0x1234", device="0xabcd")

            result = fixture.detector().detect()

            self.assertIs(result.status, DetectionStatus.COMPLETE)
            gpu = result.value.devices[0]
            self.assertIsNone(gpu.commercial_name)
            self.assertEqual((gpu.vendor_id, gpu.device_id), ("0x1234", "0xabcd"))

    def test_empty_available_sources_produce_complete_empty_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drm = root / "drm"
            pci = root / "pci"
            drm.mkdir()
            pci.mkdir()

            result = GpuDetector(drm_root=drm, pci_root=pci).detect()

            self.assertIs(result.status, DetectionStatus.COMPLETE)
            self.assertEqual(result.value.devices, ())

    def test_connectors_are_associated_without_becoming_devices(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = GpuFixture(Path(temporary))
            device = fixture.add_pci("0000:04:00.0")
            card = fixture.add_card("card1", device)
            fixture.add_connector("card1-HDMI-A-1", card)
            fixture.add_connector("card1-DP-2", card)
            (fixture.drm / "renderD128").mkdir()

            devices = fixture.detector().detect().value.devices

            self.assertEqual(len(devices), 1)
            self.assertEqual(
                devices[0].connectors, ("card1-DP-2", "card1-HDMI-A-1")
            )

    def test_card_without_backing_device_is_not_invented(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = GpuFixture(Path(temporary))
            fixture.add_card("card0", None)
            (fixture.drm / "card0-DP-1").mkdir()

            result = fixture.detector().detect()

            self.assertIs(result.status, DetectionStatus.UNAVAILABLE)
            self.assertEqual(result.value.devices, ())
            self.assertIn("DRM card has no canonical backing device.", result.issues)

    def test_missing_discovery_sources_are_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            result = GpuDetector(
                drm_root=root / "missing-drm", pci_root=root / "missing-pci"
            ).detect()

            self.assertIs(result.status, DetectionStatus.UNAVAILABLE)
            self.assertEqual(result.value.devices, ())

    def test_permission_and_malformed_attempted_values_are_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = GpuFixture(Path(temporary))
            device = fixture.add_pci(
                "0000:02:00.0",
                vendor="not-an-id",
                attributes={"mem_info_vram_total": "-1"},
            )

            def restricted_reader(path: Path) -> str:
                if path == device / "boot_vga":
                    raise PermissionError("fixture")
                return path.read_text(encoding="utf-8")

            result = fixture.detector(read_text=restricted_reader).detect()

            self.assertIs(result.status, DetectionStatus.PARTIAL)
            self.assertIsNone(result.value.devices[0].vendor_id)
            self.assertIsNone(result.value.devices[0].memory_total_bytes)
            self.assertIn("Malformed GPU vendor ID.", result.issues)
            self.assertIn("Unable to read GPU boot_vga.", result.issues)

    def test_driver_specific_vram_is_not_assumed_for_other_drivers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = GpuFixture(Path(temporary))
            fixture.add_pci(
                "0000:00:02.0",
                driver="i915",
                attributes={"mem_info_vram_total": "not-amdgpu-data"},
            )

            result = fixture.detector().detect()

            self.assertIs(result.status, DetectionStatus.COMPLETE)
            self.assertIsNone(result.value.devices[0].memory_total_bytes)


if __name__ == "__main__":
    unittest.main()
