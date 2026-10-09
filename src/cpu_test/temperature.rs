//! Read-only temperature sensors: hwmon first (the source psutil used), and
//! thermal zones only when hwmon has nothing, so one sensor is never
//! monitored twice under two names.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

use super::TemperatureReading;
use super::engine::TemperatureRound;
use crate::detectors::numbered_entries;
use crate::text::read_optional;

pub fn read(root: &Path) -> TemperatureRound {
    let mut warnings = Vec::new();
    let mut readings = hwmon(&root.join("sys/class/hwmon"), &mut warnings);
    if readings.is_empty() {
        readings = thermal_zones(&root.join("sys/class/thermal"), &mut warnings);
    }
    readings.sort_by(|a, b| a.identity.cmp(&b.identity));
    warnings.dedup();
    TemperatureRound { readings, warnings }
}

fn text(path: &Path) -> Option<String> {
    read_optional(path).ok().flatten().map(|text| text.trim().to_owned())
}

fn parse_millidegrees(text: &str) -> Option<f64> {
    text.trim().parse::<f64>().ok().map(|value| value / 1000.0).filter(|value| value.is_finite())
}

fn millidegrees(path: &Path) -> Option<f64> {
    parse_millidegrees(&text(path)?)
}

/// Label, current and critical temperature of one hwmon channel.
type Channel = (Option<String>, f64, Option<f64>);

/// `hwmonN/tempM_*` (and `hwmonN/device/tempM_*`) channels grouped by chip
/// name. Identities are `hwmon:<chip>:<label>:<occurrence>`.
fn hwmon(root: &Path, warnings: &mut Vec<String>) -> Vec<TemperatureReading> {
    let Ok(chips) = numbered_entries(root, "hwmon") else { return Vec::new() };
    let mut channels: BTreeSet<PathBuf> = BTreeSet::new();
    for (_, chip) in &chips {
        for directory in [chip.clone(), chip.join("device")] {
            let Ok(entries) = fs::read_dir(&directory) else { continue };
            for entry in entries.filter_map(Result::ok) {
                let name = entry.file_name().to_string_lossy().into_owned();
                let Some((base, _)) = name.split_once('_') else { continue };
                if base.starts_with("temp") {
                    channels.insert(directory.join(base));
                }
            }
        }
    }
    let mut by_chip: BTreeMap<String, Vec<Channel>> = BTreeMap::new();
    for channel in channels {
        let base = channel.to_string_lossy();
        let Some(current) = millidegrees(Path::new(&format!("{base}_input"))) else { continue };
        let directory = channel.parent().unwrap_or(&channel);
        let chip = text(&directory.join("name"))
            .or_else(|| directory.parent().and_then(|parent| text(&parent.join("name"))))
            .filter(|chip| !chip.is_empty());
        let Some(chip) = chip else {
            warnings.push("Malformed temperature sensor identity.".to_owned());
            continue;
        };
        let label = text(Path::new(&format!("{base}_label"))).filter(|label| !label.is_empty());
        let critical = millidegrees(Path::new(&format!("{base}_crit")));
        by_chip.entry(chip).or_default().push((label, current, critical));
    }
    let mut readings = Vec::new();
    for (chip, entries) in by_chip {
        let mut occurrences: BTreeMap<String, usize> = BTreeMap::new();
        for (label, current, critical) in entries {
            let label = label.unwrap_or_else(|| "unlabelled".to_owned());
            let occurrence = occurrences.entry(label.clone()).or_default();
            readings.push(TemperatureReading {
                identity: format!("hwmon:{chip}:{label}:{occurrence}"),
                current_celsius: current,
                critical_celsius: critical,
            });
            *occurrence += 1;
        }
    }
    readings
}

