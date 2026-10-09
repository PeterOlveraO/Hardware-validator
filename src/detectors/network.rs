//! Local network interface configuration from sysfs, getifaddrs, the kernel
//! routing tables, and resolv.conf. Sends no traffic and performs no lookups.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};
use std::path::{Path, PathBuf};

use crate::collector::{DetectionResult, Issues};
use crate::host::{AddressFamily, Host, InterfaceAddress};
use crate::models::{
    NetworkAddressInfo, NetworkDuplex, NetworkGatewayInfo, NetworkInfo, NetworkInterfaceInfo,
    NetworkInterfaceType,
};
use crate::text::read_optional;

const ARPHRD_ETHER: u32 = 1;
const ARPHRD_LOOPBACK: u32 = 772;
const IFF_UP: u32 = 0x1;
const RTF_UP_GATEWAY: u32 = 0x3;
const RTF_REJECT: u32 = 0x200;

pub fn detect(host: &Host) -> DetectionResult<NetworkInfo> {
    let mut issues = Issues::default();
    let sys_net = host.path("/sys/class/net");
    let sysfs = list_sysfs(&sys_net, &mut issues);
    let addresses = match &host.interface_addresses {
        Ok(addresses) => Some(addresses.as_slice()),
        Err(_) => {
            issues.push("Unable to enumerate network addresses.");
            None
        }
    };

    let mut names: BTreeSet<&str> = sysfs.iter().flatten().map(|(name, _)| name.as_str()).collect();
    for address in addresses.into_iter().flatten() {
        if valid_name(&address.interface) {
            names.insert(&address.interface);
        } else {
            issues.push("Malformed network address data.");
        }
    }

    let mut interfaces: Vec<(String, NetworkInterfaceInfo)> = names
        .into_iter()
        .map(|name| {
            let identity = sysfs.as_ref().and_then(|sysfs| sysfs.get(name));
            let mut interface = NetworkInterfaceInfo::new(name, NetworkInterfaceType::Unknown);
            if let Some(identity) = identity {
                let path = sys_net.join(name);
                interface.interface_type = classify(&path, identity.as_deref(), &mut issues);
                read_statistics(&path, &mut interface, &mut issues);
            }
            if let Some(addresses) = addresses {
                apply_addresses(&mut interface, addresses, &mut issues);
            }
            let sort_key = match identity {
                Some(Some(identity)) => identity.to_string_lossy().into_owned(),
                _ => format!("~{name}"),
            };
            (sort_key, interface)
        })
        .collect();
    interfaces.sort_by(|a, b| (&a.0, &a.1.name).cmp(&(&b.0, &b.1.name)));
    let interfaces: Vec<NetworkInterfaceInfo> =
        interfaces.into_iter().map(|(_, interface)| interface).collect();

    let known: BTreeSet<&str> = interfaces.iter().map(|i| i.name.as_str()).collect();
    let little_endian = cfg!(target_endian = "little");
    let ipv4_default_gateways = match read_optional(&host.path("/proc/net/route")) {
        Ok(Some(text)) => ipv4_default_routes(&text, little_endian, &known, &mut issues),
        Ok(None) => {
            issues.push("IPv4 routing information is unavailable.");
            None
        }
        Err(_) => {
            issues.push("Unable to read IPv4 routing information.");
            None
        }
    };
    let ipv6_default_gateways = match read_optional(&host.path("/proc/net/ipv6_route")) {
        Ok(Some(text)) => ipv6_default_routes(&text, &known, &mut issues),
        Ok(None) => None,
        Err(_) => {
            issues.push("Unable to read IPv6 routing information.");
            None
        }
    };
    let configured_dns = match read_optional(&host.path("/etc/resolv.conf")) {
        Ok(Some(text)) => nameservers(&text, "Malformed DNS configuration.", &mut issues),
        Ok(None) | Err(_) => {
            issues.push("Configured DNS is unavailable.");
            None
        }
    };
    let upstream_dns = match read_optional(&host.path("/run/systemd/resolve/resolv.conf")) {
        Ok(Some(text)) => nameservers(&text, "Malformed upstream DNS configuration.", &mut issues),
        Ok(None) => None,
        Err(_) => {
            issues.push("Unable to read upstream DNS configuration.");
            None
        }
    };

    let value = NetworkInfo {
        interfaces,
        ipv4_default_gateways,
        ipv6_default_gateways,
        configured_dns,
        upstream_dns,
    };
    let useful = sysfs.is_some() || addresses.is_some() || value != NetworkInfo::default();
    DetectionResult::from_issues(value, issues, useful, "No network information was available.")
}

