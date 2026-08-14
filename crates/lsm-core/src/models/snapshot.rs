//! System metadata snapshots and the diff between two of them.
//!
//! These are *metadata* snapshots — a few hundred kilobytes of package
//! versions, unit states, config hashes and driver facts. They are not
//! filesystem snapshots and never copy user data. Their whole purpose is to let
//! a user say "compare now against the checkpoint from when things worked".

use std::collections::BTreeMap;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use super::config::ConfigFileState;
use super::dkms::DkmsEntry;
use super::service::{ActiveState, EnablementState};
use super::version::{Version, VersionDelta};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct SnapshotId(pub i64);

impl std::fmt::Display for SnapshotId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// Why a snapshot was taken. Automatic checkpoints bracket every transaction so
/// that "what did that update actually do" is always answerable.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SnapshotKind {
    /// Taken automatically before an operation.
    PreOperation,
    /// Taken automatically after an operation completed.
    PostOperation,
    /// Created by the user with a name they chose.
    Manual,
    /// Taken on first run / at the start of a boot, to have a baseline at all.
    Baseline,
}

impl SnapshotKind {
    pub fn label(&self) -> &'static str {
        match self {
            SnapshotKind::PreOperation => "Before operation",
            SnapshotKind::PostOperation => "After operation",
            SnapshotKind::Manual => "Checkpoint",
            SnapshotKind::Baseline => "Baseline",
        }
    }

    pub fn slug(&self) -> &'static str {
        match self {
            SnapshotKind::PreOperation => "pre_operation",
            SnapshotKind::PostOperation => "post_operation",
            SnapshotKind::Manual => "manual",
            SnapshotKind::Baseline => "baseline",
        }
    }

    pub fn from_slug(s: &str) -> Option<Self> {
        Some(match s {
            "pre_operation" => SnapshotKind::PreOperation,
            "post_operation" => SnapshotKind::PostOperation,
            "manual" => SnapshotKind::Manual,
            "baseline" => SnapshotKind::Baseline,
            _ => return None,
        })
    }

    /// Manual checkpoints are what users compare against; they are never
    /// pruned automatically.
    pub fn is_prunable(&self) -> bool {
        !matches!(self, SnapshotKind::Manual)
    }
}

/// Snapshot header, cheap to list.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SnapshotMeta {
    pub id: SnapshotId,
    pub taken_at: DateTime<Utc>,
    pub kind: SnapshotKind,
    /// User-chosen name for manual checkpoints, otherwise a generated summary.
    pub label: String,
    pub note: Option<String>,
    pub operation_id: Option<String>,
    pub boot_id: Option<String>,
    pub kernel_release: Option<String>,
    /// Counts, so the list can show size without loading the payload.
    pub package_count: usize,
    pub service_count: usize,
    pub config_count: usize,
}

/// The full captured state.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SystemSnapshot {
    pub meta: SnapshotMeta,
    /// package name → version.
    pub packages: BTreeMap<String, Version>,
    /// unit name → recorded state.
    pub services: BTreeMap<String, ServiceSnapshotState>,
    pub dkms: Vec<DkmsEntry>,
    /// path → tracked file state.
    pub config: BTreeMap<String, ConfigFileState>,
    /// Free-form driver/system facts (Mesa version, GPU driver, session type…).
    pub facts: BTreeMap<String, String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ServiceSnapshotState {
    pub enablement: EnablementState,
    pub active_state: ActiveState,
}

/// The difference between two snapshots — only meaningful differences.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct SnapshotDiff {
    pub packages_added: Vec<(String, Version)>,
    pub packages_removed: Vec<(String, Version)>,
    pub packages_changed: Vec<PackageVersionDiff>,
    pub services_changed: Vec<ServiceStateDiff>,
    pub config_changed: Vec<ConfigDiffEntry>,
    pub dkms_changed: Vec<DkmsDiffEntry>,
    pub facts_changed: Vec<FactDiff>,
}

