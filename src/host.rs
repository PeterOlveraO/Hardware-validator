//! The local machine as seen by detectors: a filesystem root plus the few
//! values that only system calls provide. Tests replace every field.

use std::ffi::{CStr, CString};
use std::io;
use std::net::{Ipv4Addr, Ipv6Addr};
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Uname {
    pub release: String,
    pub nodename: String,
    pub machine: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FilesystemUsage {
    pub total_bytes: u64,
    pub used_bytes: u64,
    pub available_bytes: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AddressFamily {
    /// Hardware address, already formatted as `aa:bb:cc:dd:ee:ff`.
    Link(String),
    Ipv4 { address: Ipv4Addr, netmask: Option<Ipv4Addr> },
    Ipv6 { address: Ipv6Addr, netmask: Option<Ipv6Addr>, scope_id: u32 },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InterfaceAddress {
    pub interface: String,
    pub family: AddressFamily,
}

pub type UsageReader = fn(&Path) -> io::Result<FilesystemUsage>;

pub struct Host {
    /// `/` in production; a fixture directory in tests.
    pub root: PathBuf,
    pub uname: io::Result<Uname>,
    pub online_processors: Option<u32>,
    pub interface_addresses: io::Result<Vec<InterfaceAddress>>,
    pub filesystem_usage: UsageReader,
}

impl Host {
    pub fn local() -> Self {
        Self {
            root: PathBuf::from("/"),
            uname: uname(),
            online_processors: online_processors(),
            interface_addresses: interface_addresses(),
            filesystem_usage: statvfs_usage,
        }
    }

    /// Maps an absolute Linux path such as `/proc/cpuinfo` into the root.
    pub fn path(&self, absolute: &str) -> PathBuf {
        self.root.join(absolute.trim_start_matches('/'))
    }
}

fn c_field(field: &[libc::c_char]) -> String {
    // SAFETY: utsname fields are NUL-terminated within their fixed buffers.
    unsafe { CStr::from_ptr(field.as_ptr()) }.to_string_lossy().into_owned()
}

fn uname() -> io::Result<Uname> {
    // SAFETY: uname fills the zeroed struct and reports failure through -1.
    let mut value: libc::utsname = unsafe { std::mem::zeroed() };
    if unsafe { libc::uname(&mut value) } != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(Uname {
        release: c_field(&value.release),
        nodename: c_field(&value.nodename),
        machine: c_field(&value.machine),
    })
}

fn online_processors() -> Option<u32> {
    // SAFETY: sysconf has no memory-safety preconditions.
    let count = unsafe { libc::sysconf(libc::_SC_NPROCESSORS_ONLN) };
    u32::try_from(count).ok().filter(|count| *count > 0)
}

fn statvfs_usage(path: &Path) -> io::Result<FilesystemUsage> {
    let path = CString::new(path.as_os_str().as_bytes())?;
    // SAFETY: statvfs writes into the zeroed struct for a valid C string.
    let mut value: libc::statvfs = unsafe { std::mem::zeroed() };
    if unsafe { libc::statvfs(path.as_ptr(), &mut value) } != 0 {
        return Err(io::Error::last_os_error());
    }
    let block = value.f_frsize as u64;
    Ok(FilesystemUsage {
        total_bytes: value.f_blocks as u64 * block,
        used_bytes: (value.f_blocks as u64).saturating_sub(value.f_bfree as u64) * block,
        available_bytes: value.f_bavail as u64 * block,
    })
}

fn interface_addresses() -> io::Result<Vec<InterfaceAddress>> {
    let mut first: *mut libc::ifaddrs = std::ptr::null_mut();
    // SAFETY: getifaddrs allocates a list released below with freeifaddrs.
    if unsafe { libc::getifaddrs(&mut first) } != 0 {
        return Err(io::Error::last_os_error());
    }
    let mut addresses = Vec::new();
    let mut current = first;
    while !current.is_null() {
        // SAFETY: every node in the list stays valid until freeifaddrs.
        let entry = unsafe { &*current };
        current = entry.ifa_next;
        if entry.ifa_addr.is_null() || entry.ifa_name.is_null() {
            continue;
        }
        // SAFETY: ifa_name is a NUL-terminated interface name.
        let interface = unsafe { CStr::from_ptr(entry.ifa_name) }.to_string_lossy().into_owned();
        // SAFETY: the family tag tells which sockaddr layout ifa_addr points to.
        let family = unsafe { address_family(entry) };
        if let Some(family) = family {
            addresses.push(InterfaceAddress { interface, family });
        }
    }
    // SAFETY: first came from a successful getifaddrs call.
    unsafe { libc::freeifaddrs(first) };
    Ok(addresses)
}

/// # Safety
/// `entry.ifa_addr` must be non-null and valid for its declared family.
unsafe fn address_family(entry: &libc::ifaddrs) -> Option<AddressFamily> {
    unsafe {
        match i32::from((*entry.ifa_addr).sa_family) {
            libc::AF_INET => {
                let address = &*(entry.ifa_addr as *const libc::sockaddr_in);
                let netmask = (!entry.ifa_netmask.is_null()).then(|| {
                    let mask = &*(entry.ifa_netmask as *const libc::sockaddr_in);
                    Ipv4Addr::from(u32::from_be(mask.sin_addr.s_addr))
                });
                Some(AddressFamily::Ipv4 {
                    address: Ipv4Addr::from(u32::from_be(address.sin_addr.s_addr)),
                    netmask,
                })
            }
            libc::AF_INET6 => {
                let address = &*(entry.ifa_addr as *const libc::sockaddr_in6);
                let netmask = (!entry.ifa_netmask.is_null()).then(|| {
                    let mask = &*(entry.ifa_netmask as *const libc::sockaddr_in6);
                    Ipv6Addr::from(mask.sin6_addr.s6_addr)
                });
                Some(AddressFamily::Ipv6 {
                    address: Ipv6Addr::from(address.sin6_addr.s6_addr),
                    netmask,
                    scope_id: address.sin6_scope_id,
                })
            }
            libc::AF_PACKET => {
                let link = &*(entry.ifa_addr as *const libc::sockaddr_ll);
                (link.sll_halen == 6).then(|| {
                    let mac: Vec<String> =
                        link.sll_addr[..6].iter().map(|byte| format!("{byte:02x}")).collect();
                    AddressFamily::Link(mac.join(":"))
                })
            }
            _ => None,
        }
    }
}