fn valid_name(name: &str) -> bool {
    !name.trim().is_empty() && name != "." && name != ".." && !name.contains(['/', '\0'])
}

/// Interface name -> canonical sysfs identity (`None` when it cannot be resolved).
type SysfsInterfaces = BTreeMap<String, Option<PathBuf>>;

fn list_sysfs(root: &Path, issues: &mut Issues) -> Option<SysfsInterfaces> {
    let read = match fs::read_dir(root) {
        Ok(read) => read,
        Err(error) => {
            issues.push(if error.kind() == io::ErrorKind::NotFound {
                "Network sysfs is unavailable."
            } else {
                "Unable to enumerate network sysfs."
            });
            return None;
        }
    };
    let mut interfaces = BTreeMap::new();
    for entry in read.filter_map(Result::ok) {
        let name = entry.file_name().to_string_lossy().into_owned();
        if !valid_name(&name) {
            issues.push("Malformed network sysfs entry.");
            continue;
        }
        let identity = match fs::canonicalize(entry.path()) {
            Ok(identity) => Some(identity),
            Err(error) if error.kind() == io::ErrorKind::NotFound => None,
            Err(_) => {
                issues.push(format!("Unable to resolve network interface {name}."));
                None
            }
        };
        interfaces.insert(name, identity);
    }
    Some(interfaces)
}

/// Reads a sysfs attribute; read errors become an issue and `None`.
fn attribute(path: &Path, file: &str, issues: &mut Issues) -> Option<String> {
    read_optional(&path.join(file)).unwrap_or_else(|_| {
        let name = path.file_name().unwrap_or_default().to_string_lossy();
        issues.push(format!("Unable to read network attributes for {name}."));
        None
    })
}

fn exists(path: &Path, name: &str, issues: &mut Issues) -> Option<bool> {
    match path.try_exists() {
        Ok(exists) => Some(exists),
        Err(_) => {
            issues.push(format!("Unable to inspect network interface {name}."));
            None
        }
    }
}

/// Evidence-based classification; interface names are never used to guess.
fn classify(path: &Path, identity: Option<&Path>, issues: &mut Issues) -> NetworkInterfaceType {
    let name = path.file_name().unwrap_or_default().to_string_lossy().into_owned();
    let hardware_type = attribute(path, "type", issues).and_then(|text| {
        let parsed = text.trim().parse::<u32>().ok();
        if parsed.is_none() {
            issues.push(format!("Malformed network type for {name}."));
        }
        parsed
    });
    if hardware_type == Some(ARPHRD_LOOPBACK) {
        return NetworkInterfaceType::Loopback;
    }
    let virtual_identity = identity.is_some_and(|identity| {
        let parts: Vec<_> = identity.components().map(|part| part.as_os_str()).collect();
        parts.windows(3).any(|window| window == ["devices", "virtual", "net"])
    });
    if virtual_identity {
        return NetworkInterfaceType::Virtual;
    }
    let wireless = exists(&path.join("wireless"), &name, issues);
    let phy80211 = exists(&path.join("phy80211"), &name, issues);
    let device = exists(&path.join("device"), &name, issues);
    if wireless == Some(true) || phy80211 == Some(true) {
        return if device == Some(true) {
            NetworkInterfaceType::Wifi
        } else {
            NetworkInterfaceType::Unknown
        };
    }
    if wireless.is_none() || phy80211.is_none() {
        return NetworkInterfaceType::Unknown;
    }
    if hardware_type == Some(ARPHRD_ETHER) && identity.is_some() && device == Some(true) {
        return NetworkInterfaceType::Ethernet;
    }
    NetworkInterfaceType::Unknown
}

