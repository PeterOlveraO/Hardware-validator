//! Deterministic plain-text inventory presentation. Performs no detection.

use std::fmt::Display;

use crate::collector::{DetectionResult, DetectionStatus, InventoryResults};
use crate::models::{
    NetworkAddressInfo, NetworkGatewayInfo, NetworkInfo, NetworkInterfaceInfo,
    NetworkInterfaceType, PartitionInfo,
};
use crate::text::plain_text;

pub const UNKNOWN: &str = "Unknown";
const NONE_DETECTED: &str = "None detected";

/// Formats an optional value without inventing a replacement.
fn optional<T: Display>(value: Option<T>) -> String {
    value.map_or_else(|| UNKNOWN.to_owned(), |value| plain_text(&value.to_string()))
}

fn yes_no(value: Option<bool>) -> String {
    optional(value.map(|value| if value { "Yes" } else { "No" }))
}

fn connection(value: Option<bool>) -> String {
    optional(value.map(|value| if value { "Connected" } else { "Disconnected" }))
}

/// Scales into the largest unit not exceeding the value, with at most two
/// decimals (round half to even) and no trailing zeros: `1.5 GiB`, `4 GHz`.
fn scaled(value: u64, base: u64, units: &[&str]) -> String {
    let mut divisor: u128 = 1;
    let mut index = 0;
    while index < units.len() - 1 && u128::from(value) >= divisor * u128::from(base) {
        divisor *= u128::from(base);
        index += 1;
    }
    if divisor == 1 {
        return format!("{value} {}", units[index]);
    }
    let scaled = u128::from(value) * 100;
    let (mut hundredths, remainder) = (scaled / divisor, scaled % divisor);
    if remainder * 2 > divisor || (remainder * 2 == divisor && hundredths % 2 == 1) {
        hundredths += 1;
    }
    let number = format!("{}.{:02}", hundredths / 100, hundredths % 100);
    let number = number.trim_end_matches('0').trim_end_matches('.');
    format!("{number} {}", units[index])
}

const BYTE_UNITS: [&str; 7] = ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"];
const FREQUENCY_UNITS: [&str; 5] = ["Hz", "kHz", "MHz", "GHz", "THz"];
const SPEED_UNITS: [&str; 3] = ["Mbps", "Gbps", "Tbps"];

fn bytes(value: Option<u64>) -> String {
    optional(value.map(|value| scaled(value, 1024, &BYTE_UNITS)))
}

fn frequency(value: Option<u64>) -> String {
    optional(value.map(|value| scaled(value, 1000, &FREQUENCY_UNITS)))
}

fn network_speed(value: Option<u64>) -> String {
    optional(value.map(|value| scaled(value, 1000, &SPEED_UNITS)))
}

fn empty_collection(status: DetectionStatus) -> &'static str {
    if status == DetectionStatus::Complete { NONE_DETECTED } else { UNKNOWN }
}

/// Accumulates indented `Label: value` lines.
#[derive(Default)]
struct Lines(Vec<String>);

impl Lines {
    fn heading(&mut self, indent: usize, text: &str) {
        self.0.push(format!("{:indent$}{text}", ""));
    }

    fn field(&mut self, indent: usize, label: &str, value: String) {
        self.0.push(format!("{:indent$}{label}: {value}", ""));
    }

    fn strings(&mut self, indent: usize, label: &str, values: &[String], status: DetectionStatus) {
        if values.is_empty() {
            self.field(indent, label, empty_collection(status).to_owned());
        } else {
            self.heading(indent, &format!("{label}:"));
            for value in values {
                self.0.push(format!("{:width$}- {}", "", plain_text(value), width = indent + 2));
            }
        }
    }

    fn dns(&mut self, label: &str, values: Option<&[String]>) {
        match values {
            None => self.field(2, label, UNKNOWN.to_owned()),
            Some([]) => self.field(2, label, "None configured".to_owned()),
            Some(values) => self.strings(2, label, values, DetectionStatus::Complete),
        }
    }
}

fn join(sections: Vec<Lines>) -> String {
    sections.into_iter().map(|section| section.0.join("\n")).collect::<Vec<_>>().join("\n\n")
}

/// The short default inventory.
pub fn render_summary(results: &InventoryResults) -> String {
    join(vec![
        summary_computer(results),
        summary_processor(results),
        summary_memory(results),
        summary_storage(results),
        summary_graphics(results),
        summary_network(&results.network),
    ])
}

