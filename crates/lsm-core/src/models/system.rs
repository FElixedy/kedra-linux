//! Overview / system identity model.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Which distribution family a system belongs to. Determines which backends are
/// wired up; adding Fedora or Debian means adding a variant and a backend, not
/// touching the UI.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DistroFamily {
    Arch,
    Fedora,
    Debian,
    Unknown,
}

impl DistroFamily {
    pub fn label(&self) -> &'static str {
        match self {
            DistroFamily::Arch => "Arch-based",
            DistroFamily::Fedora => "Fedora-based",
            DistroFamily::Debian => "Debian-based",
            DistroFamily::Unknown => "Unknown",
        }
    }

    /// Whether this release ships a package backend for the family.
    pub fn is_supported(&self) -> bool {
        matches!(self, DistroFamily::Arch)
    }
}

/// Parsed `/etc/os-release` plus the family we inferred.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DistroInfo {
    /// `ID`, e.g. `cachyos`.
    pub id: String,
    /// `NAME`, e.g. `CachyOS Linux`.
    pub name: String,
    /// `PRETTY_NAME`, e.g. `CachyOS`.
    pub pretty_name: String,
    /// `VERSION_ID` or `BUILD_ID`; rolling releases often only have the latter.
    pub version: Option<String>,
    /// `ID_LIKE` entries.
    pub id_like: Vec<String>,
    pub family: DistroFamily,
    pub logo: Option<String>,
    pub home_url: Option<String>,
}

/// The graphical session, when there is one.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionType {
    Wayland,
    X11,
    Tty,
    Unknown,
}

impl SessionType {
    pub fn label(&self) -> &'static str {
        match self {
            SessionType::Wayland => "Wayland",
            SessionType::X11 => "X11",
            SessionType::Tty => "Console",
            SessionType::Unknown => "Unknown",
        }
    }
}

/// Everything the Overview header needs, gathered in one pass.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SystemIdentity {
    pub distro: DistroInfo,
    pub hostname: String,
    pub kernel_release: String,
    pub desktop_environment: Option<String>,
    pub session_type: SessionType,
    pub cpu_model: String,
    pub cpu_cores: usize,
    pub cpu_threads: usize,
    pub architecture: String,
    pub boot_id: Option<String>,
    pub booted_at: Option<DateTime<Utc>>,
}

/// Fast-changing metrics, refreshed on a timer rather than gathered once.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct SystemVitals {
    pub uptime_seconds: u64,
    /// 0.0–1.0 across all cores.
    pub cpu_usage: f32,
    /// Highest reported package/core temperature, in degrees Celsius.
    pub cpu_temperature_c: Option<f32>,
    pub memory_total: u64,
    pub memory_available: u64,
    pub swap_total: u64,
    pub swap_free: u64,
    pub load_average: [f32; 3],
}

impl SystemVitals {
    pub fn memory_used(&self) -> u64 {
        self.memory_total.saturating_sub(self.memory_available)
    }

    pub fn memory_usage_fraction(&self) -> f64 {
        if self.memory_total == 0 {
            return 0.0;
        }
        self.memory_used() as f64 / self.memory_total as f64
    }

    pub fn swap_used(&self) -> u64 {
        self.swap_total.saturating_sub(self.swap_free)
    }

    pub fn swap_usage_fraction(&self) -> f64 {
        if self.swap_total == 0 {
            return 0.0;
        }
        self.swap_used() as f64 / self.swap_total as f64
    }
}

/// Snapshot of counters shown on the Overview dashboard.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SystemHealth {
    pub available_updates: usize,
    pub failed_services: Vec<String>,
    pub last_update: Option<DateTime<Utc>>,
    pub reboot_required: RebootRequirement,
    pub pending_pacnew: usize,
    pub dkms_failures: usize,
}

/// Whether a reboot is needed, and why — never a bare boolean, because the
/// reason is what makes the prompt actionable.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum RebootRequirement {
    NotRequired,
    Required { reasons: Vec<RebootReason> },
}

