//! Block devices, partitions, and mounted filesystems from sysfs and
//! `/proc/self/mounts`. Never opens a block device.

use std::collections::{BTreeSet, HashSet};
use std::fs;
use std::io;
use std::path::{Component, Path, PathBuf};

use crate::collector::{DetectionResult, Issues};
use crate::host::Host;
use crate::models::{BlockDeviceKind, DiskInfo, MountInfo, PartitionInfo, StorageInfo};
use crate::text::{UNKNOWN_PLACEHOLDERS, clean, read_optional};

pub fn detect(host: &Host) -> DetectionResult<StorageInfo> {
    let mut issues = Issues::default();
    let mounts = mounts(host, &mut issues);
    let entries = block_entries(host, &mut issues);

    let identities: HashSet<&Path> = entries.iter().map(|entry| entry.identity.as_path()).collect();
    let is_partition = |entry: &&BlockEntry| {
        entry.has_partition_attribute
            || entry.identity.parent().is_some_and(|parent| identities.contains(parent))
    };
    let (partitions, disks): (Vec<&BlockEntry>, Vec<&BlockEntry>) =
        entries.iter().partition(is_partition);
    let disk_identities: Vec<&PathBuf> = disks.iter().map(|disk| &disk.identity).collect();

    let mut partitions_by_disk: Vec<Vec<&BlockEntry>> = vec![Vec::new(); disks.len()];
    for partition in partitions {
        let parent = partition.identity.ancestors().skip(1).find_map(|ancestor| {
            disk_identities.iter().position(|disk| disk.as_path() == ancestor)
        });
        match parent {
            Some(index) => partitions_by_disk[index].push(partition),
            None => issues.push(format!("Unable to identify parent of {}.", partition.name)),
        }
    }

    let disks: Vec<DiskInfo> = disks
        .iter()
        .zip(&partitions_by_disk)
        .map(|(disk, partitions)| read_disk(host, disk, partitions, &mounts, &mut issues))
        .collect();
    let value = StorageInfo { disks, mounts: mounts.unwrap_or_default() };
    if issues.is_empty() && value.disks.is_empty() && value.mounts.is_empty() {
        // Readable but empty sources are a known empty inventory.
        return DetectionResult::complete(value);
    }
    let useful = !value.disks.is_empty() || !value.mounts.is_empty();
    DetectionResult::from_issues(value, issues, useful, "No storage information was available.")
}

struct BlockEntry {
    name: String,
    path: PathBuf,
    identity: PathBuf,
    has_partition_attribute: bool,
}

fn block_entries(host: &Host, issues: &mut Issues) -> Vec<BlockEntry> {
    let read = match fs::read_dir(host.path("/sys/class/block")) {
        Ok(read) => read,
        Err(error) => {
            issues.push(if error.kind() == io::ErrorKind::NotFound {
                "Block device sysfs is unavailable."
            } else {
                "Unable to enumerate block devices."
            });
            return Vec::new();
        }
    };
    let mut paths: Vec<PathBuf> = read.filter_map(Result::ok).map(|entry| entry.path()).collect();
    paths.sort();
    let mut entries = Vec::new();
    for path in paths.into_iter().filter(|path| path.is_dir()) {
        let name = path.file_name().unwrap_or_default().to_string_lossy().into_owned();
        let identity = fs::canonicalize(&path).unwrap_or_else(|_| {
            issues.push(format!("Unable to resolve block device {name}."));
            path.clone()
        });
        let has_partition_attribute = match read_optional(&path.join("partition")) {
            Ok(None) => false,
            Ok(Some(number)) => {
                if !number.trim().parse::<u32>().is_ok_and(|number| number > 0) {
                    issues.push(format!("Malformed partition identity for {name}."));
                }
                true
            }
            Err(_) => {
                issues.push(format!("Unable to read partition identity for {name}."));
                false
            }
        };
        entries.push(BlockEntry { name, path, identity, has_partition_attribute });
    }
    entries
}

/// Reads sysfs attributes of one device, recording issues under its name.
struct Attributes<'a> {
    path: &'a Path,
    name: &'a str,
    issues: &'a mut Issues,
}

