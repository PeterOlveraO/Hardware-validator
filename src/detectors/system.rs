//! Linux distribution, kernel, system, baseboard, and firmware identity.

use std::collections::HashMap;

use crate::collector::{DetectionResult, Issues};
use crate::host::Host;
use crate::models::{BaseboardInfo, BiosInfo, SystemInfo};
use crate::text::{DMI_PLACEHOLDERS, clean, read_optional};

pub fn detect(host: &Host) -> DetectionResult<SystemInfo> {
    let mut issues = Issues::default();
    let mut info = SystemInfo::default();

    for path in ["/etc/os-release", "/usr/lib/os-release"] {
        match read_optional(&host.path(path)) {
            Ok(None) => continue,
            Err(_) => {
                issues.push("Unable to read os-release.");
                continue;
            }
            Ok(Some(text)) => {
                let (values, malformed) = parse_os_release(&text);
                info.distribution = first_clean(&values, &["NAME", "ID"]);
                info.distribution_version = first_clean(&values, &["VERSION_ID", "VERSION"]);
                if malformed {
                    issues.push("Malformed os-release data.");
                }
                break;
            }
        }
    }

    match &host.uname {
        Ok(uname) => {
            info.kernel_version = clean(&uname.release, DMI_PLACEHOLDERS);
            info.hostname = clean(&uname.nodename, DMI_PLACEHOLDERS);
            info.architecture = clean(&uname.machine, DMI_PLACEHOLDERS);
        }
        Err(_) => issues.push("Unable to query system identity."),
    }

    let mut dmi = |file: &str, label: &str| -> Option<String> {
        let path = if file.starts_with('/') {
            host.path(file)
        } else {
            host.path("/sys/class/dmi/id").join(file)
        };
        match read_optional(&path) {
            Ok(text) => text.and_then(|text| clean(&text, DMI_PLACEHOLDERS)),
            Err(_) => {
                issues.push(format!("Unable to read {label}."));
                None
            }
        }
    };
    info.manufacturer = dmi("sys_vendor", "system vendor");
    info.model = dmi("product_name", "system model")
        .or_else(|| dmi("/sys/firmware/devicetree/base/model", "Device Tree model"));
    info.baseboard = BaseboardInfo {
        manufacturer: dmi("board_vendor", "baseboard vendor"),
        model: dmi("board_name", "baseboard model"),
        version: dmi("board_version", "baseboard version"),
    };
    info.bios = BiosInfo {
        manufacturer: dmi("bios_vendor", "firmware vendor"),
        version: dmi("bios_version", "firmware version"),
        release_date: dmi("bios_date", "firmware release date"),
    };

    let useful = info != SystemInfo::default();
    DetectionResult::from_issues(info, issues, useful, "No system information was available.")
}

fn first_clean(values: &HashMap<String, String>, keys: &[&str]) -> Option<String> {
    keys.iter().find_map(|key| values.get(*key).and_then(|value| clean(value, DMI_PLACEHOLDERS)))
}

/// Parses `KEY=value` lines with shell quoting; returns whether any line was malformed.
fn parse_os_release(content: &str) -> (HashMap<String, String>, bool) {
    let mut values = HashMap::new();
    let mut malformed = false;
    for line in content.lines().map(str::trim) {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((key, raw_value)) = line.split_once('=') else {
            malformed = true;
            continue;
        };
        let valid_key = key.chars().all(|c| c.is_alphanumeric() || c == '_')
            && key.chars().any(char::is_alphanumeric);
        match shell_words(raw_value) {
            Some(words) if valid_key && words.len() <= 1 => {
                values.insert(key.to_owned(), words.into_iter().next().unwrap_or_default());
            }
            _ => malformed = true,
        }
    }
    (values, malformed)
}