/// Link state, speed, MTU, and duplex from sysfs. The kernel rejects reads of
/// `speed` and `duplex` while a link is down, which simply means unknown.
fn read_statistics(path: &Path, interface: &mut NetworkInterfaceInfo, issues: &mut Issues) {
    let name = interface.name.clone();
    let mut malformed = false;
    let flags = attribute(path, "flags", issues).map(|text| {
        let text = text.trim();
        u32::from_str_radix(text.trim_start_matches("0x"), 16).ok()
    });
    let operstate = attribute(path, "operstate", issues);
    interface.is_up = match (flags, operstate.as_deref().map(str::trim)) {
        (Some(Some(flags)), Some(state)) => {
            // psutil semantics: administratively up and operationally running.
            Some(flags & IFF_UP != 0 && matches!(state, "up" | "unknown"))
        }
        (Some(None), _) => {
            malformed = true;
            None
        }
        _ => None,
    };
    let quiet = |file: &str| read_optional(&path.join(file)).ok().flatten();
    interface.speed_mbps = quiet("speed")
        .and_then(|text| text.trim().parse::<i64>().ok())
        .and_then(|speed| u64::try_from(speed).ok())
        .filter(|speed| *speed > 0);
    interface.duplex = match quiet("duplex").as_deref().map(str::trim) {
        Some("full") => Some(NetworkDuplex::Full),
        Some("half") => Some(NetworkDuplex::Half),
        _ => None,
    };
    interface.mtu = attribute(path, "mtu", issues).and_then(|text| {
        let mtu = text.trim().parse::<u32>().ok().filter(|mtu| *mtu > 0);
        malformed |= mtu.is_none();
        mtu
    });
    if malformed {
        issues.push(format!("Malformed network statistics for {name}."));
    }
}

fn apply_addresses(
    interface: &mut NetworkInterfaceInfo,
    addresses: &[InterfaceAddress],
    issues: &mut Issues,
) {
    let mut ipv4 = Merged::default();
    let mut ipv6 = Merged::default();
    for address in addresses.iter().filter(|address| address.interface == interface.name) {
        match &address.family {
            AddressFamily::Link(mac) => {
                interface.mac_address.get_or_insert_with(|| mac.clone());
            }
            AddressFamily::Ipv4 { address, netmask } => {
                let mask = netmask.map(|mask| (u128::from(u32::from(mask)), 32, mask.to_string()));
                ipv4.add(address.to_string(), mask, &interface.name, issues);
            }
            AddressFamily::Ipv6 { address, netmask, scope_id } => {
                let text = if *scope_id != 0 {
                    format!("{address}%{}", interface.name)
                } else {
                    address.to_string()
                };
                let mask = netmask.map(|mask| (u128::from(mask), 128, mask.to_string()));
                ipv6.add(text, mask, &interface.name, issues);
            }
        }
    }
    interface.ipv4_addresses = Some(ipv4.addresses.into_values().collect());
    interface.ipv6_addresses = Some(ipv6.addresses.into_values().collect());
}

/// Addresses of one family keyed by text; conflicting masks become unknown.
#[derive(Default)]
struct Merged {
    addresses: BTreeMap<String, NetworkAddressInfo>,
    conflicts: BTreeSet<String>,
}