impl Attributes<'_> {
    fn raw(&mut self, file: &str) -> Option<String> {
        read_optional(&self.path.join(file)).unwrap_or_else(|_| {
            self.issues.push(format!("Unable to read storage attribute for {}.", self.name));
            None
        })
    }

    fn string(&mut self, file: &str) -> Option<String> {
        self.raw(file).and_then(|value| clean(&value, UNKNOWN_PLACEHOLDERS))
    }

    fn integer(&mut self, file: &str) -> Option<u64> {
        let value = self.raw(file)?;
        let parsed = value.trim().parse().ok();
        if parsed.is_none() {
            self.malformed();
        }
        parsed
    }

    fn positive(&mut self, file: &str) -> Option<u64> {
        let value = self.integer(file)?;
        if value == 0 {
            self.malformed();
            return None;
        }
        Some(value)
    }

    fn flag(&mut self, file: &str) -> Option<bool> {
        match self.integer(file)? {
            0 => Some(false),
            1 => Some(true),
            _ => {
                self.malformed();
                None
            }
        }
    }

    /// sysfs `size` files count 512-byte sectors regardless of the device.
    fn sectors(&mut self, file: &str) -> Option<u64> {
        self.integer(file)?.checked_mul(512)
    }

    /// Name of the canonical target of a sysfs symlink such as `device/subsystem`.
    fn link_name(&mut self, file: &str) -> Option<String> {
        match fs::canonicalize(self.path.join(file)) {
            Ok(target) => clean(&target.file_name()?.to_string_lossy(), UNKNOWN_PLACEHOLDERS),
            Err(error) if error.kind() == io::ErrorKind::NotFound => None,
            Err(_) => {
                self.issues.push(format!("Unable to inspect storage subsystem for {}.", self.name));
                None
            }
        }
    }

    fn malformed(&mut self) {
        self.issues.push(format!("Malformed storage attribute for {}.", self.name));
    }
}

fn read_disk(
    host: &Host,
    entry: &BlockEntry,
    partitions: &[&BlockEntry],
    mounts: &Option<Vec<MountInfo>>,
    issues: &mut Issues,
) -> DiskInfo {
    let mut attributes = Attributes { path: &entry.path, name: &entry.name, issues };
    let rotational = attributes.flag("queue/rotational");
    let removable = attributes.flag("removable");
    let subsystem = attributes.link_name("device/subsystem").map(|name| name.to_lowercase());
    let device_type = attributes.string("device/type");
    let transport = transport(&mut attributes, &entry.identity, subsystem.as_deref());
    let kind = classify(
        &entry.identity,
        rotational,
        removable,
        subsystem.as_deref(),
        device_type.as_deref(),
        transport.as_deref(),
    );
    let mut disk = DiskInfo {
        name: entry.name.clone(),
        device_path: device_path(host, &entry.name),
        vendor: attributes.string("device/vendor"),
        model: attributes.string("device/model"),
        serial_number: attributes.string("device/serial").or_else(|| attributes.string("serial")),
        size_bytes: attributes.sectors("size"),
        rotational,
        transport,
        removable,
        kind,
        read_only: attributes.flag("ro"),
        logical_sector_size: attributes.positive("queue/logical_block_size"),
        physical_sector_size: attributes.positive("queue/physical_block_size"),
        partitions: Vec::new(),
    };
    for partition in partitions {
        let mut attributes = Attributes {
            path: &partition.path,
            name: &partition.name,
            issues: &mut *attributes.issues,
        };
        let device_path = device_path(host, &partition.name);
        let matching: Vec<&MountInfo> = mounts
            .iter()
            .flatten()
            .filter(|mount| device_path.is_some() && mount.source == device_path)
            .collect();
        let mount_points: BTreeSet<String> =
            matching.iter().map(|mount| mount.mount_point.clone()).collect();
        disk.partitions.push(PartitionInfo {
            name: partition.name.clone(),
            parent_name: entry.name.clone(),
            size_bytes: attributes.sectors("size"),
            read_only: attributes.flag("ro"),
            filesystem: matching.iter().find_map(|mount| mount.filesystem.clone()),
            mount_points: mount_points.into_iter().collect(),
            device_path,
        });
    }
    disk.partitions.sort_by(|a, b| a.name.cmp(&b.name));
    disk
}

fn device_path(host: &Host, name: &str) -> Option<String> {
    let path = format!("/dev/{name}");
    host.path(&path).exists().then_some(path)
}

