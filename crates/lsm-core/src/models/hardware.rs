//! Hardware and driver model.

use serde::{Deserialize, Serialize};

/// A PCI or USB device.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Device {
    pub bus: DeviceBus,
    /// Bus address, e.g. `0000:03:00.0` or `001:004`.
    pub address: String,
    pub class: String,
    pub vendor: String,
    pub model: String,
    pub vendor_id: String,
    pub device_id: String,
    /// Kernel module currently bound to the device.
    pub driver: Option<String>,
    /// Modules the kernel says could drive it.
    pub available_modules: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeviceBus {
    Pci,
    Usb,
}

/// A graphics adapter with its full userspace stack, which is what actually
/// matters when graphics break.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GpuInfo {
    pub vendor: GpuVendor,
    pub model: String,
    pub pci_address: Option<String>,
    /// Bound kernel driver, e.g. `amdgpu`, `nvidia`, `i915`.
    pub kernel_driver: Option<String>,
    /// Mesa version, when the OpenGL implementation is Mesa.
    pub mesa_version: Option<String>,
    /// Vulkan driver name, e.g. `RADV`, `ANV`, `NVIDIA`.
    pub vulkan_driver: Option<String>,
    pub vulkan_api_version: Option<String>,
    /// Proprietary driver version, for NVIDIA.
    pub proprietary_driver_version: Option<String>,
    /// Packages providing this GPU's stack, used for change correlation.
    pub stack_packages: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GpuVendor {
    Amd,
    Nvidia,
    Intel,
    Other,
}

impl GpuVendor {
    pub fn label(&self) -> &'static str {
        match self {
            GpuVendor::Amd => "AMD",
            GpuVendor::Nvidia => "NVIDIA",
            GpuVendor::Intel => "Intel",
            GpuVendor::Other => "Other",
        }
    }

    /// Identify from a PCI vendor id.
    pub fn from_vendor_id(id: &str) -> Self {
        match id.trim_start_matches("0x").to_ascii_lowercase().as_str() {
            "1002" | "1022" => GpuVendor::Amd,
            "10de" => GpuVendor::Nvidia,
            "8086" => GpuVendor::Intel,
            _ => GpuVendor::Other,
        }
    }
}

/// CPU description for the hardware page.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CpuInfo {
    pub model: String,
    pub vendor: String,
    pub physical_cores: usize,
    pub logical_cores: usize,
    /// Current per-core frequencies in MHz.
    pub frequencies_mhz: Vec<f64>,
    pub max_frequency_mhz: Option<f64>,
    pub flags: Vec<String>,
    /// Active CPU frequency governor, when readable.
    pub governor: Option<String>,
    /// Microcode revision, which changes with `amd-ucode`/`intel-ucode` updates.
    pub microcode: Option<String>,
}

/// A memory module as reported by the firmware.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MemoryModule {
    pub locator: String,
    pub size_bytes: u64,
    pub speed_mts: Option<u32>,
    pub kind: Option<String>,
    pub manufacturer: Option<String>,
}

/// A network interface.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NetworkInterface {
    pub name: String,
    pub kind: NetworkInterfaceKind,
    /// Operational state from `/sys/class/net/<n>/operstate`.
    pub state: String,
    /// Link speed in Mbit/s, when the link is up and reports one.
    pub speed_mbps: Option<u32>,
    pub driver: Option<String>,
    pub ipv4_addresses: Vec<String>,
    pub ipv6_addresses: Vec<String>,
    pub gateway: Option<String>,
    pub dns_servers: Vec<String>,
    /// Retained for display only; redacted from every export.
    pub mac_address: Option<String>,
    pub mtu: Option<u32>,
}

