//! GPUs from DRM and PCI sysfs. Connectors are attached to their GPU and
//! never counted as devices. Runs no vendor tools.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use crate::collector::{DetectionResult, Issues};
use crate::host::Host;
use crate::models::{AdapterType, GpuInfo, GpuInventory};
use crate::text::read_optional;

pub fn detect(host: &Host) -> DetectionResult<GpuInventory> {
    let mut issues = Issues::default();
    // Canonical device path -> connector names; the map keeps a stable order.
    let mut records: BTreeMap<PathBuf, BTreeSet<String>> = BTreeMap::new();

    let drm = list(&host.path("/sys/class/drm"), "DRM devices", &mut issues);
    for entry in drm.iter().flatten().filter(|entry| is_card(name(entry))) {
        match backing_device(entry, &mut issues) {
            Some(device) => {
                records.entry(device).or_default();
            }
            None => issues.push("DRM card has no canonical backing device."),
        }
    }

    let pci = list(&host.path("/sys/bus/pci/devices"), "PCI devices", &mut issues);
    for entry in pci.iter().flatten() {
        let Some(class) = optional(&entry.join("class"), "PCI class", &mut issues) else {
            continue;
        };
        let class = class.trim();
        let digits = class.strip_prefix("0x").filter(|digits| is_hex(digits, 6));
        let Some(digits) = digits else {
            issues.push("Malformed PCI class value.");
            continue;
        };
        // PCI base class 0x03 is "display controller".
        if !digits.starts_with("03") {
            continue;
        }
        if let Some(device) = resolve(entry, "PCI device path", &mut issues) {
            records.entry(device).or_default();
        }
    }

    for entry in drm.iter().flatten() {
        let Some((card, connector)) = name(entry).split_once('-') else { continue };
        if !is_card(card) || connector.is_empty() {
            continue;
        }
        let device = backing_device(entry, &mut issues);
        if let Some(connectors) = device.as_ref().and_then(|device| records.get_mut(device)) {
            connectors.insert(name(entry).to_owned());
        }
    }

    let devices: Vec<GpuInfo> = records
        .into_iter()
        .map(|(device, connectors)| read_gpu(&device, connectors, &mut issues))
        .collect();
    let found = !devices.is_empty();
    let value = GpuInventory { devices };
    if issues.is_empty() && drm.is_none() && pci.is_none() {
        return DetectionResult::unavailable(value, "No GPU discovery source was available.");
    }
    if issues.is_empty() {
        // Readable sources without GPUs are a known empty inventory.
        return DetectionResult::complete(value);
    }
    DetectionResult::from_issues(value, issues, found, "No GPU was detected.")
}

fn name(path: &Path) -> &str {
    path.file_name().and_then(|name| name.to_str()).unwrap_or_default()
}

fn is_card(name: &str) -> bool {
    name.strip_prefix("card")
        .is_some_and(|digits| !digits.is_empty() && digits.bytes().all(|b| b.is_ascii_digit()))
}

fn is_hex(value: &str, length: usize) -> bool {
    value.len() == length && value.bytes().all(|b| b.is_ascii_hexdigit())
}

