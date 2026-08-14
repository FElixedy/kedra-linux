//! DKMS model.
//!
//! DKMS failures deserve first-class treatment: a module that fails to build
//! for a new kernel produces *no error at all* until you reboot into that
//! kernel and the hardware silently stops working. Surfacing the failure at
//! build time, and again in What Changed?, is the whole point.

use serde::{Deserialize, Serialize};

pub use super::change::DkmsStatus;

/// One module/version/kernel triple as reported by `dkms status`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DkmsEntry {
    pub module: String,
    pub module_version: String,
    pub kernel_release: String,
    pub arch: Option<String>,
    pub status: DkmsStatus,
}

impl DkmsEntry {
    pub fn is_built(&self) -> bool {
        matches!(self.status, DkmsStatus::Installed | DkmsStatus::Built)
    }

    pub fn is_failure(&self) -> bool {
        self.status == DkmsStatus::BuildFailed
    }
}

/// A module grouped across all kernels, which is how the UI presents it:
///
/// ```text
/// v4l2loopback
///   Kernel 6.17 ✓
///   Kernel 6.18 ✕
/// ```
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DkmsModuleGroup {
    pub module: String,
    pub module_version: String,
    /// One entry per kernel this module is registered for.
    pub kernels: Vec<DkmsKernelStatus>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DkmsKernelStatus {
    pub kernel_release: String,
    pub status: DkmsStatus,
    /// True when this is the running kernel — a failure here is active breakage,
    /// not a future problem.
    pub is_running_kernel: bool,
}

impl DkmsModuleGroup {
    pub fn has_failure(&self) -> bool {
        self.kernels
            .iter()
            .any(|k| k.status == DkmsStatus::BuildFailed)
    }

    /// True when the module is missing for the kernel that is running right now.
    pub fn broken_on_running_kernel(&self) -> bool {
        self.kernels
            .iter()
            .any(|k| k.is_running_kernel && k.status != DkmsStatus::Installed)
    }

    /// Kernels the module is registered for but not successfully installed on.
    pub fn missing_kernels(&self) -> Vec<&str> {
        self.kernels
            .iter()
            .filter(|k| k.status != DkmsStatus::Installed)
            .map(|k| k.kernel_release.as_str())
            .collect()
    }
}

/// Group flat entries by module, marking the running kernel.
pub fn group_entries(entries: &[DkmsEntry], running_kernel: &str) -> Vec<DkmsModuleGroup> {
    let mut groups: Vec<DkmsModuleGroup> = Vec::new();
    for e in entries {
        let status = DkmsKernelStatus {
            kernel_release: e.kernel_release.clone(),
            status: e.status,
            is_running_kernel: e.kernel_release == running_kernel,
        };
        match groups.iter_mut().find(|g| g.module == e.module) {
            Some(g) => {
                // A module can be registered at several versions; show the newest.
                if e.module_version > g.module_version {
                    g.module_version = e.module_version.clone();
                }
                g.kernels.push(status);
            }
            None => groups.push(DkmsModuleGroup {
                module: e.module.clone(),
                module_version: e.module_version.clone(),
                kernels: vec![status],
            }),
        }
    }
    for g in &mut groups {
        g.kernels
            .sort_by(|a, b| a.kernel_release.cmp(&b.kernel_release));
    }
    // Surface failures first — that is what the user needs to see.
    groups.sort_by(|a, b| {
        b.has_failure()
            .cmp(&a.has_failure())
            .then_with(|| a.module.cmp(&b.module))
    });
    groups
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(module: &str, kernel: &str, status: DkmsStatus) -> DkmsEntry {
        DkmsEntry {
            module: module.into(),
            module_version: "1.0".into(),
            kernel_release: kernel.into(),
            arch: Some("x86_64".into()),
            status,
        }
    }

    #[test]
    fn groups_a_module_across_its_kernels() {
        let entries = vec![
            entry("v4l2loopback", "6.17.0", DkmsStatus::Installed),
            entry("v4l2loopback", "6.18.0", DkmsStatus::BuildFailed),
            entry("nvidia", "6.17.0", DkmsStatus::Installed),
            entry("nvidia", "6.18.0", DkmsStatus::Installed),
        ];
        let groups = group_entries(&entries, "6.18.0");
        assert_eq!(groups.len(), 2);
        // Failures sort to the top.
        assert_eq!(groups[0].module, "v4l2loopback");
        assert!(groups[0].has_failure());
        assert!(!groups[1].has_failure());
        assert_eq!(groups[0].missing_kernels(), vec!["6.18.0"]);
    }

    #[test]
    fn a_failure_on_the_running_kernel_is_distinguished_from_a_future_one() {
        let entries = vec![
            entry("v4l2loopback", "6.17.0", DkmsStatus::Installed),
            entry("v4l2loopback", "6.18.0", DkmsStatus::BuildFailed),
        ];

        // Still on the old kernel: broken, but not yet biting.
        let before_reboot = group_entries(&entries, "6.17.0");
        assert!(before_reboot[0].has_failure());
        assert!(!before_reboot[0].broken_on_running_kernel());

        // After rebooting into 6.18 the module is actually missing.
        let after_reboot = group_entries(&entries, "6.18.0");
        assert!(after_reboot[0].broken_on_running_kernel());
    }

    #[test]
    fn keeps_the_newest_module_version_when_several_are_registered() {
        let entries = vec![
            DkmsEntry {
                module_version: "1.0".into(),
                ..entry("m", "6.17.0", DkmsStatus::Installed)
            },
            DkmsEntry {
                module_version: "1.2".into(),
                ..entry("m", "6.18.0", DkmsStatus::Installed)
            },
        ];
        let groups = group_entries(&entries, "6.18.0");
        assert_eq!(groups[0].module_version, "1.2");
        assert_eq!(groups[0].kernels.len(), 2);
    }

    #[test]
    fn entry_classification_matches_the_ui_tick_and_cross() {
        assert!(entry("m", "k", DkmsStatus::Installed).is_built());
        assert!(entry("m", "k", DkmsStatus::Built).is_built());
        assert!(!entry("m", "k", DkmsStatus::BuildFailed).is_built());
        assert!(entry("m", "k", DkmsStatus::BuildFailed).is_failure());
    }
}