fn summary_computer(results: &InventoryResults) -> Lines {
    let system = &results.system.value;
    let mut lines = Lines::default();
    lines.heading(0, "Computer");
    lines.field(2, "Manufacturer", optional(system.manufacturer.as_ref()));
    lines.field(2, "Model", optional(system.model.as_ref()));
    lines.field(2, "Distribution", optional(system.distribution.as_ref()));
    lines.field(2, "Version", optional(system.distribution_version.as_ref()));
    lines.field(2, "Architecture", optional(system.architecture.as_ref()));
    lines
}

fn summary_processor(results: &InventoryResults) -> Lines {
    let cpu = &results.cpu.value;
    let mut lines = Lines::default();
    lines.heading(0, "Processor");
    lines.field(2, "Model", optional(cpu.model_names.first()));
    lines.field(2, "Physical Cores", optional(cpu.physical_core_count));
    lines.field(2, "Logical Processors", optional(cpu.logical_processor_count));
    lines.field(2, "Current Frequency", frequency(cpu.current_frequency_hz));
    lines.field(2, "Maximum Frequency", frequency(cpu.maximum_frequency_hz));
    lines
}

fn summary_memory(results: &InventoryResults) -> Lines {
    let memory = &results.memory.value;
    let mut lines = Lines::default();
    lines.heading(0, "Memory");
    lines.field(2, "Total", bytes(memory.total_bytes));
    lines.field(2, "Used", bytes(memory.used_bytes));
    lines.field(2, "Available", bytes(memory.available_bytes));
    lines.field(2, "Free", bytes(memory.free_bytes));
    let known = !memory.modules.is_empty() && memory.modules.iter().all(|m| m.has_any_value());
    let modules = known.then(|| format!("{} detected", memory.modules.len()));
    lines.field(2, "Modules", optional(modules));
    lines
}

fn summary_storage(results: &InventoryResults) -> Lines {
    use crate::models::BlockDeviceKind::{Removable, Virtual};
    let storage = &results.storage;
    let mut lines = Lines::default();
    lines.heading(0, "Storage");
    let disks: Vec<_> = storage
        .value
        .disks
        .iter()
        .filter(|disk| !matches!(disk.kind, Some(Virtual | Removable)))
        .collect();
    if disks.is_empty() {
        lines.field(2, "Devices", empty_collection(storage.status).to_owned());
    } else {
        lines.heading(2, "Devices:");
        for (index, disk) in disks.iter().enumerate() {
            lines.heading(4, &format!("Device {}:", index + 1));
            lines.field(6, "Model", optional(Some(disk.model.as_ref().unwrap_or(&disk.name))));
            lines.field(6, "Capacity", bytes(disk.size_bytes));
            lines.field(6, "Rotational", yes_no(disk.rotational));
        }
    }
    match storage.value.mounts.iter().find(|mount| mount.mount_point == "/") {
        None => lines.field(2, "Root Filesystem", UNKNOWN.to_owned()),
        Some(root) => {
            lines.heading(2, "Root Filesystem:");
            lines.field(4, "Total", bytes(root.total_bytes));
            lines.field(4, "Used", bytes(root.used_bytes));
            lines.field(4, "Available", bytes(root.available_bytes));
        }
    }
    lines
}

fn summary_graphics(results: &InventoryResults) -> Lines {
    let gpu = &results.gpu;
    let mut lines = Lines::default();
    lines.heading(0, "Graphics");
    if gpu.value.devices.is_empty() {
        lines.field(2, "Devices", empty_collection(gpu.status).to_owned());
        return lines;
    }
    lines.heading(2, "Devices:");
    for (index, device) in gpu.value.devices.iter().enumerate() {
        lines.heading(4, &format!("GPU {}:", index + 1));
        let name = match (&device.commercial_name, &device.vendor_id, &device.device_id) {
            (Some(name), _, _) => Some(plain_text(name)),
            (None, None, None) => None,
            (None, vendor, id) => {
                Some(format!("PCI device {}:{}", optional(vendor.as_ref()), optional(id.as_ref())))
            }
        };
        lines.field(6, "Name", name.unwrap_or_else(|| UNKNOWN.to_owned()));
        lines.field(6, "Driver", optional(device.driver.as_ref()));
        if let Some(kind) = device.adapter_type {
            lines.field(6, "Type", kind.as_str().to_owned());
        }
        if device.memory_total_bytes.is_some() {
            lines.field(6, "Memory", bytes(device.memory_total_bytes));
        }
    }
    lines
}