/// Sorted entries, or `None` when the directory is missing or unreadable.
fn list(root: &Path, label: &str, issues: &mut Issues) -> Option<Vec<PathBuf>> {
    match fs::read_dir(root) {
        Ok(read) => {
            let mut entries: Vec<PathBuf> = read.filter_map(Result::ok).map(|e| e.path()).collect();
            entries.sort();
            Some(entries)
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => None,
        Err(_) => {
            issues.push(format!("Unable to list {label}."));
            None
        }
    }
}

fn resolve(path: &Path, label: &str, issues: &mut Issues) -> Option<PathBuf> {
    match fs::canonicalize(path) {
        Ok(resolved) => Some(resolved),
        Err(error) if error.kind() == io::ErrorKind::NotFound => None,
        Err(_) => {
            issues.push(format!("Unable to resolve {label}."));
            None
        }
    }
}

/// The PCI (or platform) device behind a DRM card or connector entry.
fn backing_device(drm_entry: &Path, issues: &mut Issues) -> Option<PathBuf> {
    let device = resolve(&drm_entry.join("device"), "DRM backing device", issues)?;
    if is_card(name(&device)) {
        return resolve(&device.join("device"), "DRM backing device", issues);
    }
    Some(device)
}

fn optional(path: &Path, label: &str, issues: &mut Issues) -> Option<String> {
    read_optional(path).unwrap_or_else(|_| {
        issues.push(format!("Unable to read {label}."));
        None
    })
}

fn read_gpu(device: &Path, connectors: BTreeSet<String>, issues: &mut Issues) -> GpuInfo {
    let mut id = |file: &str, label: &str| {
        let text = optional(&device.join(file), label, issues)?;
        let text = text.trim();
        let digits = text.strip_prefix("0x").or_else(|| text.strip_prefix("0X")).unwrap_or(text);
        if !is_hex(digits, 4) {
            issues.push(format!("Malformed {label}."));
            return None;
        }
        Some(format!("0x{}", digits.to_lowercase()))
    };
    let vendor_id = id("vendor", "GPU vendor ID");
    let device_id = id("device", "GPU device ID");
    let driver = resolve(&device.join("driver"), "GPU driver", issues)
        .map(|driver| name(&driver).to_owned())
        .filter(|driver| !driver.is_empty());

    let mut gpu = GpuInfo {
        vendor_id,
        device_id,
        is_boot_vga: boot_vga(device, issues),
        address: pci_address(name(device)),
        connectors: connectors.into_iter().collect(),
        ..GpuInfo::default()
    };
    // Only amdgpu documents these sysfs attributes; other drivers are not guessed.
    if driver.as_deref() == Some("amdgpu") {
        gpu.commercial_name = amdgpu_name(device, issues);
        gpu.adapter_type = amdgpu_adapter_type(device, issues);
        gpu.memory_total_bytes = amdgpu_vram(device, issues);
    }
    gpu.driver = driver;
    gpu
}

fn pci_address(name: &str) -> Option<String> {
    let short = match name.split_once(':') {
        Some((domain, rest)) if is_hex(domain, 4) && rest.contains(':') => rest,
        _ => name,
    };
    let valid = match short.as_bytes() {
        [b0, b1, b':', s0, s1, b'.', b'0'..=b'7'] => {
            [b0, b1, s0, s1].iter().all(|byte| byte.is_ascii_hexdigit())
        }
        _ => false,
    };
    valid.then(|| name.to_lowercase())
}

fn boot_vga(device: &Path, issues: &mut Issues) -> Option<bool> {
    match optional(&device.join("boot_vga"), "GPU boot_vga", issues)?.trim() {
        "0" => Some(false),
        "1" => Some(true),
        _ => {
            issues.push("Malformed GPU boot_vga.");
            None
        }
    }
}

fn amdgpu_name(device: &Path, issues: &mut Issues) -> Option<String> {
    let text = optional(&device.join("product_name"), "amdgpu product name", issues)?;
    let name = text.trim();
    if name.is_empty() {
        issues.push("Malformed amdgpu product name.");
        return None;
    }
    Some(name.to_owned())
}

/// `board_info` reports `type : cem|oam|unknown`; CEM and OAM are add-in boards.
fn amdgpu_adapter_type(device: &Path, issues: &mut Issues) -> Option<AdapterType> {
    let text = optional(&device.join("board_info"), "amdgpu board info", issues)?;
    let value = text
        .trim()
        .split_once(':')
        .filter(|(key, _)| key.trim() == "type")
        .map(|(_, value)| value.trim().to_lowercase());
    match value.as_deref() {
        Some("cem" | "oam") => Some(AdapterType::Dedicated),
        Some("unknown") => None,
        _ => {
            issues.push("Malformed amdgpu board info.");
            None
        }
    }
}

fn amdgpu_vram(device: &Path, issues: &mut Issues) -> Option<u64> {
    let text = optional(&device.join("mem_info_vram_total"), "GPU VRAM total", issues)?;
    let value = text.trim();
    let parsed = value.bytes().all(|b| b.is_ascii_digit()).then(|| value.parse().ok()).flatten();
    if parsed.is_none() {
        issues.push("Malformed GPU VRAM total.");
    }
    parsed
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::collector::DetectionStatus;
    use crate::testutil::Fixture;

    const IGPU: &str = "/sys/devices/pci0000:00/0000:00:02.0";
    const DGPU: &str = "/sys/devices/pci0000:00/0000:00:01.0/0000:01:00.0";

    fn pci_gpu(fixture: &Fixture, device: &str, vendor: &str, id: &str, driver: &str) {
        let address = device.rsplit('/').next().unwrap();
        fixture
            .write(&format!("{device}/class"), "0x030000\n")
            .write(&format!("{device}/vendor"), vendor)
            .write(&format!("{device}/device"), id)
            .write(&format!("{device}/boot_vga"), "0\n")
            .dir(&format!("/sys/bus/pci/drivers/{driver}"))
            .link(&format!("{device}/driver"), &format!("/sys/bus/pci/drivers/{driver}"))
            .link(&format!("/sys/bus/pci/devices/{address}"), device);
    }

    fn drm_card(fixture: &Fixture, card: &str, device: &str, connectors: &[&str]) {
        let card_path = format!("{device}/drm/{card}");
        fixture.dir(&card_path).link(&format!("/sys/class/drm/{card}"), &card_path);
        fixture.link(&format!("{card_path}/device"), device);
        for connector in connectors {
            let connector_path = format!("{card_path}/{card}-{connector}");
            fixture.dir(&connector_path);
            fixture.link(&format!("/sys/class/drm/{card}-{connector}"), &connector_path);
            fixture.link(&format!("{connector_path}/device"), &card_path);
        }
    }

    #[test]
    fn merges_drm_and_pci_and_attaches_connectors() {
        let fixture = Fixture::new();
        pci_gpu(&fixture, IGPU, "0x8086\n", "0x46A6\n", "i915");
        pci_gpu(&fixture, DGPU, "0x1002\n", "0x73df\n", "amdgpu");
        drm_card(&fixture, "card0", IGPU, &["eDP-1", "HDMI-A-1"]);
        drm_card(&fixture, "card1", DGPU, &["DP-1"]);
        fixture
            .write(&format!("{IGPU}/boot_vga"), "1\n")
            .write(&format!("{DGPU}/product_name"), "Radeon Example\n")
            .write(&format!("{DGPU}/board_info"), "type : cem\n")
            .write(&format!("{DGPU}/mem_info_vram_total"), "12884901888\n")
            .write("/sys/bus/pci/devices/0000:00:1f.3/class", "0x040300\n");

        let result = detect(&fixture.host());

        assert_eq!(result.status, DetectionStatus::Complete, "{:?}", result.issues);
        let [dgpu, igpu] = result.value.devices.as_slice() else { panic!() };
        assert_eq!(igpu.vendor_id.as_deref(), Some("0x8086"));
        assert_eq!(igpu.device_id.as_deref(), Some("0x46a6"));
        assert_eq!(igpu.driver.as_deref(), Some("i915"));
        assert_eq!(igpu.address.as_deref(), Some("0000:00:02.0"));
        assert_eq!(igpu.is_boot_vga, Some(true));
        assert_eq!(igpu.adapter_type, None, "integrated is never guessed");
        assert_eq!(igpu.commercial_name, None);
        assert_eq!(igpu.connectors, ["card0-HDMI-A-1", "card0-eDP-1"]);
        assert_eq!(dgpu.commercial_name.as_deref(), Some("Radeon Example"));
        assert_eq!(dgpu.adapter_type, Some(AdapterType::Dedicated));
        assert_eq!(dgpu.memory_total_bytes, Some(12884901888));
        assert_eq!(dgpu.connectors, ["card1-DP-1"]);
    }

    #[test]
    fn vram_is_not_assumed_for_other_drivers() {
        let fixture = Fixture::new();
        pci_gpu(&fixture, DGPU, "0x10de\n", "0x2684\n", "nouveau");
        fixture.write(&format!("{DGPU}/mem_info_vram_total"), "1024\n");
        let gpu = &detect(&fixture.host()).value.devices[0];
        assert_eq!(gpu.memory_total_bytes, None);
        assert_eq!(gpu.driver.as_deref(), Some("nouveau"));
    }

    #[test]
    fn sources_missing_empty_and_malformed() {
        assert_eq!(detect(&Fixture::new().host()).status, DetectionStatus::Unavailable);

        let empty = Fixture::new();
        empty.dir("/sys/class/drm").dir("/sys/bus/pci/devices");
        let result = detect(&empty.host());
        assert_eq!(result.status, DetectionStatus::Complete);
        assert!(result.value.devices.is_empty());

        let fixture = Fixture::new();
        pci_gpu(&fixture, IGPU, "0x8086\n", "nonsense\n", "i915");
        fixture.write("/sys/bus/pci/devices/0000:00:1f.3/class", "display\n");
        let result = detect(&fixture.host());
        assert_eq!(result.status, DetectionStatus::Partial);
        assert_eq!(result.issues, ["Malformed PCI class value.", "Malformed GPU device ID."]);
        assert_eq!(result.value.devices[0].device_id, None);
    }

    #[test]
    fn card_without_backing_device_is_not_invented() {
        let fixture = Fixture::new();
        fixture
            .dir("/sys/devices/virtual/drm/card0")
            .link("/sys/class/drm/card0", "/sys/devices/virtual/drm/card0");
        let result = detect(&fixture.host());
        assert!(result.value.devices.is_empty());
        assert_eq!(result.issues, ["DRM card has no canonical backing device."]);
    }

    #[test]
    fn pci_addresses() {
        assert_eq!(pci_address("0000:01:00.0").as_deref(), Some("0000:01:00.0"));
        assert_eq!(pci_address("01:00.7").as_deref(), Some("01:00.7"));
        assert_eq!(pci_address("0000:01:00.8"), None);
        assert_eq!(pci_address("card0"), None);
    }
}
