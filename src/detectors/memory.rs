//! RAM and swap capacities from `/proc/meminfo`, and EDAC module metadata.
//! Allocates and measures nothing.

use std::collections::HashMap;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};

use super::numbered_entries;
use crate::collector::{DetectionResult, Issues};
use crate::host::Host;
use crate::models::{MemoryInfo, MemoryModuleInfo, SwapInfo};
use crate::text::{UNKNOWN_PLACEHOLDERS, clean, read_optional};

const MIB: u64 = 1024 * 1024;

pub fn detect(host: &Host) -> DetectionResult<MemoryInfo> {
    let mut issues = Issues::default();
    let mut info = MemoryInfo::default();

    let meminfo = match read_optional(&host.path("/proc/meminfo")) {
        Ok(Some(text)) => Some(parse_meminfo(&text)),
        Ok(None) | Err(_) => {
            issues.push("Unable to query RAM information.");
            issues.push("Unable to query swap information.");
            None
        }
    };
    let mut useful = false;
    if let Some(values) = &meminfo {
        let ram = (values.get("MemTotal"), values.get("MemAvailable"), values.get("MemFree"));
        match ram {
            (Some(&total), Some(&available), Some(&free))
                if total > 0 && available <= total && free <= total =>
            {
                info.total_bytes = Some(total);
                info.available_bytes = Some(available);
                // The model defines used memory as total minus available.
                info.used_bytes = Some(total - available);
                info.free_bytes = Some(free);
                useful = true;
            }
            _ => issues.push("Malformed RAM information."),
        }
        match (values.get("SwapTotal"), values.get("SwapFree")) {
            (Some(&total), Some(&free)) if free <= total => {
                info.swap = SwapInfo {
                    total_bytes: Some(total),
                    available_bytes: Some(free),
                    used_bytes: Some(total - free),
                };
                useful = true;
            }
            _ => issues.push("Malformed swap information."),
        }
    }

    info.modules = modules(host, &mut issues);
    useful |= !info.modules.is_empty();
    DetectionResult::from_issues(info, issues, useful, "No memory information was available.")
}

/// `Key: value kB` lines in bytes; lines that do not parse are left out.
fn parse_meminfo(content: &str) -> HashMap<String, u64> {
    content
        .lines()
        .filter_map(|line| {
            let (key, value) = line.split_once(':')?;
            let mut fields = value.split_whitespace();
            let number: u64 = fields.next()?.parse().ok()?;
            let bytes = match fields.next() {
                None => number,
                Some("kB") => number.checked_mul(1024)?,
                Some(_) => return None,
            };
            Some((key.trim().to_owned(), bytes))
        })
        .collect()
}

fn modules(host: &Host, issues: &mut Issues) -> Vec<MemoryModuleInfo> {
    let mut modules = Vec::new();
    let root = host.path("/sys/devices/system/edac/mc");
    let controller_issue = "Unable to enumerate EDAC memory controllers.";
    let module_issue = "Unable to enumerate EDAC memory modules.";
    for controller in directories(&root, "mc", controller_issue, issues) {
        for dimm in directories(&controller, "dimm", module_issue, issues) {
            modules.push(read_module(&dimm, issues));
        }
    }
    modules
}

/// `<prefix><number>` subdirectories in numeric order; a missing root is normal.
fn directories(root: &Path, prefix: &str, issue: &str, issues: &mut Issues) -> Vec<PathBuf> {
    match numbered_entries(root, prefix) {
        Ok(entries) => {
            entries.into_iter().map(|(_, path)| path).filter(|path| path.is_dir()).collect()
        }
        Err(error) if error.kind() == ErrorKind::NotFound => Vec::new(),
        Err(_) => {
            issues.push(issue);
            Vec::new()
        }
    }
}

