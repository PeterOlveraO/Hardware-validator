//! CPU identity, topology, frequencies, and caches. Measures nothing.

use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use super::numbered_entries;
use crate::collector::{DetectionResult, Issues};
use crate::host::Host;
use crate::models::{CacheKind, CpuCacheInfo, CpuInfo};
use crate::text::{UNKNOWN_PLACEHOLDERS, clean, read_optional};

const CPU_ROOT: &str = "/sys/devices/system/cpu";

pub fn detect(host: &Host) -> DetectionResult<CpuInfo> {
    let mut issues = Issues::default();
    let mut info = CpuInfo::default();

    let cpuinfo = match read_optional(&host.path("/proc/cpuinfo")) {
        Ok(text) => text,
        Err(_) => {
            issues.push("Unable to read CPU information.");
            None
        }
    };
    if let Some(text) = &cpuinfo {
        let parsed = parse_cpuinfo(text);
        info.model_names = parsed.model_names;
        info.vendor_ids = parsed.vendor_ids;
        info.capabilities = parsed.capabilities;
        if parsed.malformed {
            issues.push("Malformed CPU information.");
        }
    }

    match &host.uname {
        Ok(uname) => info.architecture = clean(&uname.machine, UNKNOWN_PLACEHOLDERS),
        Err(_) => issues.push("Unable to query CPU architecture."),
    }
    info.logical_processor_count = host.online_processors;
    info.physical_core_count = physical_core_count(host, cpuinfo.as_deref());
    let present = present_cpus(host, &mut issues);
    if let Some(present) = &present {
        info.physical_package_count = package_count(host, present, &mut issues);
    }

    match frequencies(host, cpuinfo.as_deref()) {
        Ok(entries) => {
            let current: Vec<u64> = entries.iter().filter_map(|entry| entry.current_hz).collect();
            if !current.is_empty() {
                let total: u128 = current.iter().map(|hz| u128::from(*hz)).sum();
                let count = current.len() as u128;
                info.current_frequency_hz = Some(((total + count / 2) / count) as u64);
            }
            info.maximum_frequency_hz = entries.iter().filter_map(|entry| entry.maximum_hz).max();
        }
        Err(FrequencyError::Unreadable) => issues.push("Unable to query CPU frequencies."),
        Err(FrequencyError::Malformed) => issues.push("Malformed CPU frequency data."),
    }

    info.caches = caches(host, present.as_deref(), &mut issues);

    let useful = info != CpuInfo::default();
    DetectionResult::from_issues(info, issues, useful, "No CPU information was available.")
}

#[derive(Debug, Default, PartialEq)]
struct ParsedCpuinfo {
    model_names: Vec<String>,
    vendor_ids: Vec<String>,
    capabilities: Vec<String>,
    malformed: bool,
}

/// Splits `/proc/cpuinfo` into blank-line separated blocks of lowercase keys.
fn cpuinfo_blocks(content: &str) -> (Vec<HashMap<String, String>>, bool) {
    let mut blocks = Vec::new();
    let mut block = HashMap::new();
    let mut malformed = false;
    for line in content.lines() {
        if line.trim().is_empty() {
            if !block.is_empty() {
                blocks.push(std::mem::take(&mut block));
            }
            continue;
        }
        match line.split_once(':') {
            Some((key, value)) if !key.trim().is_empty() => {
                block.insert(key.trim().to_lowercase(), value.trim().to_owned());
            }
            _ => malformed = true,
        }
    }
    if !block.is_empty() {
        blocks.push(block);
    }
    (blocks, malformed)
}

fn parse_cpuinfo(content: &str) -> ParsedCpuinfo {
    let (blocks, malformed) = cpuinfo_blocks(content);
    let mut parsed = ParsedCpuinfo { malformed, ..ParsedCpuinfo::default() };
    let cleaned = |block: &HashMap<String, String>, key: &str| {
        block.get(key).and_then(|value| clean(value, UNKNOWN_PLACEHOLDERS))
    };
    for block in &blocks {
        // ARM kernels name the SoC in a non-numeric `processor` field.
        let model = cleaned(block, "model name")
            .or_else(|| {
                cleaned(block, "processor")
                    .filter(|value| !value.chars().all(|c| c.is_ascii_digit()))
            })
            .or_else(|| cleaned(block, "cpu part"));
        let vendor = cleaned(block, "vendor_id").or_else(|| cleaned(block, "cpu implementer"));
        push_distinct(&mut parsed.model_names, model);
        push_distinct(&mut parsed.vendor_ids, vendor);
        let features =
            block.get("flags").filter(|flags| !flags.is_empty()).or_else(|| block.get("features"));
        for feature in features.into_iter().flat_map(|text| text.split_whitespace()) {
            push_distinct(&mut parsed.capabilities, Some(feature.to_owned()));
        }
    }
    parsed
}