fn transport(
    attributes: &mut Attributes,
    identity: &Path,
    subsystem: Option<&str>,
) -> Option<String> {
    if let Some(bus @ ("nvme" | "mmc" | "usb" | "virtio")) = subsystem {
        return Some(bus.to_owned());
    }
    let evidence = attributes.string("device/transport");
    let evidence = evidence.or_else(|| attributes.string("device/protocol"));
    if let Some(evidence) = evidence {
        let evidence = evidence.to_lowercase();
        return Some(match evidence.as_str() {
            "ata" | "serial ata" | "sata" => "sata".to_owned(),
            _ => evidence,
        });
    }
    let mut buses = HashSet::new();
    for ancestor in identity.ancestors() {
        match fs::canonicalize(ancestor.join("subsystem")) {
            Ok(target) => {
                let bus = target.file_name().unwrap_or_default().to_string_lossy().to_lowercase();
                buses.insert(bus);
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(_) => {
                let name = attributes.name;
                let issue = format!("Unable to inspect storage bus ancestry for {name}.");
                attributes.issues.push(issue);
            }
        }
    }
    ["nvme", "mmc", "usb", "virtio", "ata", "scsi"]
        .into_iter()
        .find(|bus| buses.contains(*bus))
        .map(|bus| if bus == "ata" { "sata".to_owned() } else { bus.to_owned() })
}

/// Conservative classification: unknown unless the evidence is explicit.
fn classify(
    identity: &Path,
    rotational: Option<bool>,
    removable: Option<bool>,
    subsystem: Option<&str>,
    device_type: Option<&str>,
    transport: Option<&str>,
) -> Option<BlockDeviceKind> {
    let components: Vec<Component> = identity.components().collect();
    let virtual_device = components
        .windows(2)
        .any(|pair| pair[0].as_os_str() == "devices" && pair[1].as_os_str() == "virtual");
    if virtual_device || transport == Some("virtio") {
        return Some(BlockDeviceKind::Virtual);
    }
    if subsystem == Some("nvme") {
        return Some(BlockDeviceKind::Nvme);
    }
    if subsystem == Some("mmc") && device_type.is_some_and(|kind| kind.eq_ignore_ascii_case("mmc"))
    {
        return Some(BlockDeviceKind::Emmc);
    }
    if removable == Some(true) {
        return Some(BlockDeviceKind::Removable);
    }
    if !matches!(transport, Some("sata" | "sas" | "scsi")) {
        return None;
    }
    if device_type.is_some_and(|kind| kind != "0" && !kind.eq_ignore_ascii_case("disk")) {
        return None;
    }
    rotational
        .map(|rotational| if rotational { BlockDeviceKind::Hdd } else { BlockDeviceKind::Ssd })
}

/// Physical mounted filesystems, like `psutil.disk_partitions(all=False)`.
/// `None` when the mount table itself is unavailable.
fn mounts(host: &Host, issues: &mut Issues) -> Option<Vec<MountInfo>> {
    let tables = (
        read_optional(&host.path("/proc/filesystems")),
        read_optional(&host.path("/proc/self/mounts")),
    );
    let (Ok(Some(filesystems)), Ok(Some(table))) = tables else {
        issues.push("Unable to enumerate mounted filesystems.");
        return None;
    };
    let mut physical: HashSet<&str> = filesystems
        .lines()
        .filter(|line| !line.starts_with("nodev"))
        .map(str::trim)
        .filter(|name| !name.is_empty())
        .collect();
    physical.insert("zfs");

    let mut mounts = Vec::new();
    for line in table.lines().filter(|line| !line.trim().is_empty()) {
        let fields: Vec<String> = line.split_whitespace().take(3).map(unescape_octal).collect();
        let [source, mount_point, filesystem] = fields.as_slice() else {
            issues.push("Malformed mounted filesystem data.");
            continue;
        };
        if source == "none" || !physical.contains(filesystem.as_str()) {
            continue;
        }
        let source = clean(source, UNKNOWN_PLACEHOLDERS);
        let filesystem = clean(filesystem, UNKNOWN_PLACEHOLDERS);
        let mut mount = MountInfo {
            mount_point: mount_point.clone(),
            source: source.clone(),
            filesystem: filesystem.clone(),
            ..MountInfo::default()
        };
        let folded = filesystem.unwrap_or_default().to_lowercase();
        let remote_or_deferred = folded == "autofs"
            || ["fuse.", "nfs", "cifs", "smb"].iter().any(|prefix| folded.starts_with(prefix))
            || source.is_some_and(|source| source.starts_with("//") || source.contains(':'));
        if !remote_or_deferred {
            match (host.filesystem_usage)(Path::new(mount_point)) {
                Ok(usage) => {
                    mount.total_bytes = Some(usage.total_bytes);
                    mount.used_bytes = Some(usage.used_bytes);
                    mount.available_bytes = Some(usage.available_bytes);
                }
                Err(_) => {
                    issues.push(format!("Unable to read filesystem usage for {mount_point}."))
                }
            }
        }
        mounts.push(mount);
    }
    mounts.sort_by(|a, b| {
        (&a.mount_point, &a.source, &a.filesystem).cmp(&(&b.mount_point, &b.source, &b.filesystem))
    });
    Some(mounts)
}

/// Decodes the `\040`-style escapes the kernel uses in mount tables.
fn unescape_octal(field: &str) -> String {
    let bytes = field.as_bytes();
    let mut output = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        let escaped = bytes
            .get(index + 1..index + 4)
            .filter(|_| bytes[index] == b'\\')
            .and_then(|digits| u8::from_str_radix(std::str::from_utf8(digits).ok()?, 8).ok());
        match escaped {
            Some(byte) => {
                output.push(byte);
                index += 4;
            }
            None => {
                output.push(bytes[index]);
                index += 1;
            }
        }
    }
    String::from_utf8_lossy(&output).into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::collector::DetectionStatus;
    use crate::host::FilesystemUsage;
    use crate::testutil::Fixture;

    fn usage(_: &Path) -> io::Result<FilesystemUsage> {
        Ok(FilesystemUsage { total_bytes: 100, used_bytes: 40, available_bytes: 50 })
    }

    /// `/sys/class/block/<name>` linking into a `/sys/devices/...` tree.
    fn block(fixture: &Fixture, name: &str, device: &str) {
        fixture.dir(device).link(&format!("/sys/class/block/{name}"), device);
    }

    fn sata_fixture() -> Fixture {
        let fixture = Fixture::new();
        let hdd = "/sys/devices/pci0000:00/0000:00:17.0/ata1/host0/target0:0:0/0:0:0:0/block/sda";
        let ssd = "/sys/devices/pci0000:00/0000:00:17.0/ata2/host1/target1:0:0/1:0:0:0/block/sdb";
        block(&fixture, "sda", hdd);
        block(&fixture, "sdb", ssd);
        block(&fixture, "sda1", &format!("{hdd}/sda1"));
        block(&fixture, "sda2", &format!("{hdd}/sda2"));
        fixture
            .dir("/sys/bus/scsi")
            .link(&format!("{hdd}/device/subsystem"), "/sys/bus/scsi")
            .link(&format!("{ssd}/device/subsystem"), "/sys/bus/scsi")
            .dir("/sys/bus/ata")
            .link("/sys/devices/pci0000:00/0000:00:17.0/ata1/subsystem", "/sys/bus/ata")
            .write(&format!("{hdd}/queue/rotational"), "1\n")
            .write(&format!("{hdd}/removable"), "0\n")
            .write(&format!("{hdd}/ro"), "0\n")
            .write(&format!("{hdd}/size"), "1953525168\n")
            .write(&format!("{hdd}/device/vendor"), "ATA     \n")
            .write(&format!("{hdd}/device/model"), "Disk Model\n")
            .write(&format!("{hdd}/device/type"), "0\n")
            .write(&format!("{hdd}/queue/logical_block_size"), "512\n")
            .write(&format!("{hdd}/queue/physical_block_size"), "4096\n")
            .write(&format!("{hdd}/sda1/partition"), "1\n")
            .write(&format!("{hdd}/sda1/size"), "2048\n")
            .write(&format!("{hdd}/sda2/partition"), "2\n")
            .write(&format!("{ssd}/queue/rotational"), "0\n")
            .write(&format!("{ssd}/device/transport"), "Serial ATA\n")
            .write("/dev/sda1", "")
            .write("/proc/filesystems", "nodev\tsysfs\nnodev\ttmpfs\n\text4\n\tvfat\n")
            .write(
                "/proc/self/mounts",
                "sysfs /sys sysfs rw 0 0\n/dev/sda1 /mnt/My\\040Disk ext4 rw 0 0\n/dev/sda1 / ext4 rw 0 0\nserver:/x /net nfs4 rw 0 0\n",
            );
        fixture
    }

    #[test]
    fn detects_sata_hdd_ssd_partitions_and_mounts() {
        let fixture = sata_fixture();
        fixture.write("/proc/filesystems", "nodev\tsysfs\n\text4\n\tnfs4\n");
        let mut host = fixture.host();
        host.filesystem_usage = usage;

        let result = detect(&host);

        assert_eq!(result.status, DetectionStatus::Complete, "{:?}", result.issues);
        let storage = result.value;
        let [hdd, ssd] = storage.disks.as_slice() else { panic!("{:?}", storage.disks) };
        assert_eq!(hdd.name, "sda");
        assert_eq!(hdd.kind, Some(BlockDeviceKind::Hdd));
        assert_eq!(hdd.transport.as_deref(), Some("sata"));
        assert_eq!(hdd.vendor.as_deref(), Some("ATA"));
        assert_eq!(hdd.size_bytes, Some(1953525168 * 512));
        assert_eq!(hdd.physical_sector_size, Some(4096));
        assert_eq!(hdd.device_path, None);
        assert_eq!(ssd.kind, Some(BlockDeviceKind::Ssd));
        assert_eq!(ssd.transport.as_deref(), Some("sata"));

        let [first, second] = hdd.partitions.as_slice() else { panic!("{:?}", hdd.partitions) };
        assert_eq!(first.parent_name, "sda");
        assert_eq!(first.device_path.as_deref(), Some("/dev/sda1"));
        assert_eq!(first.size_bytes, Some(2048 * 512));
        assert_eq!(first.filesystem.as_deref(), Some("ext4"));
        assert_eq!(first.mount_points, ["/", "/mnt/My Disk"]);
        assert!(second.mount_points.is_empty());

        let points: Vec<&str> = storage.mounts.iter().map(|m| m.mount_point.as_str()).collect();
        assert_eq!(points, ["/", "/mnt/My Disk", "/net"]);
        assert_eq!(storage.mounts[0].total_bytes, Some(100));
        assert_eq!(storage.mounts[2].total_bytes, None, "remote usage is never queried");
    }

    #[test]
    fn classifies_nvme_emmc_virtual_removable_and_unknown() {
        let devices = Path::new("/sys/devices/pci0000:00/x");
        let virtual_path = Path::new("/sys/devices/virtual/block/loop0");
        assert_eq!(
            classify(devices, Some(false), Some(false), Some("nvme"), None, Some("nvme")),
            Some(BlockDeviceKind::Nvme)
        );
        assert_eq!(
            classify(devices, Some(false), Some(false), Some("mmc"), Some("MMC"), Some("mmc")),
            Some(BlockDeviceKind::Emmc)
        );
        assert_eq!(
            classify(devices, None, None, Some("mmc"), Some("SD"), Some("mmc")),
            None,
            "an SD card is not guessed as eMMC"
        );
        assert_eq!(
            classify(virtual_path, Some(false), Some(false), None, None, None),
            Some(BlockDeviceKind::Virtual)
        );
        assert_eq!(
            classify(devices, Some(true), Some(false), None, None, Some("virtio")),
            Some(BlockDeviceKind::Virtual)
        );
        assert_eq!(
            classify(devices, Some(false), Some(true), None, None, Some("usb")),
            Some(BlockDeviceKind::Removable)
        );
        assert_eq!(classify(devices, Some(true), Some(false), None, None, None), None);
        assert_eq!(classify(devices, Some(true), None, None, Some("5"), Some("scsi")), None);
    }

    #[test]
    fn unreadable_attributes_and_mount_usage_are_partial() {
        let fixture = sata_fixture();
        fixture.unreadable(
            "/sys/devices/pci0000:00/0000:00:17.0/ata2/host1/target1:0:0/1:0:0:0/block/sdb/ro",
        );
        let result = detect(&fixture.host());
        assert_eq!(result.status, DetectionStatus::Partial);
        assert_eq!(
            result.issues,
            [
                "Unable to read filesystem usage for /.",
                "Unable to read filesystem usage for /mnt/My Disk.",
                "Unable to read storage attribute for sdb.",
            ]
        );
        assert_eq!(result.value.disks.len(), 2);
    }

    #[test]
    fn malformed_attribute_is_partial_and_empty_sources_are_complete() {
        let fixture = Fixture::new();
        block(&fixture, "vda", "/sys/devices/virtual/block/vda");
        fixture.write("/sys/devices/virtual/block/vda/removable", "2\n");
        let result = detect(&fixture.host());
        assert_eq!(result.status, DetectionStatus::Partial);
        assert!(result.issues.contains(&"Malformed storage attribute for vda.".to_owned()));
        assert_eq!(result.value.disks[0].removable, None);
        assert_eq!(result.value.disks[0].kind, Some(BlockDeviceKind::Virtual));

        let empty = Fixture::new();
        empty.dir("/sys/class/block").write("/proc/filesystems", "").write("/proc/self/mounts", "");
        assert_eq!(detect(&empty.host()).status, DetectionStatus::Complete);
        assert_eq!(detect(&Fixture::new().host()).status, DetectionStatus::Unavailable);
    }

    #[test]
    fn mount_fields_are_unescaped() {
        assert_eq!(unescape_octal("/mnt/a\\040b\\134c"), "/mnt/a b\\c");
        assert_eq!(unescape_octal("trailing\\04"), "trailing\\04");
    }
}