impl NetworkInterface {
    pub fn is_up(&self) -> bool {
        self.state == "up"
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NetworkInterfaceKind {
    Ethernet,
    Wireless,
    Loopback,
    Bridge,
    Virtual,
    Tunnel,
    Other,
}

impl NetworkInterfaceKind {
    pub fn label(&self) -> &'static str {
        match self {
            NetworkInterfaceKind::Ethernet => "Ethernet",
            NetworkInterfaceKind::Wireless => "Wi-Fi",
            NetworkInterfaceKind::Loopback => "Loopback",
            NetworkInterfaceKind::Bridge => "Bridge",
            NetworkInterfaceKind::Virtual => "Virtual",
            NetworkInterfaceKind::Tunnel => "Tunnel",
            NetworkInterfaceKind::Other => "Other",
        }
    }
}

/// A socket in LISTEN state.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ListeningPort {
    pub port: u16,
    pub protocol: TransportProtocol,
    /// Bind address; `0.0.0.0`/`::` mean "reachable from the network".
    pub local_address: String,
    pub process_name: Option<String>,
    pub pid: Option<u32>,
    pub user: Option<String>,
}

impl ListeningPort {
    /// Whether the socket is reachable from outside the machine — the only
    /// distinction most users actually need.
    pub fn is_externally_reachable(&self) -> bool {
        matches!(self.local_address.as_str(), "0.0.0.0" | "::" | "*")
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TransportProtocol {
    Tcp,
    Udp,
}

/// Battery state, when the machine has one.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BatteryInfo {
    pub name: String,
    /// 0.0–1.0.
    pub charge: f32,
    pub state: String,
    /// Ratio of full capacity to design capacity; health below ~0.8 is notable.
    pub capacity_health: Option<f32>,
    pub power_draw_watts: Option<f32>,
    pub technology: Option<String>,
}

/// Firewall state. Read-only in this release.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FirewallStatus {
    pub provider: FirewallProvider,
    pub active: bool,
    /// Default policy for incoming traffic, when it can be determined.
    pub default_incoming_policy: Option<String>,
    /// Human-readable allowed services/ports.
    pub allowed: Vec<String>,
    /// Set when a provider is present but its state needs privileges to read.
    pub requires_privilege_to_inspect: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FirewallProvider {
    Firewalld,
    Ufw,
    Nftables,
    Iptables,
    None,
}

impl FirewallProvider {
    pub fn label(&self) -> &'static str {
        match self {
            FirewallProvider::Firewalld => "firewalld",
            FirewallProvider::Ufw => "ufw",
            FirewallProvider::Nftables => "nftables",
            FirewallProvider::Iptables => "iptables",
            FirewallProvider::None => "None detected",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gpu_vendors_are_identified_from_pci_ids() {
        assert_eq!(GpuVendor::from_vendor_id("1002"), GpuVendor::Amd);
        assert_eq!(GpuVendor::from_vendor_id("0x10de"), GpuVendor::Nvidia);
        assert_eq!(GpuVendor::from_vendor_id("8086"), GpuVendor::Intel);
        assert_eq!(GpuVendor::from_vendor_id("1234"), GpuVendor::Other);
    }

    #[test]
    fn wildcard_bind_addresses_are_reported_as_externally_reachable() {
        let mk = |addr: &str| ListeningPort {
            port: 8080,
            protocol: TransportProtocol::Tcp,
            local_address: addr.into(),
            process_name: None,
            pid: None,
            user: None,
        };
        assert!(mk("0.0.0.0").is_externally_reachable());
        assert!(mk("::").is_externally_reachable());
        // Loopback-only services are not exposed and should not alarm anyone.
        assert!(!mk("127.0.0.1").is_externally_reachable());
        assert!(!mk("::1").is_externally_reachable());
    }

    #[test]
    fn interface_up_state_is_read_from_operstate() {
        let mk = |state: &str| NetworkInterface {
            name: "wlan0".into(),
            kind: NetworkInterfaceKind::Wireless,
            state: state.into(),
            speed_mbps: None,
            driver: None,
            ipv4_addresses: vec![],
            ipv6_addresses: vec![],
            gateway: None,
            dns_servers: vec![],
            mac_address: None,
            mtu: None,
        };
        assert!(mk("up").is_up());
        assert!(!mk("down").is_up());
        assert!(!mk("unknown").is_up());
    }
}