/// POSIX shell word splitting (quotes and backslashes, no expansion).
/// Returns `None` for an unterminated quote or trailing backslash.
fn shell_words(input: &str) -> Option<Vec<String>> {
    let mut words = Vec::new();
    let mut word: Option<String> = None;
    let mut chars = input.chars();
    while let Some(c) = chars.next() {
        match c {
            c if c.is_whitespace() => words.extend(word.take()),
            '\'' => {
                let current = word.get_or_insert_with(String::new);
                loop {
                    match chars.next()? {
                        '\'' => break,
                        other => current.push(other),
                    }
                }
            }
            '"' => {
                let current = word.get_or_insert_with(String::new);
                loop {
                    match chars.next()? {
                        '"' => break,
                        '\\' => match chars.next()? {
                            escaped @ ('"' | '\\' | '$' | '`') => current.push(escaped),
                            '\n' => {}
                            other => {
                                current.push('\\');
                                current.push(other);
                            }
                        },
                        other => current.push(other),
                    }
                }
            }
            '\\' => word.get_or_insert_with(String::new).push(chars.next()?),
            other => word.get_or_insert_with(String::new).push(other),
        }
    }
    words.extend(word);
    Some(words)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::collector::DetectionStatus;
    use crate::testutil::{Fixture, uname};

    #[test]
    fn detects_distribution_system_baseboard_and_bios() {
        let fixture = Fixture::new();
        fixture
            .write(
                "/etc/os-release",
                "# comment\nNAME=\"Example Linux\"\nID=example\nVERSION_ID='24.04'\n",
            )
            .write("/sys/class/dmi/id/sys_vendor", "System Maker\n")
            .write("/sys/class/dmi/id/product_name", "Model X\n")
            .write("/sys/class/dmi/id/board_vendor", "Board Maker\n")
            .write("/sys/class/dmi/id/board_name", "To Be Filled By O.E.M.\n")
            .write("/sys/class/dmi/id/bios_version", "1.2.3\n");
        let mut host = fixture.host();
        host.uname = uname("6.8.0", "workstation", "x86_64");

        let result = detect(&host);

        assert_eq!(result.status, DetectionStatus::Complete);
        let info = result.value;
        assert_eq!(info.distribution.as_deref(), Some("Example Linux"));
        assert_eq!(info.distribution_version.as_deref(), Some("24.04"));
        assert_eq!(info.kernel_version.as_deref(), Some("6.8.0"));
        assert_eq!(info.hostname.as_deref(), Some("workstation"));
        assert_eq!(info.architecture.as_deref(), Some("x86_64"));
        assert_eq!(info.manufacturer.as_deref(), Some("System Maker"));
        assert_eq!(info.model.as_deref(), Some("Model X"));
        assert_eq!(info.baseboard.manufacturer.as_deref(), Some("Board Maker"));
        assert_eq!(info.baseboard.model, None);
        assert_eq!(info.bios.version.as_deref(), Some("1.2.3"));
        assert_eq!(info.bios.manufacturer, None);
    }

    #[test]
    fn uses_fallback_os_release_id_and_device_tree_model() {
        let fixture = Fixture::new();
        fixture
            .write("/usr/lib/os-release", "ID=arch\nVERSION=rolling\n")
            .write("/sys/firmware/devicetree/base/model", "Raspberry Pi 5\0");

        let info = detect(&fixture.host()).value;

        assert_eq!(info.distribution.as_deref(), Some("arch"));
        assert_eq!(info.distribution_version.as_deref(), Some("rolling"));
        assert_eq!(info.model.as_deref(), Some("Raspberry Pi 5"));
    }

    #[test]
    fn nothing_available_is_unavailable_and_read_errors_are_partial() {
        let empty = Fixture::new();
        let result = detect(&empty.host());
        assert_eq!(result.status, DetectionStatus::Unavailable);
        assert_eq!(result.issues, ["Unable to query system identity."]);

        let fixture = Fixture::new();
        fixture
            .write("/etc/os-release", "NAME=Linux\n")
            .unreadable("/sys/class/dmi/id/sys_vendor");
        let result = detect(&fixture.host());
        assert_eq!(result.status, DetectionStatus::Partial);
        assert!(result.issues.contains(&"Unable to read system vendor.".to_owned()));
        assert_eq!(result.value.distribution.as_deref(), Some("Linux"));
    }

    #[test]
    fn malformed_os_release_keeps_valid_fields() {
        let (values, malformed) =
            parse_os_release("NAME=\"Good\"\nbroken line\nVERSION=two words\nID='open\n");
        assert!(malformed);
        assert_eq!(values.get("NAME").map(String::as_str), Some("Good"));
        assert!(!values.contains_key("VERSION"));
        assert!(!values.contains_key("ID"));
    }

    #[test]
    fn shell_words_follow_posix_quoting() {
        assert_eq!(shell_words(r#""a \"b\" \$c""#), Some(vec![r#"a "b" $c"#.to_owned()]));
        assert_eq!(shell_words(r"'x\y'z"), Some(vec![r"x\yz".to_owned()]));
        assert_eq!(shell_words("a\\ b c"), Some(vec!["a b".to_owned(), "c".to_owned()]));
        assert_eq!(shell_words("\"\""), Some(vec![String::new()]));
        assert_eq!(shell_words(""), Some(vec![]));
        assert_eq!(shell_words("\"open"), None);
    }
}
