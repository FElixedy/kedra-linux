//! Package domain model, shared by every backend (Arch today; Fedora/Debian later).

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

pub use super::change::{PackageAction, PackageOrigin};
use super::version::Version;

/// Why a package is on the system. Drives the "explicit vs dependency"
/// distinction and orphan detection.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InstallReason {
    /// The user asked for it.
    Explicit,
    /// Pulled in to satisfy another package.
    Dependency,
}

impl InstallReason {
    pub fn label(&self) -> &'static str {
        match self {
            InstallReason::Explicit => "Explicitly installed",
            InstallReason::Dependency => "Installed as a dependency",
        }
    }
}

/// An installed package.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct InstalledPackage {
    pub name: String,
    pub version: Version,
    pub description: String,
    pub origin: PackageOrigin,
    /// Repository name for repo packages (`core`, `extra`, `cachyos`…).
    pub repository: Option<String>,
    /// Installed size on disk, in bytes.
    pub installed_size: u64,
    pub install_reason: InstallReason,
    pub install_date: Option<DateTime<Utc>>,
    pub build_date: Option<DateTime<Utc>>,
    pub licenses: Vec<String>,
    pub url: Option<String>,
    pub packager: Option<String>,
    pub groups: Vec<String>,
}

impl InstalledPackage {
    pub fn is_explicit(&self) -> bool {
        self.install_reason == InstallReason::Explicit
    }
}

/// Full detail for the package detail pane, fetched on demand.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PackageDetails {
    pub package: InstalledPackage,
    pub depends: Vec<String>,
    pub optional_depends: Vec<OptionalDependency>,
    pub required_by: Vec<String>,
    pub optional_for: Vec<String>,
    pub provides: Vec<String>,
    pub conflicts: Vec<String>,
    pub replaces: Vec<String>,
    /// Config files owned by the package that pacman tracks as backup entries.
    pub backup_files: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OptionalDependency {
    pub name: String,
    pub reason: Option<String>,
    pub installed: bool,
}

/// A package in a remote repository (search result / install candidate).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RemotePackage {
    pub name: String,
    pub version: Version,
    pub description: String,
    pub origin: PackageOrigin,
    pub repository: Option<String>,
    /// Compressed download size, when the backend reports it.
    pub download_size: Option<u64>,
    pub installed_size: Option<u64>,
    /// The version currently installed, if any.
    pub installed_version: Option<Version>,
    /// AUR popularity, used only to order search results.
    pub popularity: Option<f64>,
    pub out_of_date: bool,
}

impl RemotePackage {
    pub fn is_installed(&self) -> bool {
        self.installed_version.is_some()
    }
}

/// An available update.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PackageUpdate {
    pub name: String,
    pub current_version: Version,
    pub new_version: Version,
    pub origin: PackageOrigin,
    pub repository: Option<String>,
    pub download_size: Option<u64>,
    /// Change in installed size; negative when the update frees space.
    pub size_delta: Option<i64>,
}

impl PackageUpdate {
    /// True when this update replaces the running kernel's package family.
    pub fn is_kernel(&self) -> bool {
        super::kernel::is_kernel_package(&self.name)
    }
}

/// Filter shown above the update list.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum UpdateFilter {
    All,
    Official,
    Aur,
    Flatpak,
}

impl UpdateFilter {
    pub const ALL: &'static [UpdateFilter] = &[
        UpdateFilter::All,
        UpdateFilter::Official,
        UpdateFilter::Aur,
        UpdateFilter::Flatpak,
    ];

    pub fn label(&self) -> &'static str {
        match self {
            UpdateFilter::All => "All",
            UpdateFilter::Official => "Official",
            UpdateFilter::Aur => "AUR",
            UpdateFilter::Flatpak => "Flatpak",
        }
    }

    pub fn matches(&self, origin: PackageOrigin) -> bool {
        match self {
            UpdateFilter::All => true,
            UpdateFilter::Official => origin == PackageOrigin::Repository,
            UpdateFilter::Aur => origin == PackageOrigin::Aur,
            UpdateFilter::Flatpak => origin == PackageOrigin::Flatpak,
        }
    }
}

/// A previewed transaction: exactly what will happen if the user confirms.
///
/// Nothing is executed until the user has seen one of these.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct TransactionPreview {
    pub to_install: Vec<TransactionItem>,
    pub to_upgrade: Vec<TransactionItem>,
    pub to_downgrade: Vec<TransactionItem>,
    pub to_remove: Vec<TransactionItem>,
    pub to_reinstall: Vec<TransactionItem>,
    /// Total bytes to download.
    pub download_size: u64,
    /// Net change in disk usage; may be negative.
    pub installed_size_delta: i64,
    /// Human-readable warnings surfaced by the backend (replacements, conflicts…).
    pub warnings: Vec<String>,
    /// True when the transaction touches the running kernel or its modules.
    pub affects_kernel: bool,
}

impl TransactionPreview {
    pub fn is_empty(&self) -> bool {
        self.to_install.is_empty()
            && self.to_upgrade.is_empty()
            && self.to_downgrade.is_empty()
            && self.to_remove.is_empty()
            && self.to_reinstall.is_empty()
    }

    pub fn total_packages(&self) -> usize {
        self.to_install.len()
            + self.to_upgrade.len()
            + self.to_downgrade.len()
            + self.to_remove.len()
            + self.to_reinstall.len()
    }

