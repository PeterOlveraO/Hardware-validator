//! Public hardware inventory data models.
//!
//! `None` always means "unknown"; detectors never invent replacement values.

#[derive(Debug, Clone, Default, PartialEq)]
pub struct BaseboardInfo {
    pub manufacturer: Option<String>,
    pub model: Option<String>,
    pub version: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct BiosInfo {
    pub manufacturer: Option<String>,
    pub version: Option<String>,
    pub release_date: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct SystemInfo {
    pub distribution: Option<String>,
    pub distribution_version: Option<String>,
    pub kernel_version: Option<String>,
    pub hostname: Option<String>,
    pub manufacturer: Option<String>,
    pub model: Option<String>,
    pub architecture: Option<String>,
    pub baseboard: BaseboardInfo,
    pub bios: BiosInfo,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum CacheKind {
    Data,
    Instruction,
    Unified,
}

impl CacheKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Data => "data",
            Self::Instruction => "instruction",
            Self::Unified => "unified",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct CpuCacheInfo {
    pub level: u32,
    pub kind: CacheKind,
    pub size_bytes: Option<u64>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct CpuInfo {
    pub model_names: Vec<String>,
    pub vendor_ids: Vec<String>,
    pub architecture: Option<String>,
    pub physical_package_count: Option<u32>,
    pub physical_core_count: Option<u32>,
    pub logical_processor_count: Option<u32>,
    pub current_frequency_hz: Option<u64>,
    pub maximum_frequency_hz: Option<u64>,
    pub caches: Vec<CpuCacheInfo>,
    pub capabilities: Vec<String>,
}

/// Fields the Linux EDAC sysfs ABI exposes without root.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct MemoryModuleInfo {
    pub locator: Option<String>,
    pub size_bytes: Option<u64>,
    pub memory_type: Option<String>,
}

impl MemoryModuleInfo {
    pub fn has_any_value(&self) -> bool {
        self.locator.is_some() || self.size_bytes.is_some() || self.memory_type.is_some()
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct SwapInfo {
    pub total_bytes: Option<u64>,
    pub available_bytes: Option<u64>,
    pub used_bytes: Option<u64>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct MemoryInfo {
    pub total_bytes: Option<u64>,
    pub available_bytes: Option<u64>,
    /// Always `total - available` when both are known.
    pub used_bytes: Option<u64>,
    pub free_bytes: Option<u64>,
    pub modules: Vec<MemoryModuleInfo>,
    pub swap: SwapInfo,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PartitionInfo {
    pub name: String,
    pub parent_name: String,
    pub device_path: Option<String>,
    pub size_bytes: Option<u64>,
    pub read_only: Option<bool>,
    pub filesystem: Option<String>,
    pub mount_points: Vec<String>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct MountInfo {
    pub mount_point: String,
    pub source: Option<String>,
    pub filesystem: Option<String>,
    pub total_bytes: Option<u64>,
    pub used_bytes: Option<u64>,
    pub available_bytes: Option<u64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BlockDeviceKind {
    Hdd,
    Ssd,
    Nvme,
    Emmc,
    Removable,
    Virtual,
}

impl BlockDeviceKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Hdd => "hdd",
            Self::Ssd => "ssd",
            Self::Nvme => "nvme",
            Self::Emmc => "emmc",
            Self::Removable => "removable",
            Self::Virtual => "virtual",
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct DiskInfo {
    pub name: String,
    pub device_path: Option<String>,
    pub vendor: Option<String>,
    pub model: Option<String>,
    pub serial_number: Option<String>,
    pub size_bytes: Option<u64>,
    pub rotational: Option<bool>,
    pub transport: Option<String>,
    pub removable: Option<bool>,
    pub kind: Option<BlockDeviceKind>,
    pub read_only: Option<bool>,
    pub logical_sector_size: Option<u64>,
    pub physical_sector_size: Option<u64>,
    pub partitions: Vec<PartitionInfo>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct StorageInfo {
    pub disks: Vec<DiskInfo>,
    pub mounts: Vec<MountInfo>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AdapterType {
    Integrated,
    Dedicated,
}

impl AdapterType {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Integrated => "integrated",
            Self::Dedicated => "dedicated",
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct GpuInfo {
    pub commercial_name: Option<String>,
    /// Lowercase `0xNNNN` PCI identifier.
    pub vendor_id: Option<String>,
    /// Lowercase `0xNNNN` PCI identifier.
    pub device_id: Option<String>,
    pub adapter_type: Option<AdapterType>,
    pub memory_total_bytes: Option<u64>,
    pub driver: Option<String>,
    pub address: Option<String>,
    pub is_boot_vga: Option<bool>,
    pub connectors: Vec<String>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct GpuInventory {
    pub devices: Vec<GpuInfo>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NetworkInterfaceType {
    Ethernet,
    Wifi,
    Loopback,
    Virtual,
    Unknown,
}

impl NetworkInterfaceType {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Ethernet => "ethernet",
            Self::Wifi => "wifi",
            Self::Loopback => "loopback",
            Self::Virtual => "virtual",
            Self::Unknown => "unknown",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NetworkDuplex {
    Full,
    Half,
}

impl NetworkDuplex {
    pub fn label(self) -> &'static str {
        match self {
            Self::Full => "Full",
            Self::Half => "Half",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NetworkAddressInfo {
    /// Textual address; IPv6 link-local addresses keep their `%interface` scope.
    pub address: String,
    pub prefix_length: Option<u8>,
    pub subnet_mask: Option<String>,
}

impl NetworkAddressInfo {
    pub fn bare(address: impl Into<String>) -> Self {
        Self { address: address.into(), prefix_length: None, subnet_mask: None }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NetworkGatewayInfo {
    pub address: String,
    pub interface_name: Option<String>,
    pub metric: Option<u32>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct NetworkInterfaceInfo {
    pub name: String,
    pub interface_type: NetworkInterfaceType,
    pub is_up: Option<bool>,
    pub mac_address: Option<String>,
    /// `None` when unknown, empty when known to have no address.
    pub ipv4_addresses: Option<Vec<NetworkAddressInfo>>,
    pub ipv6_addresses: Option<Vec<NetworkAddressInfo>>,
    pub speed_mbps: Option<u64>,
    pub mtu: Option<u32>,
    pub duplex: Option<NetworkDuplex>,
}

impl NetworkInterfaceInfo {
    pub fn new(name: impl Into<String>, interface_type: NetworkInterfaceType) -> Self {
        Self {
            name: name.into(),
            interface_type,
            is_up: None,
            mac_address: None,
            ipv4_addresses: None,
            ipv6_addresses: None,
            speed_mbps: None,
            mtu: None,
            duplex: None,
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct NetworkInfo {
    pub interfaces: Vec<NetworkInterfaceInfo>,
    pub ipv4_default_gateways: Option<Vec<NetworkGatewayInfo>>,
    pub ipv6_default_gateways: Option<Vec<NetworkGatewayInfo>>,
    pub configured_dns: Option<Vec<String>>,
    pub upstream_dns: Option<Vec<String>>,
}