fn read_module(dimm: &Path, issues: &mut Issues) -> MemoryModuleInfo {
    let mut text = |file: &str| match read_optional(&dimm.join(file)) {
        Ok(value) => value.and_then(|value| clean(&value, UNKNOWN_PLACEHOLDERS)),
        Err(_) => {
            issues.push("Unable to read EDAC memory module data.");
            None
        }
    };
    let locator = text("dimm_location").or_else(|| text("dimm_label"));
    let memory_type = text("dimm_mem_type");
    let size_bytes = match read_optional(&dimm.join("size")) {
        Ok(None) => None,
        // The Linux EDAC ABI documents the size file in MiB.
        Ok(Some(size)) => match size.trim().parse::<u64>() {
            Ok(mib) if mib.to_string() == size.trim() => mib.checked_mul(MIB),
            _ => {
                issues.push("Malformed EDAC memory module size.");
                None
            }
        },
        Err(_) => {
            issues.push("Unable to read EDAC memory module data.");
            None
        }
    };
    MemoryModuleInfo { locator, size_bytes, memory_type }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::collector::DetectionStatus;
    use crate::testutil::Fixture;

    const MEMINFO: &str = "MemTotal:       16384 kB\nMemFree:         2048 kB\nMemAvailable:    6144 kB\nSwapTotal:       2048 kB\nSwapFree:        1024 kB\nHugePages_Total:       0\n";

    #[test]
    fn complete_ram_swap_and_edac_modules() {
        let fixture = Fixture::new();
        fixture
            .write("/proc/meminfo", MEMINFO)
            .write("/sys/devices/system/edac/mc/mc0/dimm1/dimm_label", "DIMM_B1\n")
            .write("/sys/devices/system/edac/mc/mc0/dimm1/size", "8192\n")
            .write("/sys/devices/system/edac/mc/mc0/dimm0/dimm_location", "channel 0 slot 0\n")
            .write("/sys/devices/system/edac/mc/mc0/dimm0/dimm_mem_type", "Unbuffered-DDR4\n")
            .write("/sys/devices/system/edac/mc/mc0/dimm0/size", "4096\n")
            .write("/sys/devices/system/edac/mc/mc0/rank0/size", "1\n");

        let result = detect(&fixture.host());

        assert_eq!(result.status, DetectionStatus::Complete, "{:?}", result.issues);
        let memory = result.value;
        assert_eq!(memory.total_bytes, Some(16384 * 1024));
        assert_eq!(memory.available_bytes, Some(6144 * 1024));
        assert_eq!(memory.used_bytes, Some(10240 * 1024));
        assert_eq!(memory.free_bytes, Some(2048 * 1024));
        assert_eq!(
            memory.swap,
            SwapInfo {
                total_bytes: Some(2048 * 1024),
                available_bytes: Some(1024 * 1024),
                used_bytes: Some(1024 * 1024),
            }
        );
        assert_eq!(
            memory.modules,
            [
                MemoryModuleInfo {
                    locator: Some("channel 0 slot 0".into()),
                    size_bytes: Some(4096 * MIB),
                    memory_type: Some("Unbuffered-DDR4".into()),
                },
                MemoryModuleInfo {
                    locator: Some("DIMM_B1".into()),
                    size_bytes: Some(8192 * MIB),
                    memory_type: None,
                },
            ]
        );
    }

    #[test]
    fn no_swap_is_complete_and_malformed_values_are_reported() {
        let fixture = Fixture::new();
        fixture.write(
            "/proc/meminfo",
            "MemTotal: 100 kB\nMemFree: 1 kB\nMemAvailable: 2 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n",
        );
        let result = detect(&fixture.host());
        assert_eq!(result.status, DetectionStatus::Complete);
        assert_eq!(result.value.swap.total_bytes, Some(0));

        fixture.write("/proc/meminfo", "MemTotal: 100 kB\nMemFree: 1 kB\nMemAvailable: 200 kB\n");
        let result = detect(&fixture.host());
        assert_eq!(result.status, DetectionStatus::Unavailable);
        assert_eq!(result.issues, ["Malformed RAM information.", "Malformed swap information."]);
        assert_eq!(result.value.total_bytes, None);
    }

    #[test]
    fn malformed_module_size_and_unreadable_fields_are_partial() {
        let fixture = Fixture::new();
        fixture
            .write("/proc/meminfo", MEMINFO)
            .write("/sys/devices/system/edac/mc/mc0/dimm0/size", "08\n")
            .unreadable("/sys/devices/system/edac/mc/mc0/dimm0/dimm_label");
        let result = detect(&fixture.host());
        assert_eq!(result.status, DetectionStatus::Partial);
        assert_eq!(
            result.issues,
            ["Unable to read EDAC memory module data.", "Malformed EDAC memory module size."]
        );
        assert_eq!(result.value.modules, [MemoryModuleInfo::default()]);
    }

    #[test]
    fn missing_meminfo_is_unavailable() {
        let result = detect(&Fixture::new().host());
        assert_eq!(result.status, DetectionStatus::Unavailable);
        assert_eq!(result.value, MemoryInfo::default());
    }
}