fn summary_interface(
    lines: &mut Lines,
    indent: usize,
    interface: &NetworkInterfaceInfo,
    network: &NetworkInfo,
) {
    let kind = if interface.interface_type == NetworkInterfaceType::Ethernet {
        "Ethernet"
    } else {
        "Wi-Fi"
    };
    lines.field(indent, "Type", kind.to_owned());
    lines.field(indent, "Interface", plain_text(&interface.name));
    lines.field(indent, "Status", connection(interface.is_up));
    if interface.speed_mbps.is_some() {
        lines.field(indent, "Speed", network_speed(interface.speed_mbps));
    }
    match interface.ipv4_addresses.as_deref() {
        None => {
            lines.field(indent, "IPv4", UNKNOWN.to_owned());
            lines.field(indent, "Subnet Mask", UNKNOWN.to_owned());
        }
        Some([]) => lines.field(indent, "IPv4", "None assigned".to_owned()),
        Some([address]) => {
            lines.field(indent, "IPv4", plain_text(&address.address));
            lines.field(indent, "Subnet Mask", optional(address.subnet_mask.as_ref()));
        }
        Some(addresses) => {
            for (index, address) in addresses.iter().enumerate() {
                let number = index + 1;
                lines.field(indent, &format!("IPv4 {number}"), plain_text(&address.address));
                let mask = optional(address.subnet_mask.as_ref());
                lines.field(indent, &format!("Subnet Mask {number}"), mask);
            }
        }
    }
    lines.field(indent, "MAC", optional(interface.mac_address.as_ref()));
    let gateway = network.ipv4_default_gateways.as_ref().map(|gateways| {
        gateways
            .iter()
            .find(|gateway| gateway.interface_name.as_deref() == Some(interface.name.as_str()))
            .map_or_else(|| "None assigned".to_owned(), |gateway| plain_text(&gateway.address))
    });
    lines.field(indent, "Default Gateway", gateway.unwrap_or_else(|| UNKNOWN.to_owned()));
}

const SUMMARY_GROUPS: [(&str, NetworkInterfaceType); 2] =
    [("Ethernet", NetworkInterfaceType::Ethernet), ("Wi-Fi", NetworkInterfaceType::Wifi)];

fn summary_network(result: &DetectionResult<NetworkInfo>) -> Lines {
    let network = &result.value;
    let mut lines = Lines::default();
    lines.heading(0, "Network");
    if network.interfaces.is_empty() && result.status != DetectionStatus::Complete {
        lines.field(2, "Interfaces", UNKNOWN.to_owned());
    } else {
        for (label, kind) in SUMMARY_GROUPS {
            let group: Vec<_> =
                network.interfaces.iter().filter(|i| i.interface_type == kind).collect();
            match group.as_slice() {
                [] => lines.field(2, label, empty_collection(result.status).to_owned()),
                [interface] => {
                    lines.heading(2, &format!("{label}:"));
                    summary_interface(&mut lines, 4, interface, network);
                }
                interfaces => {
                    lines.heading(2, &format!("{label}:"));
                    for (index, interface) in interfaces.iter().enumerate() {
                        lines.heading(4, &format!("Interface {}:", index + 1));
                        summary_interface(&mut lines, 6, interface, network);
                    }
                }
            }
        }
    }
    lines.0.push(String::new());
    lines.dns("Configured DNS", network.configured_dns.as_deref());
    lines
}

/// The complete technical inventory (`--verbose`).
pub fn render_inventory(results: &InventoryResults) -> String {
    join(vec![
        render_system(results),
        render_cpu(results),
        render_memory(results),
        render_storage(results),
        render_gpu(results),
        render_network(&results.network),
    ])
}

fn render_system(results: &InventoryResults) -> Lines {
    let system = &results.system.value;
    let mut lines = Lines::default();
    lines.heading(0, "System");
    lines.field(2, "Distribution", optional(system.distribution.as_ref()));
    lines.field(2, "Distribution Version", optional(system.distribution_version.as_ref()));
    lines.field(2, "Kernel Version", optional(system.kernel_version.as_ref()));
    lines.field(2, "Hostname", optional(system.hostname.as_ref()));
    lines.field(2, "Manufacturer", optional(system.manufacturer.as_ref()));
    lines.field(2, "Model", optional(system.model.as_ref()));
    lines.field(2, "Architecture", optional(system.architecture.as_ref()));
    lines.heading(2, "Baseboard:");
    lines.field(4, "Manufacturer", optional(system.baseboard.manufacturer.as_ref()));
    lines.field(4, "Model", optional(system.baseboard.model.as_ref()));
    lines.field(4, "Version", optional(system.baseboard.version.as_ref()));
    lines.heading(2, "BIOS:");
    lines.field(4, "Manufacturer", optional(system.bios.manufacturer.as_ref()));
    lines.field(4, "Version", optional(system.bios.version.as_ref()));
    lines.field(4, "Release Date", optional(system.bios.release_date.as_ref()));
    lines
}