/// Thermal zones with the lowest `critical` trip point as their own limit.
fn thermal_zones(root: &Path, warnings: &mut Vec<String>) -> Vec<TemperatureReading> {
    let zones = match numbered_entries(root, "thermal_zone") {
        Ok(zones) => zones,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Vec::new(),
        Err(_) => {
            warnings.push("Unable to enumerate thermal zones.".to_owned());
            return Vec::new();
        }
    };
    let mut readings = Vec::new();
    for (_, zone) in zones {
        let current = match read_optional(&zone.join("temp")) {
            Ok(None) => continue,
            Ok(Some(text)) => parse_millidegrees(&text),
            Err(_) => None,
        };
        let Some(current) = current else {
            warnings.push("Invalid temperature source temp.".to_owned());
            continue;
        };
        let name = zone.file_name().unwrap_or_default().to_string_lossy().into_owned();
        let critical = numbered_trip_points(&zone)
            .into_iter()
            .filter(|(kind, _)| kind.eq_ignore_ascii_case("critical"))
            .filter_map(|(_, temperature)| temperature)
            .reduce(f64::min);
        readings.push(TemperatureReading {
            identity: format!("thermal:{name}"),
            current_celsius: current,
            critical_celsius: critical,
        });
    }
    readings
}

/// `(trip_point_N_type, trip_point_N_temp)` pairs of one zone.
fn numbered_trip_points(zone: &Path) -> Vec<(String, Option<f64>)> {
    let Ok(entries) = fs::read_dir(zone) else { return Vec::new() };
    entries
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let name = entry.file_name().to_string_lossy().into_owned();
            let number = name.strip_prefix("trip_point_")?.strip_suffix("_type")?.to_owned();
            let kind = text(&entry.path())?;
            Some((kind, millidegrees(&zone.join(format!("trip_point_{number}_temp")))))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::Fixture;

    #[test]
    fn hwmon_readings_keep_chip_label_and_occurrence() {
        let fixture = Fixture::new();
        fixture
            .write("/sys/class/hwmon/hwmon1/name", "coretemp\n")
            .write("/sys/class/hwmon/hwmon1/temp1_input", "45000\n")
            .write("/sys/class/hwmon/hwmon1/temp1_label", "Package id 0\n")
            .write("/sys/class/hwmon/hwmon1/temp1_crit", "100000\n")
            .write("/sys/class/hwmon/hwmon1/temp2_input", "41500\n")
            .write("/sys/class/hwmon/hwmon1/temp3_input", "bad\n")
            .write("/sys/class/hwmon/hwmon1/temp4_input", "40000\n")
            .write("/sys/class/hwmon/hwmon0/name", "nvme\n")
            .write("/sys/class/hwmon/hwmon0/device/temp1_input", "35000\n")
            .write("/sys/class/thermal/thermal_zone0/temp", "50000\n");

        let round = read(&fixture.root);

        let summary: Vec<(&str, f64, Option<f64>)> = round
            .readings
            .iter()
            .map(|r| (r.identity.as_str(), r.current_celsius, r.critical_celsius))
            .collect();
        assert_eq!(
            summary,
            [
                ("hwmon:coretemp:Package id 0:0", 45.0, Some(100.0)),
                ("hwmon:coretemp:unlabelled:0", 41.5, None),
                ("hwmon:coretemp:unlabelled:1", 40.0, None),
                ("hwmon:nvme:unlabelled:0", 35.0, None),
            ]
        );
        assert!(round.warnings.is_empty());
    }

    #[test]
    fn thermal_zones_are_only_a_fallback() {
        let fixture = Fixture::new();
        fixture
            .write("/sys/class/thermal/thermal_zone0/temp", "52000\n")
            .write("/sys/class/thermal/thermal_zone0/trip_point_0_type", "passive\n")
            .write("/sys/class/thermal/thermal_zone0/trip_point_0_temp", "80000\n")
            .write("/sys/class/thermal/thermal_zone0/trip_point_1_type", "critical\n")
            .write("/sys/class/thermal/thermal_zone0/trip_point_1_temp", "95000\n")
            .write("/sys/class/thermal/thermal_zone1/temp", "nan\n")
            .write("/sys/class/thermal/cooling_device0/type", "Fan\n");

        let round = read(&fixture.root);

        assert_eq!(
            round.readings,
            [TemperatureReading {
                identity: "thermal:thermal_zone0".into(),
                current_celsius: 52.0,
                critical_celsius: Some(95.0),
            }]
        );
        assert_eq!(round.warnings, ["Invalid temperature source temp."]);
        assert_eq!(read(&Fixture::new().root), TemperatureRound::default());
    }
}