impl Merged {
    fn add(
        &mut self,
        address: String,
        mask: Option<(u128, u32, String)>,
        name: &str,
        issues: &mut Issues,
    ) {
        let mut information = NetworkAddressInfo::bare(address.clone());
        if let Some((value, width, text)) = mask {
            let prefix = value.count_ones();
            let contiguous =
                prefix == 0 || value == (u128::MAX >> (128 - prefix)) << (width - prefix);
            if contiguous {
                information.prefix_length = u8::try_from(prefix).ok();
                information.subnet_mask = Some(text);
            } else {
                issues.push(format!("Malformed network address for {name}."));
            }
        }
        if self.conflicts.contains(&address) {
            return;
        }
        let Some(previous) = self.addresses.get(&address) else {
            self.addresses.insert(address, information);
            return;
        };
        let known = |info: &NetworkAddressInfo| info.prefix_length.is_some();
        if !known(previous) {
            self.addresses.insert(address, information);
        } else if known(&information) && information != *previous {
            self.addresses.insert(address.clone(), NetworkAddressInfo::bare(address.clone()));
            self.conflicts.insert(address);
            issues.push(format!("Malformed network address for {name}."));
        }
    }
}

fn is_hex(value: &str, length: usize) -> bool {
    value.len() == length && value.bytes().all(|b| b.is_ascii_hexdigit())
}

fn is_decimal(value: &str) -> bool {
    !value.is_empty() && value.bytes().all(|b| b.is_ascii_digit())
}

/// Sorted by metric (unknown last), then interface, then address.
fn sorted_gateways(mut routes: Vec<(NetworkGatewayInfo, String)>) -> Vec<NetworkGatewayInfo> {
    routes.sort_by_key(|(gateway, interface)| {
        (gateway.metric.is_none(), gateway.metric, interface.clone(), gateway.address.clone())
    });
    routes.into_iter().map(|(gateway, _)| gateway).collect()
}

/// Default gateways from `/proc/net/route`, whose addresses are hex in host
/// byte order. `None` when the table is unusable.
fn ipv4_default_routes(
    text: &str,
    little_endian: bool,
    known: &BTreeSet<&str>,
    issues: &mut Issues,
) -> Option<Vec<NetworkGatewayInfo>> {
    const HEADER: [&str; 11] = [
        "Iface",
        "Destination",
        "Gateway",
        "Flags",
        "RefCnt",
        "Use",
        "Metric",
        "Mask",
        "MTU",
        "Window",
        "IRTT",
    ];
    let mut lines = text
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| line.split_whitespace().collect::<Vec<&str>>());
    if lines.next().as_deref() != Some(HEADER.as_slice()) {
        issues.push("Malformed IPv4 routing information.");
        return None;
    }
    let mut routes = Vec::new();
    let mut malformed = false;
    let mut reject = |issues: &mut Issues| {
        issues.push("Malformed IPv4 default route.");
        malformed = true;
    };
    for fields in lines {
        let field = |index: usize| fields.get(index).copied().unwrap_or_default();
        if !is_hex(field(1), 8) {
            reject(issues);
            continue;
        }
        if field(1) != "00000000" {
            continue;
        }
        if !is_hex(field(7), 8) {
            reject(issues);
            continue;
        }
        if field(7) != "00000000" {
            continue;
        }
        let counters_valid = [4, 5, 8, 9, 10].iter().all(|index| is_decimal(field(*index)));
        if fields.len() != HEADER.len() || !counters_valid {
            reject(issues);
            continue;
        }
        if !is_hex(field(3), 4) {
            reject(issues);
            continue;
        }
        let flags = u32::from_str_radix(field(3), 16).unwrap_or_default();
        if flags & RTF_UP_GATEWAY != RTF_UP_GATEWAY || flags & RTF_REJECT != 0 {
            continue;
        }
        let interface = field(0);
        if !is_hex(field(2), 8) || !valid_name(interface) {
            reject(issues);
            continue;
        }
        let raw = u32::from_str_radix(field(2), 16).unwrap_or_default();
        let address = Ipv4Addr::from(if little_endian { raw.swap_bytes() } else { raw });
        if address.is_unspecified() {
            reject(issues);
            continue;
        }
        let metric = field(6).parse().ok();
        if metric.is_none() {
            reject(issues);
        }
        let interface_name = known.contains(interface).then(|| interface.to_owned());
        if interface_name.is_none() {
            issues.push(format!("IPv4 default route interface {interface} is unknown."));
        }
        let gateway = NetworkGatewayInfo { address: address.to_string(), interface_name, metric };
        routes.push((gateway, interface.to_owned()));
    }
    if malformed && routes.is_empty() {
        return None;
    }
    Some(sorted_gateways(routes))
}

