//! Shared text reading, cleaning, and terminal-safe rendering.

use std::fs;
use std::io;
use std::path::Path;

/// Reads a UTF-8 file. A missing file is a normal `Ok(None)`; any other
/// failure (permission, invalid UTF-8, sysfs read error) is an `Err`.
pub fn read_optional(path: &Path) -> io::Result<Option<String>> {
    match fs::read_to_string(path) {
        Ok(text) => Ok(Some(text)),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

/// Placeholder values firmware writes into DMI fields instead of real data.
pub const DMI_PLACEHOLDERS: &[&str] =
    &["unknown", "not specified", "default string", "to be filled by o.e.m."];

/// Generic placeholders that never describe real hardware.
pub const UNKNOWN_PLACEHOLDERS: &[&str] = &["unknown", "not specified"];

/// Trims NUL and whitespace, and drops empty values and known placeholders.
pub fn clean(value: &str, placeholders: &[&str]) -> Option<String> {
    let cleaned = value.trim_matches(|c: char| c == '\0' || c.is_whitespace());
    let folded = cleaned.to_lowercase();
    if cleaned.is_empty() || placeholders.contains(&folded.as_str()) {
        None
    } else {
        Some(cleaned.to_owned())
    }
}

/// Renders control and invisible format characters as escapes so detected
/// strings cannot drive the terminal.
pub fn plain_text(value: &str) -> String {
    let mut output = String::with_capacity(value.len());
    for character in value.chars() {
        let code_point = u32::from(character);
        if character.is_control() || is_format_character(code_point) {
            if code_point <= 0xff {
                output.push_str(&format!("\\x{code_point:02x}"));
            } else if code_point <= 0xffff {
                output.push_str(&format!("\\u{code_point:04x}"));
            } else {
                output.push_str(&format!("\\U{code_point:08x}"));
            }
        } else {
            output.push(character);
        }
    }
    output
}

/// Unicode general category Cf (format characters), as inclusive ranges.
const FORMAT_CHARACTERS: &[(u32, u32)] = &[
    (0x00ad, 0x00ad),
    (0x0600, 0x0605),
    (0x061c, 0x061c),
    (0x06dd, 0x06dd),
    (0x070f, 0x070f),
    (0x0890, 0x0891),
    (0x08e2, 0x08e2),
    (0x180e, 0x180e),
    (0x200b, 0x200f),
    (0x202a, 0x202e),
    (0x2060, 0x2064),
    (0x2066, 0x206f),
    (0xfeff, 0xfeff),
    (0xfff9, 0xfffb),
    (0x110bd, 0x110bd),
    (0x110cd, 0x110cd),
    (0x13430, 0x1343f),
    (0x1bca0, 0x1bca3),
    (0x1d173, 0x1d17a),
    (0xe0001, 0xe0001),
    (0xe0020, 0xe007f),
];

fn is_format_character(code_point: u32) -> bool {
    FORMAT_CHARACTERS.iter().any(|&(start, end)| (start..=end).contains(&code_point))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clean_drops_placeholders_and_padding() {
        assert_eq!(clean("  Vendor\0\n", UNKNOWN_PLACEHOLDERS), Some("Vendor".into()));
        assert_eq!(clean("Not Specified", UNKNOWN_PLACEHOLDERS), None);
        assert_eq!(clean("To Be Filled By O.E.M.", DMI_PLACEHOLDERS), None);
        assert_eq!(
            clean("To Be Filled By O.E.M.", UNKNOWN_PLACEHOLDERS),
            Some("To Be Filled By O.E.M.".into())
        );
        assert_eq!(clean(" \t", DMI_PLACEHOLDERS), None);
    }

    #[test]
    fn plain_text_escapes_terminal_controls() {
        assert_eq!(plain_text("a\x1b[31mb"), "a\\x1b[31mb");
        assert_eq!(plain_text("x\u{202e}y\u{feff}"), "x\\u202ey\\ufeff");
        assert_eq!(plain_text("\u{e0041}"), "\\U000e0041");
        assert_eq!(plain_text("Ñandú 64-bit"), "Ñandú 64-bit");
    }
}