fn push_distinct(values: &mut Vec<String>, candidate: Option<String>) {
    if let Some(candidate) = candidate.filter(|candidate| !values.contains(candidate)) {
        values.push(candidate);
    }
}

/// Distinct sibling lists from sysfs topology, else `physical id`/`cpu cores`
/// pairs from `/proc/cpuinfo`. Never falls back to the logical count.
fn physical_core_count(host: &Host, cpuinfo: Option<&str>) -> Option<u32> {
    for file in ["core_cpus_list", "thread_siblings_list"] {
        let lists: BTreeSet<String> = numbered_entries(&host.path(CPU_ROOT), "cpu")
            .unwrap_or_default()
            .iter()
            .filter_map(|(_, path)| fs::read_to_string(path.join("topology").join(file)).ok())
            .collect();
        if !lists.is_empty() {
            return u32::try_from(lists.len()).ok();
        }
    }
    let (blocks, _) = cpuinfo_blocks(cpuinfo?);
    let mut cores_by_package: HashMap<&str, u32> = HashMap::new();
    for block in &blocks {
        let cores = block.get("cpu cores").and_then(|cores| cores.parse().ok());
        if let (Some(package), Some(cores)) = (block.get("physical id"), cores) {
            cores_by_package.insert(package, cores);
        }
    }
    Some(cores_by_package.values().sum()).filter(|count: &u32| *count > 0)
}

fn parse_cpu_list(value: &str) -> Option<Vec<u32>> {
    let mut ids = BTreeSet::new();
    for section in value.trim().split(',') {
        match section.split_once('-') {
            Some((first, last)) => {
                let (first, last): (u32, u32) = (first.parse().ok()?, last.parse().ok()?);
                if last < first {
                    return None;
                }
                ids.extend(first..=last);
            }
            None => {
                ids.insert(section.parse().ok()?);
            }
        }
    }
    (!ids.is_empty()).then(|| ids.into_iter().collect())
}

fn present_cpus(host: &Host, issues: &mut Issues) -> Option<Vec<u32>> {
    let text = match read_optional(&host.path(CPU_ROOT).join("present")) {
        Ok(text) => text?,
        Err(_) => {
            issues.push("Unable to read present CPU topology.");
            return None;
        }
    };
    let ids = parse_cpu_list(&text);
    if ids.is_none() {
        issues.push("Malformed present CPU topology.");
    }
    ids
}