fn render_cpu(results: &InventoryResults) -> Lines {
    let (cpu, status) = (&results.cpu.value, results.cpu.status);
    let mut lines = Lines::default();
    lines.heading(0, "CPU");
    lines.strings(2, "Model Names", &cpu.model_names, status);
    lines.strings(2, "Vendor IDs", &cpu.vendor_ids, status);
    lines.field(2, "Architecture", optional(cpu.architecture.as_ref()));
    lines.field(2, "Physical Packages", optional(cpu.physical_package_count));
    lines.field(2, "Physical Cores", optional(cpu.physical_core_count));
    lines.field(2, "Logical Processors", optional(cpu.logical_processor_count));
    lines.field(2, "Current Frequency", frequency(cpu.current_frequency_hz));
    lines.field(2, "Maximum Frequency", frequency(cpu.maximum_frequency_hz));
    if cpu.caches.is_empty() {
        lines.field(2, "Caches", empty_collection(status).to_owned());
    } else {
        lines.heading(2, "Caches:");
        for (index, cache) in cpu.caches.iter().enumerate() {
            lines.heading(4, &format!("Cache {}:", index + 1));
            lines.field(6, "Level", cache.level.to_string());
            lines.field(6, "Kind", cache.kind.as_str().to_owned());
            lines.field(6, "Size", bytes(cache.size_bytes));
        }
    }
    lines.strings(2, "Capabilities", &cpu.capabilities, status);
    lines
}

fn render_memory(results: &InventoryResults) -> Lines {
    let (memory, status) = (&results.memory.value, results.memory.status);
    let mut lines = Lines::default();
    lines.heading(0, "Memory");
    lines.field(2, "Total", bytes(memory.total_bytes));
    lines.field(2, "Available", bytes(memory.available_bytes));
    lines.field(2, "Used", bytes(memory.used_bytes));
    lines.field(2, "Free", bytes(memory.free_bytes));
    if memory.modules.is_empty() {
        lines.field(2, "Modules", empty_collection(status).to_owned());
    } else {
        lines.heading(2, "Modules:");
        for (index, module) in memory.modules.iter().enumerate() {
            lines.heading(4, &format!("Module {}:", index + 1));
            lines.field(6, "Locator", optional(module.locator.as_ref()));
            lines.field(6, "Size", bytes(module.size_bytes));
            lines.field(6, "Type", optional(module.memory_type.as_ref()));
        }
    }
    lines.heading(2, "Swap:");
    lines.field(4, "Total", bytes(memory.swap.total_bytes));
    lines.field(4, "Available", bytes(memory.swap.available_bytes));
    lines.field(4, "Used", bytes(memory.swap.used_bytes));
    lines
}

fn render_partition(
    lines: &mut Lines,
    index: usize,
    partition: &PartitionInfo,
    status: DetectionStatus,
) {
    lines.heading(8, &format!("Partition {index}:"));
    lines.field(10, "Name", plain_text(&partition.name));
    lines.field(10, "Parent Name", plain_text(&partition.parent_name));
    lines.field(10, "Device Path", optional(partition.device_path.as_ref()));
    lines.field(10, "Size", bytes(partition.size_bytes));
    lines.field(10, "Read Only", yes_no(partition.read_only));
    lines.field(10, "Filesystem", optional(partition.filesystem.as_ref()));
    lines.strings(10, "Mount Points", &partition.mount_points, status);
}

fn render_storage(results: &InventoryResults) -> Lines {
    let (storage, status) = (&results.storage.value, results.storage.status);
    let mut lines = Lines::default();
    lines.heading(0, "Storage");
    if storage.disks.is_empty() {
        lines.field(2, "Disks", empty_collection(status).to_owned());
    }
    for (index, disk) in storage.disks.iter().enumerate() {
        if index == 0 {
            lines.heading(2, "Disks:");
        }
        lines.heading(4, &format!("Disk {}:", index + 1));
        lines.field(6, "Name", plain_text(&disk.name));
        lines.field(6, "Device Path", optional(disk.device_path.as_ref()));
        lines.field(6, "Vendor", optional(disk.vendor.as_ref()));
        lines.field(6, "Model", optional(disk.model.as_ref()));
        lines.field(6, "Serial Number", optional(disk.serial_number.as_ref()));
        lines.field(6, "Size", bytes(disk.size_bytes));
        lines.field(6, "Rotational", yes_no(disk.rotational));
        lines.field(6, "Transport", optional(disk.transport.as_ref()));
        lines.field(6, "Removable", yes_no(disk.removable));
        lines.field(6, "Kind", optional(disk.kind.map(|kind| kind.as_str())));
        lines.field(6, "Read Only", yes_no(disk.read_only));
        lines.field(6, "Logical Sector Size", bytes(disk.logical_sector_size));
        lines.field(6, "Physical Sector Size", bytes(disk.physical_sector_size));
        if disk.partitions.is_empty() {
            lines.field(6, "Partitions", empty_collection(status).to_owned());
        } else {
            lines.heading(6, "Partitions:");
            for (index, partition) in disk.partitions.iter().enumerate() {
                render_partition(&mut lines, index + 1, partition, status);
            }
        }
    }
    if storage.mounts.is_empty() {
        lines.field(2, "Mounts", empty_collection(status).to_owned());
    } else {
        lines.heading(2, "Mounts:");
        for (index, mount) in storage.mounts.iter().enumerate() {
            lines.heading(4, &format!("Mount {}:", index + 1));
            lines.field(6, "Mount Point", plain_text(&mount.mount_point));
            lines.field(6, "Source", optional(mount.source.as_ref()));
            lines.field(6, "Filesystem", optional(mount.filesystem.as_ref()));
            lines.field(6, "Total", bytes(mount.total_bytes));
            lines.field(6, "Used", bytes(mount.used_bytes));
            lines.field(6, "Available", bytes(mount.available_bytes));
        }
    }
    lines
}

