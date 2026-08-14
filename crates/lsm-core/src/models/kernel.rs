//! Kernel domain model.
//!
//! Kernel package names are *not* an enumerable list — Arch has `linux`,
//! `linux-lts`, `linux-zen`, `linux-hardened`; CachyOS alone ships a dozen
//! `linux-cachyos-*` variants, and users build their own. Detection is therefore
//! pattern-based and validated against what actually provides a kernel image.

use serde::{Deserialize, Serialize};

use super::version::Version;

/// A kernel package installed on the system.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct InstalledKernel {
    /// Package name, e.g. `linux-cachyos`.
    pub package: String,
    /// Package version, e.g. `6.18.0-1`.
    pub version: Version,
    /// `uname -r` style release for this kernel's modules directory, when
    /// resolvable (`/usr/lib/modules/<release>`).
    pub release: Option<String>,
    /// True when this is the kernel the machine is currently running.
    pub running: bool,
    /// Whether a matching `-headers` package is installed. Without headers,
    /// DKMS modules cannot build for this kernel — a common, confusing failure.
    pub headers_installed: bool,
}

/// A kernel package available to install.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AvailableKernel {
    pub package: String,
    pub version: Version,
    pub repository: Option<String>,
    pub description: String,
    pub installed: bool,
}

/// The kernel currently executing.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunningKernel {
    /// Full `uname -r`, e.g. `6.18.0-1-cachyos`.
    pub release: String,
    /// `uname -v` build string.
    pub version_string: String,
    /// Package that owns this kernel's modules, when it can be attributed.
    pub package: Option<String>,
    /// Kernel command line from `/proc/cmdline`, already redacted of anything
    /// that looks like a secret before it reaches a report.
    pub cmdline: String,
}

impl RunningKernel {
    /// The numeric part, e.g. `6.18.0` from `6.18.0-1-cachyos`. Used to compare
    /// kernels across flavours.
    pub fn base_version(&self) -> &str {
        let end = self
            .release
            .find(|c: char| c != '.' && !c.is_ascii_digit())
            .unwrap_or(self.release.len());
        self.release[..end].trim_end_matches('.')
    }

    /// The flavour suffix, e.g. `cachyos`, or `""` for a stock kernel.
    pub fn flavour(&self) -> &str {
        match self.release.split_once('-') {
            Some((_, rest)) => rest.rsplit('-').next().unwrap_or(""),
            None => "",
        }
    }
}

/// Prefixes that mark a package as providing a kernel.
const KERNEL_PREFIXES: &[&str] = &["linux"];

/// Suffixes that mark a *companion* package rather than the kernel itself.
const KERNEL_COMPANION_SUFFIXES: &[&str] = &[
    "-headers",
    "-docs",
    "-dkms",
    "-firmware",
    "-api-headers",
    "-tools",
    "-tools-libs",
];

/// Whether a package name looks like a kernel image package.
///
/// Deliberately pattern-based: `linux`, `linux-lts`, `linux-cachyos-bore`,
/// `linux-rt-lts` all match, while `linux-firmware`, `linux-headers` and
/// `linux-api-headers` do not.
pub fn is_kernel_package(name: &str) -> bool {
    let n = name.to_ascii_lowercase();
    if !KERNEL_PREFIXES
        .iter()
        .any(|p| n == *p || n.starts_with(&format!("{p}-")))
    {
        return false;
    }
    if KERNEL_COMPANION_SUFFIXES.iter().any(|s| n.ends_with(s)) {
        return false;
    }
    // `linux-firmware-*` variants are firmware, not kernels.
    if n.starts_with("linux-firmware") {
        return false;
    }
    true
}

/// Whether a package is the `-headers` companion of a kernel.
pub fn is_kernel_headers_package(name: &str) -> bool {
    let n = name.to_ascii_lowercase();
    n.starts_with("linux") && n.ends_with("-headers") && n != "linux-api-headers"
}

/// Given a kernel package name, the name of its headers package.
pub fn headers_package_for(kernel_package: &str) -> String {
    format!("{kernel_package}-headers")
}

/// Whether a package provides firmware blobs (relevant to driver correlation).
pub fn is_firmware_package(name: &str) -> bool {
    let n = name.to_ascii_lowercase();
    n.starts_with("linux-firmware")
        || n.ends_with("-firmware")
        || n.starts_with("sof-firmware")
        || n.starts_with("amd-ucode")
        || n.starts_with("intel-ucode")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recognises_kernels_across_distributions_and_flavours() {
        for pkg in [
            "linux",
            "linux-lts",
            "linux-zen",
            "linux-hardened",
            "linux-rt",
            "linux-rt-lts",
            "linux-cachyos",
            "linux-cachyos-bore",
            "linux-cachyos-lts",
            "linux-surface",
        ] {
            assert!(
                is_kernel_package(pkg),
                "{pkg} should be detected as a kernel"
            );
        }
    }

    #[test]
    fn companion_packages_are_not_mistaken_for_kernels() {
        for pkg in [
            "linux-firmware",
            "linux-firmware-qlogic",
            "linux-headers",
            "linux-lts-headers",
            "linux-cachyos-headers",
            "linux-api-headers",
            "linux-tools",
            "linux-docs",
        ] {
            assert!(
                !is_kernel_package(pkg),
                "{pkg} must not be treated as a kernel"
            );
        }
        // ...and unrelated packages certainly are not.
        assert!(!is_kernel_package("mesa"));
        assert!(
            !is_kernel_package("linuxlogo"),
            "prefix must be followed by a separator"
        );
    }

    #[test]
    fn headers_detection_excludes_the_userspace_api_headers() {
        assert!(is_kernel_headers_package("linux-headers"));
        assert!(is_kernel_headers_package("linux-cachyos-headers"));
        assert!(!is_kernel_headers_package("linux-api-headers"));
        assert_eq!(
            headers_package_for("linux-cachyos"),
            "linux-cachyos-headers"
        );
    }

    #[test]
    fn firmware_packages_are_identified_for_driver_correlation() {
        assert!(is_firmware_package("linux-firmware"));
        assert!(is_firmware_package("sof-firmware"));
        assert!(is_firmware_package("amd-ucode"));
        assert!(is_firmware_package("ast-firmware"));
        assert!(!is_firmware_package("mesa"));
    }

    #[test]
    fn running_kernel_splits_base_version_from_flavour() {
        let k = RunningKernel {
            release: "6.18.0-1-cachyos".into(),
            version_string: String::new(),
            package: None,
            cmdline: String::new(),
        };
        assert_eq!(k.base_version(), "6.18.0");
        assert_eq!(k.flavour(), "cachyos");

        // The real running kernel on the development machine.
        let k2 = RunningKernel {
            release: "7.1.8-1-cachyos".into(),
            ..k.clone()
        };
        assert_eq!(k2.base_version(), "7.1.8");

        let stock = RunningKernel {
            release: "6.18.0-arch1-1".into(),
            ..k.clone()
        };
        assert_eq!(stock.base_version(), "6.18.0");
    }
}