impl SnapshotDiff {
    pub fn is_empty(&self) -> bool {
        self.packages_added.is_empty()
            && self.packages_removed.is_empty()
            && self.packages_changed.is_empty()
            && self.services_changed.is_empty()
            && self.config_changed.is_empty()
            && self.dkms_changed.is_empty()
            && self.facts_changed.is_empty()
    }

    pub fn total_differences(&self) -> usize {
        self.packages_added.len()
            + self.packages_removed.len()
            + self.packages_changed.len()
            + self.services_changed.len()
            + self.config_changed.len()
            + self.dkms_changed.len()
            + self.facts_changed.len()
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PackageVersionDiff {
    pub name: String,
    pub from: Version,
    pub to: Version,
    pub delta: VersionDelta,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ServiceStateDiff {
    pub unit: String,
    pub from_enablement: EnablementState,
    pub to_enablement: EnablementState,
    pub from_active: ActiveState,
    pub to_active: ActiveState,
}

impl ServiceStateDiff {
    /// Whether the *persistent* configuration changed, as opposed to the unit
    /// merely being running or not right now.
    pub fn enablement_changed(&self) -> bool {
        self.from_enablement != self.to_enablement
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ConfigDiffEntry {
    pub path: String,
    /// `None` on the "from" side means the file did not exist then.
    pub from_hash: Option<String>,
    pub to_hash: Option<String>,
    pub existed_before: bool,
    pub exists_now: bool,
}

impl ConfigDiffEntry {
    pub fn summary(&self) -> &'static str {
        match (self.existed_before, self.exists_now) {
            (false, true) => "Not present → Created",
            (true, false) => "Present → Removed",
            _ => "Modified",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DkmsDiffEntry {
    pub module: String,
    pub kernel_release: String,
    pub from: Option<super::dkms::DkmsStatus>,
    pub to: Option<super::dkms::DkmsStatus>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FactDiff {
    pub key: String,
    pub from: Option<String>,
    pub to: Option<String>,
}

/// Compute the meaningful difference between two snapshots.
///
/// Unchanged entries never appear; that is the difference between a diff and a
/// dump, and it is what makes compare mode usable on a 2000-package system.
pub fn diff_snapshots(from: &SystemSnapshot, to: &SystemSnapshot) -> SnapshotDiff {
    let mut d = SnapshotDiff::default();

    for (name, new_ver) in &to.packages {
        match from.packages.get(name) {
            None => d.packages_added.push((name.clone(), new_ver.clone())),
            Some(old_ver) => {
                let delta = VersionDelta::between(old_ver, new_ver);
                if delta != VersionDelta::Unchanged {
                    d.packages_changed.push(PackageVersionDiff {
                        name: name.clone(),
                        from: old_ver.clone(),
                        to: new_ver.clone(),
                        delta,
                    });
                }
            }
        }
    }
    for (name, old_ver) in &from.packages {
        if !to.packages.contains_key(name) {
            d.packages_removed.push((name.clone(), old_ver.clone()));
        }
    }

    for (unit, new_state) in &to.services {
        if let Some(old_state) = from.services.get(unit) {
            if old_state != new_state {
                d.services_changed.push(ServiceStateDiff {
                    unit: unit.clone(),
                    from_enablement: old_state.enablement,
                    to_enablement: new_state.enablement,
                    from_active: old_state.active_state,
                    to_active: new_state.active_state,
                });
            }
        }
    }

    for (path, new_state) in &to.config {
        match from.config.get(path) {
            None => d.config_changed.push(ConfigDiffEntry {
                path: path.clone(),
                from_hash: None,
                to_hash: new_state.hash.clone(),
                existed_before: false,
                exists_now: true,
            }),
            Some(old_state) => {
                if ConfigFileState::diff_kind(old_state, new_state).is_some() {
                    d.config_changed.push(ConfigDiffEntry {
                        path: path.clone(),
                        from_hash: old_state.hash.clone(),
                        to_hash: new_state.hash.clone(),
                        existed_before: true,
                        exists_now: true,
                    });
                }
            }
        }
    }
    for (path, old_state) in &from.config {
        if !to.config.contains_key(path) {
            d.config_changed.push(ConfigDiffEntry {
                path: path.clone(),
                from_hash: old_state.hash.clone(),
                to_hash: None,
                existed_before: true,
                exists_now: false,
            });
        }
    }

    let dkms_key = |e: &DkmsEntry| (e.module.clone(), e.kernel_release.clone());
    let from_dkms: BTreeMap<_, _> = from.dkms.iter().map(|e| (dkms_key(e), e.status)).collect();
    let to_dkms: BTreeMap<_, _> = to.dkms.iter().map(|e| (dkms_key(e), e.status)).collect();
    for (key, to_status) in &to_dkms {
        let from_status = from_dkms.get(key).copied();
        if from_status != Some(*to_status) {
            d.dkms_changed.push(DkmsDiffEntry {
                module: key.0.clone(),
                kernel_release: key.1.clone(),
                from: from_status,
                to: Some(*to_status),
            });
        }
    }
    for (key, from_status) in &from_dkms {
        if !to_dkms.contains_key(key) {
            d.dkms_changed.push(DkmsDiffEntry {
                module: key.0.clone(),
                kernel_release: key.1.clone(),
                from: Some(*from_status),
                to: None,
            });
        }
    }

    let mut fact_keys: Vec<&String> = from.facts.keys().chain(to.facts.keys()).collect();
    fact_keys.sort();
    fact_keys.dedup();
    for key in fact_keys {
        let a = from.facts.get(key);
        let b = to.facts.get(key);
        if a != b {
            d.facts_changed.push(FactDiff {
                key: key.clone(),
                from: a.cloned(),
                to: b.cloned(),
            });
        }
    }

    d.packages_added.sort();
    d.packages_removed.sort();
    d.packages_changed.sort_by(|a, b| a.name.cmp(&b.name));
    d.services_changed.sort_by(|a, b| a.unit.cmp(&b.unit));
    d.config_changed.sort_by(|a, b| a.path.cmp(&b.path));
    d.dkms_changed.sort_by(|a, b| a.module.cmp(&b.module));
    d
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::dkms::DkmsStatus;

    fn snap() -> SystemSnapshot {
        SystemSnapshot {
            meta: SnapshotMeta {
                id: SnapshotId(1),
                taken_at: Utc::now(),
                kind: SnapshotKind::Manual,
                label: "test".into(),
                note: None,
                operation_id: None,
                boot_id: None,
                kernel_release: None,
                package_count: 0,
                service_count: 0,
                config_count: 0,
            },
            packages: BTreeMap::new(),
            services: BTreeMap::new(),
            dkms: Vec::new(),
            config: BTreeMap::new(),
            facts: BTreeMap::new(),
        }
    }

    fn cfg(path: &str, hash: &str) -> ConfigFileState {
        ConfigFileState {
            path: path.into(),
            hash: Some(hash.into()),
            kind: super::super::config::FileKind::Regular,
            size: 1,
            modified: None,
            mode: 0o644,
            uid: 0,
            gid: 0,
            symlink_target: None,
        }
    }

    #[test]
    fn identical_snapshots_produce_an_empty_diff() {
        let mut a = snap();
        a.packages.insert("mesa".into(), Version::new("26.1.3-1"));
        a.config
            .insert("/etc/fstab".into(), cfg("/etc/fstab", "h1"));
        let b = a.clone();
        let d = diff_snapshots(&a, &b);
        assert!(d.is_empty(), "unchanged state must not appear in a diff");
        assert_eq!(d.total_differences(), 0);
    }

    #[test]
    fn reproduces_the_compare_mode_example_from_the_spec() {
        let mut from = snap();
        from.packages
            .insert("linux-cachyos".into(), Version::new("6.17.9-1"));
        from.packages
            .insert("mesa".into(), Version::new("26.1.3-1"));
        from.packages
            .insert("bluez-utils".into(), Version::new("5.79-1"));
        from.services.insert(
            "bluetooth.service".into(),
            ServiceSnapshotState {
                enablement: EnablementState::Enabled,
                active_state: ActiveState::Active,
            },
        );

        let mut to = snap();
        to.packages
            .insert("linux-cachyos".into(), Version::new("6.18.0-1"));
        to.packages.insert("mesa".into(), Version::new("26.2.0-1"));
        to.packages
            .insert("v4l2loopback-dkms".into(), Version::new("0.13.2-1"));
        to.services.insert(
            "bluetooth.service".into(),
            ServiceSnapshotState {
                enablement: EnablementState::Disabled,
                active_state: ActiveState::Inactive,
            },
        );
        to.config.insert(
            "/etc/modprobe.d/bluetooth.conf".into(),
            cfg("/etc/modprobe.d/bluetooth.conf", "h9"),
        );

        let d = diff_snapshots(&from, &to);

        assert_eq!(d.packages_changed.len(), 2);
        assert_eq!(d.packages_changed[0].name, "linux-cachyos");
        assert_eq!(d.packages_changed[0].delta, VersionDelta::Upgrade);
        assert_eq!(
            d.packages_added,
            vec![("v4l2loopback-dkms".into(), Version::new("0.13.2-1"))]
        );
        assert_eq!(
            d.packages_removed,
            vec![("bluez-utils".into(), Version::new("5.79-1"))]
        );

        assert_eq!(d.services_changed.len(), 1);
        assert!(d.services_changed[0].enablement_changed());
        assert_eq!(
            d.services_changed[0].from_enablement,
            EnablementState::Enabled
        );
        assert_eq!(
            d.services_changed[0].to_enablement,
            EnablementState::Disabled
        );

        assert_eq!(d.config_changed.len(), 1);
        assert_eq!(d.config_changed[0].summary(), "Not present → Created");
    }

    #[test]
    fn a_downgrade_is_labelled_as_such_not_as_a_generic_change() {
        let mut from = snap();
        from.packages
            .insert("mesa".into(), Version::new("26.2.0-1"));
        let mut to = snap();
        to.packages.insert("mesa".into(), Version::new("26.1.3-1"));
        let d = diff_snapshots(&from, &to);
        assert_eq!(d.packages_changed[0].delta, VersionDelta::Downgrade);
    }

    #[test]
    fn dkms_regressions_are_visible_in_a_diff() {
        let mut from = snap();
        from.dkms.push(DkmsEntry {
            module: "v4l2loopback".into(),
            module_version: "0.13.2".into(),
            kernel_release: "6.18.0".into(),
            arch: None,
            status: DkmsStatus::Installed,
        });
        let mut to = snap();
        to.dkms.push(DkmsEntry {
            status: DkmsStatus::BuildFailed,
            ..from.dkms[0].clone()
        });

        let d = diff_snapshots(&from, &to);
        assert_eq!(d.dkms_changed.len(), 1);
        assert_eq!(d.dkms_changed[0].from, Some(DkmsStatus::Installed));
        assert_eq!(d.dkms_changed[0].to, Some(DkmsStatus::BuildFailed));
    }

    #[test]
    fn driver_facts_are_diffed_so_mesa_and_session_changes_show_up() {
        let mut from = snap();
        from.facts.insert("mesa_version".into(), "26.1.3".into());
        from.facts.insert("session_type".into(), "wayland".into());
        let mut to = snap();
        to.facts.insert("mesa_version".into(), "26.2.0".into());
        to.facts.insert("session_type".into(), "wayland".into());

        let d = diff_snapshots(&from, &to);
        assert_eq!(
            d.facts_changed.len(),
            1,
            "only the changed fact should appear"
        );
        assert_eq!(d.facts_changed[0].key, "mesa_version");
        assert_eq!(d.facts_changed[0].to.as_deref(), Some("26.2.0"));
    }

    #[test]
    fn manual_checkpoints_are_never_pruned_automatically() {
        assert!(!SnapshotKind::Manual.is_prunable());
        assert!(SnapshotKind::PreOperation.is_prunable());
        assert!(SnapshotKind::PostOperation.is_prunable());
    }
}
