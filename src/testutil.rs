//! Throwaway procfs/sysfs trees for detector tests.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

use crate::host::{FilesystemUsage, Host, Uname};

static NEXT: AtomicUsize = AtomicUsize::new(0);

pub struct Fixture {
    pub root: PathBuf,
}

impl Fixture {
    pub fn new() -> Self {
        let id = NEXT.fetch_add(1, Ordering::Relaxed);
        let root = std::env::temp_dir().join(format!("hv-test-{}-{id}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        Self { root }
    }

    pub fn path(&self, absolute: &str) -> PathBuf {
        self.root.join(absolute.trim_start_matches('/'))
    }

    pub fn write(&self, absolute: &str, content: &str) -> &Self {
        let path = self.path(absolute);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, content).unwrap();
        self
    }

    pub fn dir(&self, absolute: &str) -> &Self {
        fs::create_dir_all(self.path(absolute)).unwrap();
        self
    }

    /// Creates `absolute` as a symlink to the fixture path `target`.
    pub fn link(&self, absolute: &str, target: &str) -> &Self {
        let path = self.path(absolute);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::os::unix::fs::symlink(self.path(target), path).unwrap();
        self
    }

    /// A directory the test cannot read from, standing in for a permission error.
    pub fn unreadable(&self, absolute: &str) -> &Self {
        self.dir(absolute)
    }

    pub fn host(&self) -> Host {
        Host {
            root: self.root.clone(),
            uname: Err(io::Error::other("no uname in fixture")),
            online_processors: None,
            interface_addresses: Ok(Vec::new()),
            filesystem_usage: no_usage,
        }
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn no_usage(_: &Path) -> io::Result<FilesystemUsage> {
    Err(io::Error::from(io::ErrorKind::PermissionDenied))
}

pub fn uname(release: &str, nodename: &str, machine: &str) -> io::Result<Uname> {
    Ok(Uname { release: release.into(), nodename: nodename.into(), machine: machine.into() })
}