/// Global default gateways from `/proc/net/ipv6_route`; `None` when every
/// candidate line was malformed.
fn ipv6_default_routes(
    text: &str,
    known: &BTreeSet<&str>,
    issues: &mut Issues,
) -> Option<Vec<NetworkGatewayInfo>> {
    let zero = "0".repeat(32);
    let mut routes = Vec::new();
    let mut malformed = false;
    let mut reject = |issues: &mut Issues| {
        issues.push("Malformed IPv6 default route.");
        malformed = true;
    };
    let prefix = |value: &str| u8::from_str_radix(value, 16).ok().filter(|prefix| *prefix <= 128);
    for line in text.lines().filter(|line| !line.trim().is_empty()) {
        let fields: Vec<&str> = line.split_whitespace().collect();
        let [destination, destination_prefix, ..] = fields.as_slice() else {
            reject(issues);
            continue;
        };
        if !is_hex(destination, 32) || !is_hex(destination_prefix, 2) {
            reject(issues);
            continue;
        }
        let Some(destination_prefix) = prefix(destination_prefix) else {
            reject(issues);
            continue;
        };
        if *destination != zero || destination_prefix != 0 {
            continue;
        }
        if fields.len() != 10 {
            reject(issues);
            continue;
        }
        let (source, source_prefix, next_hop) = (fields[2], fields[3], fields[4]);
        let (metric, flags, interface) = (fields[5], fields[8], fields[9]);
        if !is_hex(source, 32) || !is_hex(source_prefix, 2) {
            reject(issues);
            continue;
        }
        let Some(source_prefix) = prefix(source_prefix) else {
            reject(issues);
            continue;
        };
        if source != zero || source_prefix != 0 {
            continue;
        }
        let hex_fields = [(next_hop, 32), (metric, 8), (fields[6], 8), (fields[7], 8), (flags, 8)];
        if !hex_fields.iter().all(|(value, length)| is_hex(value, *length)) {
            reject(issues);
            continue;
        }
        let flags = u32::from_str_radix(flags, 16).unwrap_or_default();
        if flags & RTF_UP_GATEWAY != RTF_UP_GATEWAY || flags & RTF_REJECT != 0 {
            continue;
        }
        if next_hop == zero || !valid_name(interface) {
            reject(issues);
            continue;
        }
        let address = Ipv6Addr::from(u128::from_str_radix(next_hop, 16).unwrap_or_default());
        let interface_name = known.contains(interface).then(|| interface.to_owned());
        if interface_name.is_none() {
            issues.push(format!("IPv6 default route interface {interface} is unknown."));
        }
        let gateway = NetworkGatewayInfo {
            address: address.to_string(),
            interface_name,
            metric: u32::from_str_radix(metric, 16).ok(),
        };
        routes.push((gateway, interface.to_owned()));
    }
    if malformed && routes.is_empty() {
        return None;
    }
    Some(sorted_gateways(routes))
}

