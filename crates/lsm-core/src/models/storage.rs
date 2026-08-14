//! Storage model. Read-and-mount only: no partition editing in this release.

use serde::{Deserialize, Serialize};

/// A physical block device.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Disk {
    /// Kernel name, e.g. `nvme0n1`.
    pub name: String,
    pub path: String,
    pub model: Option<String>,
    pub size_bytes: u64,
    pub removable: bool,
    pub rotational: bool,
    pub transport: Option<String>,
    pub partitions: Vec<Partition>,
    /// SMART health. `None` when unsupported or not yet queried — the
    /// architecture is in place, collection is not implemented in this release.
    pub smart: Option<SmartHealth>,
}

impl Disk {
    pub fn kind_label(&self) -> &'static str {
        if self.removable {
            "Removable"
        } else if self.rotational {
            "Hard disk"
        } else {
            "Solid state"
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Partition {
    pub name: String,
    pub path: String,
    pub size_bytes: u64,
    pub filesystem: Option<String>,
    pub label: Option<String>,
    /// Retained for mount operations; redacted from exports.
    pub uuid: Option<String>,
    pub mount_point: Option<String>,
    pub mount_options: Vec<String>,
    /// Usage, present only when mounted.
    pub usage: Option<FilesystemUsage>,
    pub encrypted: bool,
}

impl Partition {
    pub fn is_mounted(&self) -> bool {
        self.mount_point.is_some()
    }

    /// Whether unmounting would be unsafe because the system depends on it.
    pub fn is_system_critical(&self) -> bool {
        matches!(
            self.mount_point.as_deref(),
            Some("/") | Some("/boot") | Some("/usr") | Some("/var") | Some("/boot/efi")
        )
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct FilesystemUsage {
    pub total_bytes: u64,
    pub used_bytes: u64,
    pub available_bytes: u64,
}

impl FilesystemUsage {
    pub fn fraction_used(&self) -> f64 {
        if self.total_bytes == 0 {
            return 0.0;
        }
        self.used_bytes as f64 / self.total_bytes as f64
    }

    /// Nearly-full filesystems break updates and boots; worth flagging.
    pub fn is_nearly_full(&self) -> bool {
        self.fraction_used() >= 0.90
    }
}

/// SMART summary. The type exists so the storage page and snapshot format do
/// not change shape when collection lands.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SmartHealth {
    pub passed: bool,
    pub temperature_c: Option<i32>,
    pub power_on_hours: Option<u64>,
    /// SSD/NVMe wear indicator, 0–100 where 100 is fully worn.
    pub percentage_used: Option<u8>,
    pub reallocated_sectors: Option<u64>,
}

/// A mounted filesystem as listed in `/proc/self/mountinfo`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MountPoint {
    pub source: String,
    pub target: String,
    pub filesystem: String,
    pub options: Vec<String>,
    pub usage: Option<FilesystemUsage>,
}

impl MountPoint {
    pub fn is_read_only(&self) -> bool {
        self.options.iter().any(|o| o == "ro")
    }

    /// Pseudo-filesystems are not interesting on a storage page.
    pub fn is_pseudo(&self) -> bool {
        const PSEUDO: &[&str] = &[
            "proc",
            "sysfs",
            "devtmpfs",
            "devpts",
            "tmpfs",
            "cgroup",
            "cgroup2",
            "securityfs",
            "pstore",
            "efivarfs",
            "bpf",
            "tracefs",
            "debugfs",
            "configfs",
            "fusectl",
            "hugetlbfs",
            "mqueue",
            "ramfs",
            "autofs",
            "binfmt_misc",
            "nsfs",
            "squashfs",
        ];
        PSEUDO.contains(&self.filesystem.as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn usage_fraction_flags_a_nearly_full_filesystem() {
        let u = FilesystemUsage {
            total_bytes: 100,
            used_bytes: 95,
            available_bytes: 5,
        };
        assert!(u.is_nearly_full());
        assert!((u.fraction_used() - 0.95).abs() < 1e-9);

        let ok = FilesystemUsage {
            total_bytes: 100,
            used_bytes: 50,
            available_bytes: 50,
        };
        assert!(!ok.is_nearly_full());

        // An empty/unknown filesystem must not divide by zero.
        let empty = FilesystemUsage {
            total_bytes: 0,
            used_bytes: 0,
            available_bytes: 0,
        };
        assert_eq!(empty.fraction_used(), 0.0);
    }

    #[test]
    fn system_critical_mounts_are_protected_from_unmounting() {
        let mk = |mp: Option<&str>| Partition {
            name: "nvme0n1p2".into(),
            path: "/dev/nvme0n1p2".into(),
            size_bytes: 0,
            filesystem: Some("btrfs".into()),
            label: None,
            uuid: None,
            mount_point: mp.map(String::from),
            mount_options: vec![],
            usage: None,
            encrypted: false,
        };
        assert!(mk(Some("/")).is_system_critical());
        assert!(mk(Some("/boot")).is_system_critical());
        assert!(!mk(Some("/mnt/data")).is_system_critical());
        assert!(!mk(None).is_mounted());
        assert!(mk(Some("/mnt/data")).is_mounted());
    }

    #[test]
    fn pseudo_filesystems_are_filtered_out_of_the_storage_view() {
        let mk = |fs: &str| MountPoint {
            source: "x".into(),
            target: "/x".into(),
            filesystem: fs.into(),
            options: vec!["rw".into()],
            usage: None,
        };
        assert!(mk("proc").is_pseudo());
        assert!(mk("cgroup2").is_pseudo());
        assert!(mk("tmpfs").is_pseudo());
        assert!(!mk("btrfs").is_pseudo());
        assert!(!mk("ext4").is_pseudo());
    }

    #[test]
    fn read_only_mounts_are_detected() {
        let m = MountPoint {
            source: "/dev/sda1".into(),
            target: "/".into(),
            filesystem: "ext4".into(),
            options: vec!["ro".into(), "relatime".into()],
            usage: None,
        };
        assert!(m.is_read_only());
    }

    #[test]
    fn disk_kind_label_distinguishes_ssd_hdd_and_removable() {
        let mk = |removable, rotational| Disk {
            name: "sda".into(),
            path: "/dev/sda".into(),
            model: None,
            size_bytes: 0,
            removable,
            rotational,
            transport: None,
            partitions: vec![],
            smart: None,
        };
        assert_eq!(mk(false, false).kind_label(), "Solid state");
        assert_eq!(mk(false, true).kind_label(), "Hard disk");
        assert_eq!(mk(true, false).kind_label(), "Removable");
    }
}