fn render_gpu(results: &InventoryResults) -> Lines {
    let (gpu, status) = (&results.gpu.value, results.gpu.status);
    let mut lines = Lines::default();
    lines.heading(0, "GPU");
    if gpu.devices.is_empty() {
        lines.field(2, "Devices", empty_collection(status).to_owned());
        return lines;
    }
    lines.heading(2, "Devices:");
    for (index, device) in gpu.devices.iter().enumerate() {
        lines.heading(4, &format!("GPU {}:", index + 1));
        lines.field(6, "Commercial Name", optional(device.commercial_name.as_ref()));
        lines.field(6, "Vendor ID", optional(device.vendor_id.as_ref()));
        lines.field(6, "Device ID", optional(device.device_id.as_ref()));
        lines.field(6, "Adapter Type", optional(device.adapter_type.map(|kind| kind.as_str())));
        lines.field(6, "Memory Total", bytes(device.memory_total_bytes));
        lines.field(6, "Driver", optional(device.driver.as_ref()));
        lines.field(6, "Address", optional(device.address.as_ref()));
        lines.field(6, "Boot VGA", yes_no(device.is_boot_vga));
        lines.strings(6, "Connectors", &device.connectors, status);
    }
    lines
}

fn render_addresses(
    lines: &mut Lines,
    label: &str,
    addresses: Option<&[NetworkAddressInfo]>,
    ipv4: bool,
) {
    match addresses {
        None => lines.field(6, label, UNKNOWN.to_owned()),
        Some([]) => lines.field(6, label, "None assigned".to_owned()),
        Some(addresses) => {
            lines.heading(6, &format!("{label}:"));
            for (index, address) in addresses.iter().enumerate() {
                lines.heading(8, &format!("Address {}:", index + 1));
                lines.field(10, "Address", plain_text(&address.address));
                if ipv4 {
                    lines.field(10, "Subnet Mask", optional(address.subnet_mask.as_ref()));
                }
                lines.field(10, "Prefix Length", optional(address.prefix_length));
            }
        }
    }
}

fn render_gateways(lines: &mut Lines, label: &str, gateways: Option<&[NetworkGatewayInfo]>) {
    match gateways {
        None => lines.field(2, label, UNKNOWN.to_owned()),
        Some([]) => lines.field(2, label, "None assigned".to_owned()),
        Some(gateways) => {
            lines.heading(2, &format!("{label}:"));
            for (index, gateway) in gateways.iter().enumerate() {
                lines.heading(4, &format!("Gateway {}:", index + 1));
                lines.field(6, "Address", plain_text(&gateway.address));
                lines.field(6, "Interface", optional(gateway.interface_name.as_ref()));
                lines.field(6, "Metric", optional(gateway.metric));
            }
        }
    }
}

