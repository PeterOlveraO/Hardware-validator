//! One detector per hardware component. Each reads only local files and
//! system calls through [`Host`](crate::host::Host) and never fails: problems
//! become issues on a partial or unavailable result.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

pub mod cpu;
pub mod memory;
pub mod system;

/// Lists `<prefix><number>` directory entries sorted by number.
pub(crate) fn numbered_entries(root: &Path, prefix: &str) -> io::Result<Vec<(u32, PathBuf)>> {
    let mut entries: Vec<(u32, PathBuf)> = fs::read_dir(root)?
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let name = entry.file_name().into_string().ok()?;
            let digits = name.strip_prefix(prefix)?;
            if digits.is_empty() || !digits.chars().all(|c| c.is_ascii_digit()) {
                return None;
            }
            Some((digits.parse().ok()?, entry.path()))
        })
        .collect();
    entries.sort();
    Ok(entries)
}