    /// Every package the transaction touches, with the action applied to it.
    pub fn all_items(&self) -> Vec<(PackageAction, &TransactionItem)> {
        let mut v = Vec::with_capacity(self.total_packages());
        v.extend(
            self.to_install
                .iter()
                .map(|i| (PackageAction::Installed, i)),
        );
        v.extend(self.to_upgrade.iter().map(|i| (PackageAction::Upgraded, i)));
        v.extend(
            self.to_downgrade
                .iter()
                .map(|i| (PackageAction::Downgraded, i)),
        );
        v.extend(self.to_remove.iter().map(|i| (PackageAction::Removed, i)));
        v.extend(
            self.to_reinstall
                .iter()
                .map(|i| (PackageAction::Reinstalled, i)),
        );
        v
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TransactionItem {
    pub name: String,
    pub old_version: Option<Version>,
    pub new_version: Option<Version>,
    pub origin: PackageOrigin,
    pub repository: Option<String>,
    pub download_size: Option<u64>,
    /// True when the package is only in the transaction to satisfy a dependency.
    pub is_dependency: bool,
}

/// Which AUR helper is available, if any. The app must work without one.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum AurHelper {
    Paru,
    Yay,
}

impl AurHelper {
    pub fn program(&self) -> &'static str {
        match self {
            AurHelper::Paru => "paru",
            AurHelper::Yay => "yay",
        }
    }

    pub fn label(&self) -> &'static str {
        match self {
            AurHelper::Paru => "paru",
            AurHelper::Yay => "yay",
        }
    }
}

/// Cleanup targets, kept explicit so nothing is ever deleted generically.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CleanupTarget {
    pub id: CleanupKind,
    pub title: String,
    pub description: String,
    /// Bytes that would be recovered.
    pub reclaimable_bytes: u64,
    /// Number of items (packages, files, runtimes…).
    pub item_count: usize,
    /// Individual items, so the user can see exactly what goes.
    pub items: Vec<String>,
    /// Selected by default only when unambiguously safe.
    pub safe_by_default: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CleanupKind {
    OrphanPackages,
    /// Cached versions of packages that are no longer installed.
    UninstalledPackageCache,
    /// Older cached versions of still-installed packages.
    OldPackageCache,
    UnusedFlatpakRuntimes,
    JournalOverflow,
}

impl CleanupKind {
    pub fn label(&self) -> &'static str {
        match self {
            CleanupKind::OrphanPackages => "Orphan packages",
            CleanupKind::UninstalledPackageCache => "Cache for removed packages",
            CleanupKind::OldPackageCache => "Superseded package versions",
            CleanupKind::UnusedFlatpakRuntimes => "Unused Flatpak runtimes",
            CleanupKind::JournalOverflow => "Journal beyond its size limit",
        }
    }

    pub fn slug(&self) -> &'static str {
        match self {
            CleanupKind::OrphanPackages => "orphan_packages",
            CleanupKind::UninstalledPackageCache => "uninstalled_package_cache",
            CleanupKind::OldPackageCache => "old_package_cache",
            CleanupKind::UnusedFlatpakRuntimes => "unused_flatpak_runtimes",
            CleanupKind::JournalOverflow => "journal_overflow",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn item(name: &str) -> TransactionItem {
        TransactionItem {
            name: name.into(),
            old_version: Some(Version::new("1.0-1")),
            new_version: Some(Version::new("1.1-1")),
            origin: PackageOrigin::Repository,
            repository: Some("extra".into()),
            download_size: Some(1024),
            is_dependency: false,
        }
    }

    #[test]
    fn empty_preview_is_recognised_so_we_never_run_a_no_op_transaction() {
        let p = TransactionPreview::default();
        assert!(p.is_empty());
        assert_eq!(p.total_packages(), 0);
    }

    #[test]
    fn preview_counts_every_action_bucket() {
        let p = TransactionPreview {
            to_install: vec![item("a")],
            to_upgrade: vec![item("b"), item("c")],
            to_remove: vec![item("d")],
            ..Default::default()
        };
        assert!(!p.is_empty());
        assert_eq!(p.total_packages(), 4);
        let actions: Vec<_> = p.all_items().into_iter().map(|(a, _)| a).collect();
        assert!(actions.contains(&PackageAction::Installed));
        assert!(actions.contains(&PackageAction::Upgraded));
        assert!(actions.contains(&PackageAction::Removed));
    }

    #[test]
    fn update_filters_partition_by_origin() {
        assert!(UpdateFilter::All.matches(PackageOrigin::Aur));
        assert!(UpdateFilter::Official.matches(PackageOrigin::Repository));
        assert!(!UpdateFilter::Official.matches(PackageOrigin::Aur));
        assert!(UpdateFilter::Flatpak.matches(PackageOrigin::Flatpak));
        assert!(!UpdateFilter::Aur.matches(PackageOrigin::Flatpak));
    }

    #[test]
    fn kernel_updates_are_flagged_for_extra_care() {
        let u = PackageUpdate {
            name: "linux-cachyos".into(),
            current_version: Version::new("6.17.9-1"),
            new_version: Version::new("6.18.0-1"),
            origin: PackageOrigin::Repository,
            repository: Some("cachyos".into()),
            download_size: None,
            size_delta: None,
        };
        assert!(u.is_kernel());

        let f = PackageUpdate {
            name: "firefox".into(),
            ..u.clone()
        };
        assert!(!f.is_kernel());
    }
}