/// Unique `nameserver` addresses in file order; `None` when only malformed
/// entries exist.
fn nameservers(text: &str, malformed_issue: &str, issues: &mut Issues) -> Option<Vec<String>> {
    let mut servers: Vec<String> = Vec::new();
    let mut malformed = false;
    for line in text.lines().map(str::trim) {
        if line.is_empty() || line.starts_with(['#', ';']) {
            continue;
        }
        let fields: Vec<&str> = line.split_whitespace().collect();
        if !fields[0].eq_ignore_ascii_case("nameserver") {
            continue;
        }
        match fields.as_slice() {
            [_, server] if server.parse::<IpAddr>().is_ok() => {
                if !servers.iter().any(|known| known == server) {
                    servers.push((*server).to_owned());
                }
            }
            _ => malformed = true,
        }
    }
    if malformed {
        issues.push(malformed_issue);
        if servers.is_empty() {
            return None;
        }
    }
    Some(servers)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::collector::DetectionStatus;
    use crate::testutil::Fixture;

    const ETH: &str = "/sys/devices/pci0000:00/0000:00:1f.6/net/eth0";
    const WLAN: &str = "/sys/devices/pci0000:00/0000:00:14.3/net/wlan0";
    const VETH: &str = "/sys/devices/virtual/net/veth0";
    const LO: &str = "/sys/devices/virtual/net/lo";
    const ROUTES: &str = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\neth0\t00000000\t0102000A\t0003\t0\t0\t100\t00000000\t0\t0\t0\neth0\t0002000A\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n";

    fn interface(fixture: &Fixture, name: &str, device: &str, kind: &str) {
        fixture
            .write(&format!("{device}/type"), kind)
            .write(&format!("{device}/flags"), "0x1003\n")
            .write(&format!("{device}/operstate"), "up\n")
            .write(&format!("{device}/mtu"), "1500\n")
            .link(&format!("/sys/class/net/{name}"), device);
    }

    fn v4(name: &str, address: [u8; 4], mask: [u8; 4]) -> InterfaceAddress {
        InterfaceAddress {
            interface: name.into(),
            family: AddressFamily::Ipv4 { address: address.into(), netmask: Some(mask.into()) },
        }
    }

    fn network_fixture() -> Fixture {
        let fixture = Fixture::new();
        interface(&fixture, "eth0", ETH, "1\n");
        interface(&fixture, "wlan0", WLAN, "1\n");
        interface(&fixture, "veth0", VETH, "1\n");
        interface(&fixture, "lo", LO, "772\n");
        fixture
            .write(&format!("{ETH}/speed"), "1000\n")
            .write(&format!("{ETH}/duplex"), "full\n")
            .dir(&format!("{ETH}/device"))
            .dir(&format!("{WLAN}/device"))
            .dir(&format!("{WLAN}/phy80211"))
            .write(&format!("{WLAN}/operstate"), "dormant\n")
            .write(&format!("{LO}/operstate"), "unknown\n")
            .write("/proc/net/route", ROUTES)
            .write("/proc/net/ipv6_route", "")
            .write("/etc/resolv.conf", "# stub\nnameserver 127.0.0.53\noptions edns0\n")
            .write(
                "/run/systemd/resolve/resolv.conf",
                "nameserver 192.0.2.53\nnameserver 2001:db8::53\n",
            );
        fixture
    }

    #[test]
    fn collects_interfaces_addresses_gateways_and_dns() {
        let fixture = network_fixture();
        let mut host = fixture.host();
        host.interface_addresses = Ok(vec![
            InterfaceAddress {
                interface: "eth0".into(),
                family: AddressFamily::Link("00:11:22:33:44:55".into()),
            },
            v4("eth0", [10, 0, 2, 15], [255, 255, 255, 0]),
            v4("eth0", [10, 0, 2, 9], [255, 255, 255, 0]),
            InterfaceAddress {
                interface: "eth0".into(),
                family: AddressFamily::Ipv6 {
                    address: "fe80::1".parse().unwrap(),
                    netmask: Some("ffff:ffff:ffff:ffff::".parse().unwrap()),
                    scope_id: 2,
                },
            },
            v4("lo", [127, 0, 0, 1], [255, 0, 0, 0]),
        ]);

        let result = detect(&host);

        assert_eq!(result.status, DetectionStatus::Complete, "{:?}", result.issues);
        let network = result.value;
        let names: Vec<&str> = network.interfaces.iter().map(|i| i.name.as_str()).collect();
        assert_eq!(names, ["wlan0", "eth0", "lo", "veth0"]);
        let [wlan, eth, lo, veth] = network.interfaces.as_slice() else { panic!() };
        assert_eq!(eth.interface_type, NetworkInterfaceType::Ethernet);
        assert_eq!(eth.is_up, Some(true));
        assert_eq!(eth.speed_mbps, Some(1000));
        assert_eq!(eth.duplex, Some(NetworkDuplex::Full));
        assert_eq!(eth.mtu, Some(1500));
        assert_eq!(eth.mac_address.as_deref(), Some("00:11:22:33:44:55"));
        let ipv4: Vec<&str> =
            eth.ipv4_addresses.iter().flatten().map(|a| a.address.as_str()).collect();
        assert_eq!(ipv4, ["10.0.2.15", "10.0.2.9"]);
        assert_eq!(eth.ipv4_addresses.as_ref().unwrap()[0].prefix_length, Some(24));
        assert_eq!(
            eth.ipv6_addresses.as_ref().unwrap()[0],
            NetworkAddressInfo {
                address: "fe80::1%eth0".into(),
                prefix_length: Some(64),
                subnet_mask: Some("ffff:ffff:ffff:ffff::".into()),
            }
        );
        assert_eq!(wlan.interface_type, NetworkInterfaceType::Wifi);
        assert_eq!(wlan.is_up, Some(false));
        assert_eq!(wlan.ipv4_addresses, Some(vec![]));
        assert_eq!(lo.interface_type, NetworkInterfaceType::Loopback);
        assert_eq!(lo.is_up, Some(true));
        assert_eq!(veth.interface_type, NetworkInterfaceType::Virtual);
        assert_eq!(
            network.ipv4_default_gateways,
            Some(vec![NetworkGatewayInfo {
                address: "10.0.2.1".into(),
                interface_name: Some("eth0".into()),
                metric: Some(100),
            }])
        );
        assert_eq!(network.ipv6_default_gateways, Some(vec![]));
        assert_eq!(network.configured_dns, Some(vec!["127.0.0.53".into()]));
        assert_eq!(network.upstream_dns, Some(vec!["192.0.2.53".into(), "2001:db8::53".into()]));
    }

    #[test]
    fn classification_needs_evidence() {
        let fixture = Fixture::new();
        let usb = "/sys/devices/pci0000:00/usb1/1-1/net/enx0";
        interface(&fixture, "enx0", usb, "1\n");
        interface(&fixture, "wwan0", "/sys/devices/pci0000:00/x/net/wwan0", "519\n");
        fixture.dir(&format!("{usb}/device"));
        let result = detect(&fixture.host());
        let kinds: Vec<_> =
            result.value.interfaces.iter().map(|i| (i.name.as_str(), i.interface_type)).collect();
        assert_eq!(
            kinds,
            [("enx0", NetworkInterfaceType::Ethernet), ("wwan0", NetworkInterfaceType::Unknown)]
        );
    }

    #[test]
    fn address_masks_merge_and_conflict() {
        let mut issues = Issues::default();
        let mut merged = Merged::default();
        let mask = |prefix: u32| {
            let value = u32::MAX.checked_shl(32 - prefix).unwrap_or(0);
            Some((u128::from(value), 32, Ipv4Addr::from(value).to_string()))
        };
        merged.add("192.0.2.1".into(), None, "eth0", &mut issues);
        merged.add("192.0.2.1".into(), mask(24), "eth0", &mut issues);
        assert_eq!(merged.addresses["192.0.2.1"].prefix_length, Some(24));
        merged.add("192.0.2.1".into(), mask(16), "eth0", &mut issues);
        assert_eq!(merged.addresses["192.0.2.1"], NetworkAddressInfo::bare("192.0.2.1"));
        let broken = Some((0xff00ff00, 32, "255.0.255.0".into()));
        merged.add("192.0.2.7".into(), broken, "eth0", &mut issues);
        assert_eq!(merged.addresses["192.0.2.7"].prefix_length, None);
        assert_eq!(issues.0, ["Malformed network address for eth0."]);
    }

    #[test]
    fn ipv4_routes_decode_byte_order_and_reject_bad_lines() {
        let known = BTreeSet::from(["eth0"]);
        let mut issues = Issues::default();
        let routes = ipv4_default_routes(ROUTES, true, &known, &mut issues).unwrap();
        assert_eq!(routes[0].address, "10.0.2.1");
        let routes = ipv4_default_routes(ROUTES, false, &known, &mut issues).unwrap();
        assert_eq!(routes[0].address, "1.2.0.10");
        assert!(issues.is_empty());

        let header = ROUTES.lines().next().unwrap();
        let unknown_metric = format!(
            "{header}\nwlan0\t00000000\t0102000A\t0003\t0\t0\tx\t00000000\t0\t0\t0\neth0\t00000000\t0101000A\t0003\t0\t0\t50\t00000000\t0\t0\t0\n"
        );
        let routes = ipv4_default_routes(&unknown_metric, true, &known, &mut issues).unwrap();
        let order: Vec<_> = routes.iter().map(|r| (r.address.as_str(), r.metric)).collect();
        assert_eq!(order, [("10.0.1.1", Some(50)), ("10.0.2.1", None)]);
        assert_eq!(routes[1].interface_name, None);
        assert_eq!(
            issues.0,
            ["Malformed IPv4 default route.", "IPv4 default route interface wlan0 is unknown."]
        );

        let mut issues = Issues::default();
        assert_eq!(ipv4_default_routes("", true, &known, &mut issues), None);
        let truncated = format!("{header}\neth0\t00000000\t0102000A\t0003\n");
        assert_eq!(ipv4_default_routes(&truncated, true, &known, &mut issues), None);
    }

    #[test]
    fn ipv6_default_routes_skip_source_specific_and_rejected() {
        let zero = "0".repeat(32);
        let hop = "fe800000000000000000000000000001";
        let line = |source_prefix: &str, flags: &str| {
            format!(
                "{zero} 00 {zero} {source_prefix} {hop} 00000400 00000001 00000000 {flags} eth0\n"
            )
        };
        let known = BTreeSet::from(["eth0"]);
        let mut issues = Issues::default();
        let text = line("00", "00000003") + &line("40", "00000003") + &line("00", "00000203");
        let routes = ipv6_default_routes(&text, &known, &mut issues).unwrap();
        assert_eq!(
            routes,
            [NetworkGatewayInfo {
                address: "fe80::1".into(),
                interface_name: Some("eth0".into()),
                metric: Some(1024),
            }]
        );
        assert!(issues.is_empty());
        let out_of_range = format!("{zero} 81 {zero} 00 {hop} 0 0 0 3 eth0\n");
        assert_eq!(ipv6_default_routes(&out_of_range, &known, &mut issues), None);
        assert_eq!(issues.0, ["Malformed IPv6 default route."]);
    }

    #[test]
    fn nameserver_parsing() {
        let mut issues = Issues::default();
        let text = "nameserver 1.1.1.1\nnameserver bogus\nnameserver 1.1.1.1\nNAMESERVER ::1\n";
        assert_eq!(
            nameservers(text, "Malformed DNS configuration.", &mut issues),
            Some(vec!["1.1.1.1".into(), "::1".into()])
        );
        assert_eq!(issues.0, ["Malformed DNS configuration."]);
        assert_eq!(
            nameservers("nameserver x\n", "Malformed DNS configuration.", &mut issues),
            None
        );
    }

    #[test]
    fn failed_sources_are_unavailable_and_readable_empty_sources_complete() {
        let mut host = Fixture::new().host();
        host.interface_addresses = Err(io::Error::other("denied"));
        assert_eq!(detect(&host).status, DetectionStatus::Unavailable);

        let fixture = Fixture::new();
        fixture
            .dir("/sys/class/net")
            .write("/proc/net/route", ROUTES.lines().next().unwrap())
            .write("/etc/resolv.conf", "");
        let result = detect(&fixture.host());
        assert_eq!(result.status, DetectionStatus::Complete, "{:?}", result.issues);
        assert_eq!(result.value.ipv4_default_gateways, Some(vec![]));
        assert_eq!(result.value.configured_dns, Some(vec![]));
    }
}