/// Counts packages only when every present CPU reports its package.
fn package_count(host: &Host, present: &[u32], issues: &mut Issues) -> Option<u32> {
    let mut packages = BTreeSet::new();
    let mut complete = true;
    for cpu in present {
        let path = host.path(CPU_ROOT).join(format!("cpu{cpu}/topology/physical_package_id"));
        match read_optional(&path) {
            Ok(Some(text)) => match text.trim().parse::<u32>() {
                Ok(package) => {
                    packages.insert(package);
                }
                Err(_) => {
                    issues.push("Malformed physical package topology.");
                    complete = false;
                }
            },
            Ok(None) => {
                issues.push("Incomplete physical package topology.");
                complete = false;
            }
            Err(_) => {
                issues.push("Unable to read physical package topology.");
                complete = false;
            }
        }
    }
    if complete && !packages.is_empty() { u32::try_from(packages.len()).ok() } else { None }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Frequency {
    pub current_hz: Option<u64>,
    pub maximum_hz: Option<u64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FrequencyError {
    Unreadable,
    Malformed,
}

/// One entry per cpufreq policy (or per CPU without cpufreq), like psutil.
/// The current value prefers `/proc/cpuinfo` MHz when it lines up with the
/// policies, because `scaling_cur_freq` can be stale on x86.
pub fn frequencies(host: &Host, cpuinfo: Option<&str>) -> Result<Vec<Frequency>, FrequencyError> {
    let cpuinfo_hz = cpuinfo.map(cpuinfo_mhz).transpose()?.unwrap_or_default();
    let root = host.path(CPU_ROOT);
    let policies = numbered_entries(&root.join("cpufreq"), "policy").unwrap_or_default();
    let policies = if policies.is_empty() {
        numbered_entries(&root, "cpu")
            .unwrap_or_default()
            .into_iter()
            .map(|(id, path)| (id, path.join("cpufreq")))
            .filter(|(_, path)| path.is_dir())
            .collect()
    } else {
        policies
    };
    if policies.is_empty() {
        return Ok(cpuinfo_hz
            .into_iter()
            .map(|hz| Frequency { current_hz: Some(hz), maximum_hz: None })
            .collect());
    }
    let mut entries = Vec::new();
    for (index, (_, policy)) in policies.iter().enumerate() {
        let current = if cpuinfo_hz.len() == policies.len() {
            Some(cpuinfo_hz[index])
        } else {
            match read_khz(&policy.join("scaling_cur_freq"))? {
                Some(hz) => Some(hz),
                None => read_khz(&policy.join("cpuinfo_cur_freq"))?,
            }
        };
        let maximum = read_khz(&policy.join("scaling_max_freq"))?;
        entries.push(Frequency { current_hz: current, maximum_hz: maximum });
    }
    Ok(entries)
}

fn cpuinfo_mhz(content: &str) -> Result<Vec<u64>, FrequencyError> {
    let mut values = Vec::new();
    for line in content.lines() {
        let Some((key, value)) = line.split_once(':') else { continue };
        if key.trim().eq_ignore_ascii_case("cpu mhz") {
            let mhz: f64 = value.trim().parse().map_err(|_| FrequencyError::Malformed)?;
            if !mhz.is_finite() || mhz < 0.0 {
                return Err(FrequencyError::Malformed);
            }
            if mhz > 0.0 {
                values.push((mhz * 1_000_000.0).round() as u64);
            }
        }
    }
    Ok(values)
}

/// Reads a sysfs kHz value as Hz; zero means unknown.
fn read_khz(path: &Path) -> Result<Option<u64>, FrequencyError> {
    match read_optional(path) {
        Ok(None) => Ok(None),
        Ok(Some(text)) => {
            let khz: u64 = text.trim().parse().map_err(|_| FrequencyError::Malformed)?;
            Ok((khz > 0).then(|| khz * 1000))
        }
        Err(_) => Err(FrequencyError::Unreadable),
    }
}

fn caches(host: &Host, present: Option<&[u32]>, issues: &mut Issues) -> Vec<CpuCacheInfo> {
    let root = host.path(CPU_ROOT);
    let cpu_directories: Vec<PathBuf> = match present {
        Some(ids) => ids.iter().map(|id| root.join(format!("cpu{id}"))).collect(),
        None => match numbered_entries(&root, "cpu") {
            Ok(entries) => entries.into_iter().map(|(_, path)| path).collect(),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Vec::new(),
            Err(_) => {
                issues.push("Unable to enumerate CPUs for cache detection.");
                Vec::new()
            }
        },
    };
    let mut caches = Vec::new();
    for directory in cpu_directories {
        let indexes = match numbered_entries(&directory.join("cache"), "index") {
            Ok(indexes) => indexes,
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(_) => {
                issues.push("Unable to enumerate CPU caches.");
                continue;
            }
        };
        for (_, index) in indexes.iter().filter(|(_, path)| path.is_dir()) {
            if let Some(cache) = read_cache(index, issues).filter(|cache| !caches.contains(cache)) {
                caches.push(cache);
            }
        }
    }
    caches.sort_by_key(|cache| {
        (cache.level, cache.kind, cache.size_bytes.is_none(), cache.size_bytes.unwrap_or(0))
    });
    caches
}

fn read_cache(index: &Path, issues: &mut Issues) -> Option<CpuCacheInfo> {
    let level = read_optional(&index.join("level"));
    let kind = read_optional(&index.join("type"));
    let (level, kind) = match (level, kind) {
        (Ok(Some(level)), Ok(Some(kind))) => (level, kind),
        (Err(_), _) | (_, Err(_)) => {
            issues.push("Unable to read CPU cache data.");
            return None;
        }
        _ => {
            issues.push("Incomplete CPU cache data.");
            return None;
        }
    };
    let kind = match kind.trim().to_lowercase().as_str() {
        "data" => CacheKind::Data,
        "instruction" => CacheKind::Instruction,
        "unified" => CacheKind::Unified,
        _ => {
            issues.push("Malformed CPU cache data.");
            return None;
        }
    };
    let Ok(level) = level.trim().parse() else {
        issues.push("Malformed CPU cache data.");
        return None;
    };
    let size_bytes = match read_optional(&index.join("size")) {
        Ok(None) => None,
        Ok(Some(text)) => {
            let Some(size) = parse_cache_size(&text) else {
                issues.push("Malformed CPU cache data.");
                return None;
            };
            Some(size)
        }
        Err(_) => {
            issues.push("Unable to read CPU cache size.");
            None
        }
    };
    Some(CpuCacheInfo { level, kind, size_bytes })
}

fn parse_cache_size(value: &str) -> Option<u64> {
    let value = value.trim();
    let split = value.find(|c: char| !c.is_ascii_digit()).unwrap_or(value.len());
    let (digits, unit) = value.split_at(split);
    let multiplier = match unit.to_ascii_uppercase().as_str() {
        "" => 1,
        "K" => 1024,
        "M" => 1024 * 1024,
        "G" => 1024 * 1024 * 1024,
        _ => return None,
    };
    digits.parse::<u64>().ok()?.checked_mul(multiplier)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::collector::DetectionStatus;
    use crate::testutil::{Fixture, uname};

    const X86_CPUINFO: &str = "processor\t: 0\nvendor_id\t: GenuineIntel\nmodel name\t: Example CPU @ 3.00GHz\ncpu MHz\t\t: 2500.000\nphysical id\t: 0\ncpu cores\t: 2\nflags\t\t: fpu sse sse2\n\nprocessor\t: 1\nvendor_id\t: GenuineIntel\nmodel name\t: Example CPU @ 3.00GHz\ncpu MHz\t\t: 3500.000\nphysical id\t: 0\ncpu cores\t: 2\nflags\t\t: fpu sse avx\n";

    fn x86_fixture() -> Fixture {
        let fixture = Fixture::new();
        fixture
            .write("/proc/cpuinfo", X86_CPUINFO)
            .write("/sys/devices/system/cpu/present", "0-1\n")
            .write("/sys/devices/system/cpu/cpu0/topology/physical_package_id", "0\n")
            .write("/sys/devices/system/cpu/cpu1/topology/physical_package_id", "0\n")
            .write("/sys/devices/system/cpu/cpu0/topology/core_cpus_list", "0\n")
            .write("/sys/devices/system/cpu/cpu1/topology/core_cpus_list", "1\n")
            .write("/sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq", "4000000\n")
            .write("/sys/devices/system/cpu/cpufreq/policy1/scaling_max_freq", "4200000\n")
            .write("/sys/devices/system/cpu/cpu0/cache/index0/level", "1\n")
            .write("/sys/devices/system/cpu/cpu0/cache/index0/type", "Data\n")
            .write("/sys/devices/system/cpu/cpu0/cache/index0/size", "48K\n")
            .write("/sys/devices/system/cpu/cpu0/cache/index3/level", "3\n")
            .write("/sys/devices/system/cpu/cpu0/cache/index3/type", "Unified\n")
            .write("/sys/devices/system/cpu/cpu0/cache/index3/size", "16384K\n")
            .write("/sys/devices/system/cpu/cpu1/cache/index0/level", "1\n")
            .write("/sys/devices/system/cpu/cpu1/cache/index0/type", "Data\n")
            .write("/sys/devices/system/cpu/cpu1/cache/index0/size", "48K\n");
        fixture
    }

    #[test]
    fn detects_x86_identity_counts_frequencies_and_deduplicated_caches() {
        let fixture = x86_fixture();
        let mut host = fixture.host();
        host.uname = uname("6.8.0", "host", "x86_64");
        host.online_processors = Some(2);

        let result = detect(&host);

        assert_eq!(result.status, DetectionStatus::Complete, "{:?}", result.issues);
        let cpu = result.value;
        assert_eq!(cpu.model_names, ["Example CPU @ 3.00GHz"]);
        assert_eq!(cpu.vendor_ids, ["GenuineIntel"]);
        assert_eq!(cpu.capabilities, ["fpu", "sse", "sse2", "avx"]);
        assert_eq!(cpu.architecture.as_deref(), Some("x86_64"));
        assert_eq!(cpu.logical_processor_count, Some(2));
        assert_eq!(cpu.physical_core_count, Some(2));
        assert_eq!(cpu.physical_package_count, Some(1));
        assert_eq!(cpu.current_frequency_hz, Some(3_000_000_000));
        assert_eq!(cpu.maximum_frequency_hz, Some(4_200_000_000));
        assert_eq!(
            cpu.caches,
            [
                CpuCacheInfo { level: 1, kind: CacheKind::Data, size_bytes: Some(48 * 1024) },
                CpuCacheInfo { level: 3, kind: CacheKind::Unified, size_bytes: Some(16 << 20) },
            ]
        );
    }

    #[test]
    fn detects_arm_implementer_part_and_features() {
        let parsed = parse_cpuinfo(
            "processor\t: 0\nBogoMIPS\t: 108.00\nFeatures\t: fp asimd\nCPU implementer\t: 0x41\nCPU part\t: 0xd0b\n",
        );
        assert_eq!(parsed.model_names, ["0xd0b"]);
        assert_eq!(parsed.vendor_ids, ["0x41"]);
        assert_eq!(parsed.capabilities, ["fp", "asimd"]);
        assert!(!parsed.malformed);
    }

    #[test]
    fn package_count_requires_complete_topology() {
        let fixture = x86_fixture();
        std::fs::remove_file(
            fixture.path("/sys/devices/system/cpu/cpu1/topology/physical_package_id"),
        )
        .unwrap();

        let result = detect(&fixture.host());

        assert_eq!(result.status, DetectionStatus::Partial);
        assert_eq!(result.value.physical_package_count, None);
        assert!(result.issues.contains(&"Incomplete physical package topology.".to_owned()));
    }

    #[test]
    fn physical_cores_fall_back_to_cpuinfo_and_never_to_logical_count() {
        let fixture = Fixture::new();
        fixture.write("/proc/cpuinfo", X86_CPUINFO);
        let mut host = fixture.host();
        host.online_processors = Some(16);
        assert_eq!(detect(&host).value.physical_core_count, Some(2));

        let bare = Fixture::new();
        bare.write("/proc/cpuinfo", "processor\t: 0\nmodel name\t: X\n");
        assert_eq!(detect(&bare.host()).value.physical_core_count, None);
    }

    #[test]
    fn malformed_cache_and_missing_sources() {
        let fixture = Fixture::new();
        fixture
            .write("/sys/devices/system/cpu/cpu0/cache/index0/level", "1\n")
            .write("/sys/devices/system/cpu/cpu0/cache/index0/type", "Data\n")
            .write("/sys/devices/system/cpu/cpu0/cache/index0/size", "12X\n");
        let result = detect(&fixture.host());
        assert!(result.value.caches.is_empty());
        assert!(result.issues.contains(&"Malformed CPU cache data.".to_owned()));

        let empty = Fixture::new();
        let result = detect(&empty.host());
        assert_eq!(result.status, DetectionStatus::Unavailable);
    }

    #[test]
    fn frequency_sources_and_units() {
        let fixture = Fixture::new();
        fixture
            .write("/sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq", "1200000\n")
            .write("/sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq", "3000000\n")
            .write("/sys/devices/system/cpu/cpufreq/policy4/cpuinfo_cur_freq", "1800000\n");
        assert_eq!(
            frequencies(&fixture.host(), None),
            Ok(vec![
                Frequency { current_hz: Some(1_200_000_000), maximum_hz: Some(3_000_000_000) },
                Frequency { current_hz: Some(1_800_000_000), maximum_hz: None },
            ])
        );
        fixture.write("/sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq", "fast\n");
        assert_eq!(frequencies(&fixture.host(), None), Err(FrequencyError::Malformed));
        assert_eq!(
            frequencies(&Fixture::new().host(), Some("cpu MHz : 1000.5\n")),
            Ok(vec![Frequency { current_hz: Some(1_000_500_000), maximum_hz: None }])
        );
    }

    #[test]
    fn cpu_lists_and_cache_sizes() {
        assert_eq!(parse_cpu_list("0-2,4\n"), Some(vec![0, 1, 2, 4]));
        assert_eq!(parse_cpu_list("3-1"), None);
        assert_eq!(parse_cpu_list(""), None);
        assert_eq!(parse_cache_size("32K"), Some(32 * 1024));
        assert_eq!(parse_cache_size("2m\n"), Some(2 << 20));
        assert_eq!(parse_cache_size("512"), Some(512));
        assert_eq!(parse_cache_size("K"), None);
    }
}