fn render_network(result: &DetectionResult<NetworkInfo>) -> Lines {
    let network = &result.value;
    let mut lines = Lines::default();
    lines.heading(0, "Network");
    if network.interfaces.is_empty() {
        lines.field(2, "Interfaces", empty_collection(result.status).to_owned());
    } else {
        lines.heading(2, "Interfaces:");
    }
    for (index, interface) in network.interfaces.iter().enumerate() {
        lines.heading(4, &format!("Interface {}:", index + 1));
        lines.field(6, "Name", plain_text(&interface.name));
        lines.field(6, "Type", interface.interface_type.as_str().to_owned());
        lines.field(6, "Status", connection(interface.is_up));
        lines.field(6, "MAC", optional(interface.mac_address.as_ref()));
        render_addresses(&mut lines, "IPv4 Addresses", interface.ipv4_addresses.as_deref(), true);
        render_addresses(&mut lines, "IPv6 Addresses", interface.ipv6_addresses.as_deref(), false);
        lines.field(6, "Speed", network_speed(interface.speed_mbps));
        lines.field(6, "MTU", optional(interface.mtu.map(|mtu| format!("{mtu} bytes"))));
        lines.field(6, "Duplex", optional(interface.duplex.map(|duplex| duplex.label())));
    }
    render_gateways(&mut lines, "IPv4 Default Gateways", network.ipv4_default_gateways.as_deref());
    render_gateways(&mut lines, "IPv6 Default Gateways", network.ipv6_default_gateways.as_deref());
    lines.dns("Configured DNS", network.configured_dns.as_deref());
    if network.upstream_dns.is_some() {
        lines.dns("Upstream DNS", network.upstream_dns.as_deref());
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::*;

    fn complete(
        system: SystemInfo,
        cpu: CpuInfo,
        memory: MemoryInfo,
        storage: StorageInfo,
        gpu: GpuInventory,
        network: NetworkInfo,
    ) -> InventoryResults {
        InventoryResults {
            system: DetectionResult::complete(system),
            cpu: DetectionResult::complete(cpu),
            memory: DetectionResult::complete(memory),
            storage: DetectionResult::complete(storage),
            gpu: DetectionResult::complete(gpu),
            network: DetectionResult::complete(network),
        }
    }

    fn empty() -> InventoryResults {
        complete(
            SystemInfo::default(),
            CpuInfo::default(),
            MemoryInfo::default(),
            StorageInfo::default(),
            GpuInventory::default(),
            NetworkInfo::default(),
        )
    }

    const GIB: u64 = 1 << 30;

    fn interface(name: &str, kind: NetworkInterfaceType) -> NetworkInterfaceInfo {
        NetworkInterfaceInfo::new(name, kind)
    }

    fn snapshot() -> InventoryResults {
        complete(
            SystemInfo {
                distribution: Some("Example Linux".into()),
                distribution_version: Some("24.04".into()),
                kernel_version: Some("6.8.0".into()),
                hostname: Some("workstation".into()),
                manufacturer: Some("System Maker".into()),
                model: Some("System Model".into()),
                architecture: Some("x86_64".into()),
                baseboard: BaseboardInfo {
                    model: Some("Hidden Board".into()),
                    ..Default::default()
                },
                bios: BiosInfo { version: Some("Hidden BIOS".into()), ..Default::default() },
            },
            CpuInfo {
                model_names: vec!["Main CPU".into(), "Secondary CPU".into()],
                vendor_ids: vec!["Hidden Vendor".into()],
                physical_package_count: Some(2),
                physical_core_count: Some(8),
                logical_processor_count: Some(16),
                current_frequency_hz: Some(2_500_000_000),
                maximum_frequency_hz: Some(4_000_000_000),
                caches: vec![CpuCacheInfo {
                    level: 1,
                    kind: CacheKind::Data,
                    size_bytes: Some(32768),
                }],
                capabilities: vec!["hidden_flag".into()],
                ..Default::default()
            },
            MemoryInfo {
                total_bytes: Some(16 * GIB),
                available_bytes: Some(6 * GIB),
                used_bytes: Some(10 * GIB),
                free_bytes: Some(2 * GIB),
                modules: vec![
                    MemoryModuleInfo {
                        locator: Some("DIMM_A1".into()),
                        size_bytes: Some(8 * GIB),
                        memory_type: None,
                    },
                    MemoryModuleInfo {
                        locator: Some("DIMM_B1".into()),
                        size_bytes: Some(8 * GIB),
                        memory_type: None,
                    },
                ],
                swap: SwapInfo {
                    total_bytes: Some(2 * GIB),
                    available_bytes: Some(GIB),
                    used_bytes: Some(GIB),
                },
            },
            StorageInfo {
                disks: vec![
                    DiskInfo {
                        name: "loop0".into(),
                        model: Some("Hidden Loop".into()),
                        kind: Some(BlockDeviceKind::Virtual),
                        ..Default::default()
                    },
                    DiskInfo {
                        name: "usb0".into(),
                        model: Some("Hidden USB".into()),
                        kind: Some(BlockDeviceKind::Removable),
                        ..Default::default()
                    },
                    DiskInfo {
                        name: "nvme0n1".into(),
                        serial_number: Some("hidden-serial".into()),
                        size_bytes: Some(1 << 40),
                        rotational: Some(false),
                        transport: Some("nvme".into()),
                        partitions: vec![PartitionInfo {
                            name: "nvme0n1p1".into(),
                            parent_name: "nvme0n1".into(),
                            device_path: None,
                            size_bytes: None,
                            read_only: None,
                            filesystem: Some("ext4".into()),
                            mount_points: vec!["/".into()],
                        }],
                        ..Default::default()
                    },
                ],
                mounts: vec![MountInfo {
                    mount_point: "/".into(),
                    source: Some("/dev/nvme0n1p1".into()),
                    filesystem: Some("ext4".into()),
                    total_bytes: Some(512 * GIB),
                    used_bytes: Some(128 * GIB),
                    available_bytes: Some(384 * GIB),
                }],
            },
            GpuInventory {
                devices: vec![
                    GpuInfo {
                        commercial_name: Some("Named GPU".into()),
                        driver: Some("driver-a".into()),
                        ..Default::default()
                    },
                    GpuInfo {
                        vendor_id: Some("0x1234".into()),
                        device_id: Some("0xabcd".into()),
                        adapter_type: Some(AdapterType::Dedicated),
                        memory_total_bytes: Some(8 * GIB),
                        driver: Some("driver-b".into()),
                        address: Some("0000:01:00.0".into()),
                        is_boot_vga: Some(true),
                        connectors: vec!["HDMI-A-1".into()],
                        ..Default::default()
                    },
                ],
            },
            NetworkInfo {
                interfaces: vec![
                    NetworkInterfaceInfo {
                        is_up: Some(true),
                        mac_address: Some("00:11:22:33:44:55".into()),
                        ipv4_addresses: Some(vec![NetworkAddressInfo {
                            address: "192.0.2.10".into(),
                            prefix_length: Some(24),
                            subnet_mask: Some("255.255.255.0".into()),
                        }]),
                        ipv6_addresses: Some(vec![NetworkAddressInfo {
                            address: "fe80::10%uplink0".into(),
                            prefix_length: Some(64),
                            subnet_mask: None,
                        }]),
                        speed_mbps: Some(1000),
                        mtu: Some(1500),
                        duplex: Some(NetworkDuplex::Full),
                        ..interface("uplink0", NetworkInterfaceType::Ethernet)
                    },
                    NetworkInterfaceInfo {
                        is_up: Some(false),
                        ipv4_addresses: Some(vec![]),
                        ipv6_addresses: Some(vec![]),
                        ..interface("radio0", NetworkInterfaceType::Wifi)
                    },
                    NetworkInterfaceInfo {
                        is_up: Some(true),
                        ..interface("local0", NetworkInterfaceType::Loopback)
                    },
                    interface("mystery0", NetworkInterfaceType::Unknown),
                ],
                ipv4_default_gateways: Some(vec![NetworkGatewayInfo {
                    address: "192.0.2.1".into(),
                    interface_name: Some("uplink0".into()),
                    metric: Some(100),
                }]),
                ipv6_default_gateways: Some(vec![]),
                configured_dns: Some(vec!["127.0.0.53".into()]),
                upstream_dns: Some(vec!["192.0.2.53".into()]),
            },
        )
    }

    #[test]
    fn summary_snapshot_contains_only_primary_information() {
        let expected = "\
Computer
  Manufacturer: System Maker
  Model: System Model
  Distribution: Example Linux
  Version: 24.04
  Architecture: x86_64

Processor
  Model: Main CPU
  Physical Cores: 8
  Logical Processors: 16
  Current Frequency: 2.5 GHz
  Maximum Frequency: 4 GHz

Memory
  Total: 16 GiB
  Used: 10 GiB
  Available: 6 GiB
  Free: 2 GiB
  Modules: 2 detected

Storage
  Devices:
    Device 1:
      Model: nvme0n1
      Capacity: 1 TiB
      Rotational: No
  Root Filesystem:
    Total: 512 GiB
    Used: 128 GiB
    Available: 384 GiB

Graphics
  Devices:
    GPU 1:
      Name: Named GPU
      Driver: driver-a
    GPU 2:
      Name: PCI device 0x1234:0xabcd
      Driver: driver-b
      Type: dedicated
      Memory: 8 GiB

Network
  Ethernet:
    Type: Ethernet
    Interface: uplink0
    Status: Connected
    Speed: 1 Gbps
    IPv4: 192.0.2.10
    Subnet Mask: 255.255.255.0
    MAC: 00:11:22:33:44:55
    Default Gateway: 192.0.2.1
  Wi-Fi:
    Type: Wi-Fi
    Interface: radio0
    Status: Disconnected
    IPv4: None assigned
    MAC: Unknown
    Default Gateway: None assigned

  Configured DNS:
    - 127.0.0.53";
        assert_eq!(render_summary(&snapshot()), expected);
    }

    #[test]
    fn complete_empty_inventory_says_none_detected_or_unknown() {
        let expected = "\
System
  Distribution: Unknown
  Distribution Version: Unknown
  Kernel Version: Unknown
  Hostname: Unknown
  Manufacturer: Unknown
  Model: Unknown
  Architecture: Unknown
  Baseboard:
    Manufacturer: Unknown
    Model: Unknown
    Version: Unknown
  BIOS:
    Manufacturer: Unknown
    Version: Unknown
    Release Date: Unknown

CPU
  Model Names: None detected
  Vendor IDs: None detected
  Architecture: Unknown
  Physical Packages: Unknown
  Physical Cores: Unknown
  Logical Processors: Unknown
  Current Frequency: Unknown
  Maximum Frequency: Unknown
  Caches: None detected
  Capabilities: None detected

Memory
  Total: Unknown
  Available: Unknown
  Used: Unknown
  Free: Unknown
  Modules: None detected
  Swap:
    Total: Unknown
    Available: Unknown
    Used: Unknown

Storage
  Disks: None detected
  Mounts: None detected

GPU
  Devices: None detected

Network
  Interfaces: None detected
  IPv4 Default Gateways: Unknown
  IPv6 Default Gateways: Unknown
  Configured DNS: Unknown";
        assert_eq!(render_inventory(&empty()), expected);

        let summary = render_summary(&empty());
        assert!(summary.contains("Modules: Unknown"));
        assert!(summary.contains("Storage\n  Devices: None detected\n  Root Filesystem: Unknown"));
        assert!(summary.contains("Network\n  Ethernet: None detected\n  Wi-Fi: None detected"));

        let mut unavailable = empty();
        unavailable.network = DetectionResult::unavailable(NetworkInfo::default(), "fixture");
        assert!(render_summary(&unavailable).contains("Network\n  Interfaces: Unknown"));
        unavailable.cpu.status = DetectionStatus::Partial;
        assert!(render_inventory(&unavailable).contains("Model Names: Unknown"));
    }

    #[test]
    fn verbose_network_lists_every_interface_and_address_detail() {
        let report = render_inventory(&snapshot());
        for expected in [
            "    Interface 1:\n      Name: uplink0\n      Type: ethernet\n      Status: Connected\n      MAC: 00:11:22:33:44:55\n      IPv4 Addresses:\n        Address 1:\n          Address: 192.0.2.10\n          Subnet Mask: 255.255.255.0\n          Prefix Length: 24\n      IPv6 Addresses:\n        Address 1:\n          Address: fe80::10%uplink0\n          Prefix Length: 64\n      Speed: 1 Gbps\n      MTU: 1500 bytes\n      Duplex: Full",
            "      Name: radio0\n      Type: wifi\n      Status: Disconnected\n      MAC: Unknown\n      IPv4 Addresses: None assigned\n      IPv6 Addresses: None assigned",
            "      Name: local0\n      Type: loopback",
            "      Name: mystery0\n      Type: unknown\n      Status: Unknown\n      MAC: Unknown\n      IPv4 Addresses: Unknown",
            "  IPv4 Default Gateways:\n    Gateway 1:\n      Address: 192.0.2.1\n      Interface: uplink0\n      Metric: 100\n  IPv6 Default Gateways: None assigned\n  Configured DNS:\n    - 127.0.0.53\n  Upstream DNS:\n    - 192.0.2.53",
            "      Boot VGA: Yes\n      Connectors:\n        - HDMI-A-1",
            "        Partition 1:\n          Name: nvme0n1p1\n          Parent Name: nvme0n1\n          Device Path: Unknown",
        ] {
            assert!(report.contains(expected), "missing:\n{expected}\n\nin:\n{report}");
        }
    }

    #[test]
    fn units_rounding_and_large_values() {
        assert_eq!(bytes(Some(0)), "0 B");
        assert_eq!(bytes(Some(1023)), "1023 B");
        assert_eq!(bytes(Some(1536)), "1.5 KiB");
        assert_eq!(bytes(Some(1024 * 1024 - 1)), "1024 KiB");
        assert_eq!(bytes(Some(u64::MAX)), "16 EiB");
        assert_eq!(frequency(Some(2_345_000_000)), "2.34 GHz", "half to even");
        assert_eq!(frequency(Some(2_355_000_000)), "2.36 GHz");
        assert_eq!(frequency(Some(999)), "999 Hz");
        assert_eq!(network_speed(Some(2500)), "2.5 Gbps");
        assert_eq!(bytes(None), UNKNOWN);
    }

    #[test]
    fn control_characters_are_escaped() {
        let mut results = empty();
        results.system.value.model = Some("Evil\x1b[2JModel\u{202e}".into());
        assert!(render_summary(&results).contains("  Model: Evil\\x1b[2JModel\\u202e"));
    }
}