impl RebootRequirement {
    pub fn is_required(&self) -> bool {
        matches!(self, RebootRequirement::Required { .. })
    }

    pub fn reasons(&self) -> &[RebootReason] {
        match self {
            RebootRequirement::NotRequired => &[],
            RebootRequirement::Required { reasons } => reasons,
        }
    }

    /// One-line explanation for the Overview card.
    pub fn summary(&self) -> Option<String> {
        let reasons = self.reasons();
        match reasons.len() {
            0 => None,
            1 => Some(reasons[0].describe()),
            n => Some(format!(
                "{} and {} other change{}",
                reasons[0].describe(),
                n - 1,
                if n == 2 { "" } else { "s" }
            )),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum RebootReason {
    /// The kernel on disk differs from the one running.
    KernelUpdated { running: String, installed: String },
    /// A core userspace component that cannot be restarted in place changed.
    CoreLibraryUpdated { package: String },
    /// systemd itself was updated.
    SystemdUpdated,
    /// A graphics driver changed under a running session.
    GraphicsDriverUpdated { package: String },
}

impl RebootReason {
    pub fn describe(&self) -> String {
        match self {
            RebootReason::KernelUpdated { running, installed } => {
                format!("The kernel was updated ({running} → {installed})")
            }
            RebootReason::CoreLibraryUpdated { package } => {
                format!("{package} was updated")
            }
            RebootReason::SystemdUpdated => "systemd was updated".into(),
            RebootReason::GraphicsDriverUpdated { package } => {
                format!("The graphics driver {package} was updated")
            }
        }
    }
}

/// Packages whose update means the running system no longer matches what is on
/// disk. Kept short and specific; this is not a generic "core package" list.
pub const REBOOT_TRIGGER_PACKAGES: &[&str] = &["systemd", "glibc", "dbus", "linux-firmware"];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn memory_and_swap_fractions_handle_a_swapless_system() {
        let v = SystemVitals {
            uptime_seconds: 100,
            cpu_usage: 0.1,
            cpu_temperature_c: None,
            memory_total: 16_000_000_000,
            memory_available: 8_000_000_000,
            swap_total: 0,
            swap_free: 0,
            load_average: [0.0; 3],
        };
        assert_eq!(v.memory_used(), 8_000_000_000);
        assert!((v.memory_usage_fraction() - 0.5).abs() < f64::EPSILON);
        // No swap must not divide by zero.
        assert_eq!(v.swap_usage_fraction(), 0.0);
        assert_eq!(v.swap_used(), 0);
    }

    #[test]
    fn reboot_requirement_explains_itself() {
        let r = RebootRequirement::Required {
            reasons: vec![RebootReason::KernelUpdated {
                running: "6.17.9-cachyos".into(),
                installed: "6.18.0-cachyos".into(),
            }],
        };
        assert!(r.is_required());
        let s = r.summary().unwrap();
        assert!(s.contains("6.17.9-cachyos"));
        assert!(s.contains("6.18.0-cachyos"));

        assert!(!RebootRequirement::NotRequired.is_required());
        assert_eq!(RebootRequirement::NotRequired.summary(), None);
    }

    #[test]
    fn multiple_reboot_reasons_are_summarised_not_listed() {
        let r = RebootRequirement::Required {
            reasons: vec![
                RebootReason::SystemdUpdated,
                RebootReason::CoreLibraryUpdated {
                    package: "glibc".into(),
                },
                RebootReason::GraphicsDriverUpdated {
                    package: "mesa".into(),
                },
            ],
        };
        assert_eq!(
            r.summary().unwrap(),
            "systemd was updated and 2 other changes"
        );
    }

    #[test]
    fn only_arch_is_wired_up_in_this_release() {
        assert!(DistroFamily::Arch.is_supported());
        assert!(!DistroFamily::Fedora.is_supported());
        assert!(!DistroFamily::Unknown.is_supported());
    }
}
