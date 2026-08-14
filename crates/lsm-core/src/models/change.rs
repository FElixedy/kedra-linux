//! The normalized change event — the single currency of What Changed?.
//!
//! Every subsystem (pacman, systemd, DKMS, the config tracker, the operation
//! engine…) emits `ChangeEvent`s into one store. Nothing in this crate keeps a
//! parallel private history, which is what makes "system management and
//! troubleshooting share the same history" true rather than aspirational.
//!
//! Two properties matter for correctness:
//!
//! * **Dedup key** — the same real-world change is often observable from several
//!   sources (a kernel upgrade appears in pacman.log, in our own operation
//!   output, and in the journal). Events carry a deterministic
//!   [`ChangeEvent::dedup_key`] so the store can merge them while keeping every
//!   source as provenance.
//! * **Relevance** — a troubleshooting signal, not a danger score. See
//!   [`ChangeRelevance`].

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use super::entity::EntityRef;
use super::subsystem::SubsystemTag;
use super::version::{Version, VersionDelta};

/// Database identity of a stored event.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct ChangeEventId(pub i64);

impl std::fmt::Display for ChangeEventId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// Where the knowledge of a change came from.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ChangeSource {
    Pacman,
    Aur,
    Flatpak,
    Systemd,
    Journal,
    Kernel,
    Dkms,
    ConfigTracker,
    BootAnalyzer,
    OperationEngine,
    ManualCheckpoint,
}

impl ChangeSource {
    pub fn slug(&self) -> &'static str {
        match self {
            ChangeSource::Pacman => "pacman",
            ChangeSource::Aur => "aur",
            ChangeSource::Flatpak => "flatpak",
            ChangeSource::Systemd => "systemd",
            ChangeSource::Journal => "journal",
            ChangeSource::Kernel => "kernel",
            ChangeSource::Dkms => "dkms",
            ChangeSource::ConfigTracker => "config_tracker",
            ChangeSource::BootAnalyzer => "boot_analyzer",
            ChangeSource::OperationEngine => "operation_engine",
            ChangeSource::ManualCheckpoint => "manual_checkpoint",
        }
    }

    pub fn from_slug(s: &str) -> Option<Self> {
        Some(match s {
            "pacman" => ChangeSource::Pacman,
            "aur" => ChangeSource::Aur,
            "flatpak" => ChangeSource::Flatpak,
            "systemd" => ChangeSource::Systemd,
            "journal" => ChangeSource::Journal,
            "kernel" => ChangeSource::Kernel,
            "dkms" => ChangeSource::Dkms,
            "config_tracker" => ChangeSource::ConfigTracker,
            "boot_analyzer" => ChangeSource::BootAnalyzer,
            "operation_engine" => ChangeSource::OperationEngine,
            "manual_checkpoint" => ChangeSource::ManualCheckpoint,
            _ => return None,
        })
    }

    pub fn label(&self) -> &'static str {
        match self {
            ChangeSource::Pacman => "pacman",
            ChangeSource::Aur => "AUR",
            ChangeSource::Flatpak => "Flatpak",
            ChangeSource::Systemd => "systemd",
            ChangeSource::Journal => "journal",
            ChangeSource::Kernel => "kernel",
            ChangeSource::Dkms => "DKMS",
            ChangeSource::ConfigTracker => "configuration",
            ChangeSource::BootAnalyzer => "boot analysis",
            ChangeSource::OperationEngine => "this application",
            ChangeSource::ManualCheckpoint => "checkpoint",
        }
    }

    /// How much to trust this source when merging duplicates. Our own operation
    /// records are richest (we know intent and outcome), package-manager logs
    /// next, the journal last.
    pub fn provenance_rank(&self) -> u8 {
        match self {
            ChangeSource::OperationEngine => 100,
            ChangeSource::Pacman | ChangeSource::Aur | ChangeSource::Flatpak => 80,
            ChangeSource::Dkms | ChangeSource::ConfigTracker => 70,
            ChangeSource::Systemd | ChangeSource::BootAnalyzer | ChangeSource::Kernel => 60,
            ChangeSource::ManualCheckpoint => 50,
            ChangeSource::Journal => 30,
        }
    }
}

/// Broad classification, kept deliberately extensible.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ChangeCategory {
    Package,
    Kernel,
    Graphics,
    Networking,
    Audio,
    Bluetooth,
    Firmware,
    Boot,
    Service,
    Configuration,
    Storage,
    Security,
    Application,
    System,
    Other,
}

impl ChangeCategory {
    pub const ALL: &'static [ChangeCategory] = &[
        ChangeCategory::Package,
        ChangeCategory::Kernel,
        ChangeCategory::Graphics,
        ChangeCategory::Networking,
        ChangeCategory::Audio,
        ChangeCategory::Bluetooth,
        ChangeCategory::Firmware,
        ChangeCategory::Boot,
        ChangeCategory::Service,
        ChangeCategory::Configuration,
        ChangeCategory::Storage,
        ChangeCategory::Security,
        ChangeCategory::Application,
        ChangeCategory::System,
        ChangeCategory::Other,
    ];

    pub fn slug(&self) -> &'static str {
        match self {
            ChangeCategory::Package => "package",
            ChangeCategory::Kernel => "kernel",
            ChangeCategory::Graphics => "graphics",
            ChangeCategory::Networking => "networking",
            ChangeCategory::Audio => "audio",
            ChangeCategory::Bluetooth => "bluetooth",
            ChangeCategory::Firmware => "firmware",
            ChangeCategory::Boot => "boot",
            ChangeCategory::Service => "service",
            ChangeCategory::Configuration => "configuration",
            ChangeCategory::Storage => "storage",
            ChangeCategory::Security => "security",
            ChangeCategory::Application => "application",
            ChangeCategory::System => "system",
            ChangeCategory::Other => "other",
        }
    }

    pub fn from_slug(s: &str) -> Option<Self> {
        Self::ALL.iter().copied().find(|c| c.slug() == s)
    }

    pub fn label(&self) -> &'static str {
        match self {
            ChangeCategory::Package => "Packages",
            ChangeCategory::Kernel => "Kernel",
            ChangeCategory::Graphics => "Graphics",
            ChangeCategory::Networking => "Networking",
            ChangeCategory::Audio => "Audio",
            ChangeCategory::Bluetooth => "Bluetooth",
            ChangeCategory::Firmware => "Firmware",
            ChangeCategory::Boot => "Boot",
            ChangeCategory::Service => "Services",
            ChangeCategory::Configuration => "Configuration",
            ChangeCategory::Storage => "Storage",
            ChangeCategory::Security => "Security",
            ChangeCategory::Application => "Applications",
            ChangeCategory::System => "System",
            ChangeCategory::Other => "Other",
        }
    }

    pub fn icon_name(&self) -> &'static str {
        match self {
            ChangeCategory::Package => "package-x-generic-symbolic",
            ChangeCategory::Kernel => "application-x-firmware-symbolic",
            ChangeCategory::Graphics => "video-display-symbolic",
            ChangeCategory::Networking => "network-wired-symbolic",
            ChangeCategory::Audio => "audio-speakers-symbolic",
            ChangeCategory::Bluetooth => "bluetooth-symbolic",
            ChangeCategory::Firmware => "application-x-firmware-symbolic",
            ChangeCategory::Boot => "system-reboot-symbolic",
            ChangeCategory::Service => "system-run-symbolic",
            ChangeCategory::Configuration => "text-x-generic-symbolic",
            ChangeCategory::Storage => "drive-harddisk-symbolic",
            ChangeCategory::Security => "security-high-symbolic",
            ChangeCategory::Application => "application-x-executable-symbolic",
            ChangeCategory::System => "computer-symbolic",
            ChangeCategory::Other => "dialog-information-symbolic",
        }
    }
}

/// Troubleshooting relevance.
///
/// This is explicitly *not* a danger score and not a number. It answers one
/// question: how much attention does this deserve from someone whose system
/// just broke? A Firefox update is `Info` even though it is a big download; a
/// failed DKMS build is `Failure` even though nothing has visibly broken yet.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ChangeRelevance {
    Info = 0,
    Notable = 1,
    PotentiallyRelevant = 2,
    Warning = 3,
    Failure = 4,
}

impl ChangeRelevance {
    pub const ALL: &'static [ChangeRelevance] = &[
        ChangeRelevance::Info,
        ChangeRelevance::Notable,
        ChangeRelevance::PotentiallyRelevant,
        ChangeRelevance::Warning,
        ChangeRelevance::Failure,
    ];

    pub fn slug(&self) -> &'static str {
        match self {
            ChangeRelevance::Info => "info",
            ChangeRelevance::Notable => "notable",
            ChangeRelevance::PotentiallyRelevant => "potentially_relevant",
            ChangeRelevance::Warning => "warning",
            ChangeRelevance::Failure => "failure",
        }
    }

    pub fn from_slug(s: &str) -> Option<Self> {
        Self::ALL.iter().copied().find(|r| r.slug() == s)
    }

    pub fn label(&self) -> &'static str {
        match self {
            ChangeRelevance::Info => "Info",
            ChangeRelevance::Notable => "Notable",
            ChangeRelevance::PotentiallyRelevant => "Potentially relevant",
            ChangeRelevance::Warning => "Warning",
            ChangeRelevance::Failure => "Failure",
        }
    }

    /// libadwaita/GTK style class used to colour the badge.
    pub fn css_class(&self) -> &'static str {
        match self {
            ChangeRelevance::Info => "lsm-relevance-info",
            ChangeRelevance::Notable => "lsm-relevance-notable",
            ChangeRelevance::PotentiallyRelevant => "lsm-relevance-potential",
            ChangeRelevance::Warning => "lsm-relevance-warning",
            ChangeRelevance::Failure => "lsm-relevance-failure",
        }
    }
}

/// Typed payload carried by an event.
///
/// Stored as JSON but modelled as an enum so collectors, correlation and export
/// all agree on the shape. Unknown/legacy payloads decode to [`ChangeMetadata::Other`]
/// rather than failing the whole query.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ChangeMetadata {
    Package(PackageChange),
    Kernel(KernelChange),
    Service(ServiceChange),
    Config(ConfigChange),
    Dkms(DkmsChange),
    Boot(BootChange),
    Operation(OperationChange),
    Lifecycle(LifecycleChange),
    Checkpoint(CheckpointChange),
    #[serde(other)]
    Other,
}

impl ChangeMetadata {
    pub fn as_package(&self) -> Option<&PackageChange> {
        match self {
            ChangeMetadata::Package(p) => Some(p),
            _ => None,
        }
    }
}

/// What happened to a package.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PackageAction {
    Installed,
    Removed,
    Upgraded,
    Downgraded,
    Reinstalled,
}

impl PackageAction {
    pub fn label(&self) -> &'static str {
        match self {
            PackageAction::Installed => "Installed",
            PackageAction::Removed => "Removed",
            PackageAction::Upgraded => "Updated",
            PackageAction::Downgraded => "Downgraded",
            PackageAction::Reinstalled => "Reinstalled",
        }
    }

    pub fn slug(&self) -> &'static str {
        match self {
            PackageAction::Installed => "installed",
            PackageAction::Removed => "removed",
            PackageAction::Upgraded => "upgraded",
            PackageAction::Downgraded => "downgraded",
            PackageAction::Reinstalled => "reinstalled",
        }
    }

    /// Derive the action from the versions involved, so a pacman "upgraded"
    /// line whose versions actually go backwards is reported as a downgrade.
    pub fn from_versions(old: Option<&Version>, new: Option<&Version>) -> Self {
        match (old, new) {
            (None, Some(_)) => PackageAction::Installed,
            (Some(_), None) => PackageAction::Removed,
            (Some(o), Some(n)) => match VersionDelta::between(o, n) {
                VersionDelta::Upgrade => PackageAction::Upgraded,
                VersionDelta::Downgrade => PackageAction::Downgraded,
                VersionDelta::Rebuild => PackageAction::Reinstalled,
                VersionDelta::Unchanged => PackageAction::Reinstalled,
            },
            (None, None) => PackageAction::Reinstalled,
        }
    }
}

/// Which package ecosystem a change came from.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PackageOrigin {
    Repository,
    Aur,
    Flatpak,
    Unknown,
}

impl PackageOrigin {
    pub fn label(&self) -> &'static str {
        match self {
            PackageOrigin::Repository => "Official",
            PackageOrigin::Aur => "AUR",
            PackageOrigin::Flatpak => "Flatpak",
            PackageOrigin::Unknown => "Unknown",
        }
    }

    pub fn slug(&self) -> &'static str {
        match self {
            PackageOrigin::Repository => "repository",
            PackageOrigin::Aur => "aur",
            PackageOrigin::Flatpak => "flatpak",
            PackageOrigin::Unknown => "unknown",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PackageChange {
    pub name: String,
    pub action: PackageAction,
    pub old_version: Option<Version>,
    pub new_version: Option<Version>,
    pub origin: PackageOrigin,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub repository: Option<String>,
}

impl PackageChange {
    /// `6.17.9-1 → 6.18.0-1`, or just the version for install/remove.
    pub fn version_summary(&self) -> String {
        match (&self.old_version, &self.new_version) {
            (Some(o), Some(n)) => format!("{o} → {n}"),
            (None, Some(n)) => n.to_string(),
            (Some(o), None) => o.to_string(),
            (None, None) => String::new(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct KernelChange {
    /// Package providing the kernel, e.g. `linux-cachyos`.
    pub package: String,
    pub old_version: Option<Version>,
    pub new_version: Option<Version>,
    /// Release string as `uname -r` would report it, when known.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub release: Option<String>,
    pub action: PackageAction,
}

/// A meaningful systemd change. Routine start/stop during boot is deliberately
/// *not* represented here; see `ServiceChangeKind` variants.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ServiceChangeKind {
    Enabled,
    Disabled,
    Masked,
    Unmasked,
    EnteredFailed,
    RecoveredFromFailed,
    OverrideAdded,
    OverrideRemoved,
    RepeatedRestarts,
    UnitAppeared,
    UnitDisappeared,
    /// Explicit start/stop/restart the user performed *through this app*.
    ManualRuntimeAction,
}

impl ServiceChangeKind {
    /// Persistent configuration changes survive a reboot; runtime ones do not.
    /// What Changed? separates them so a restart does not read like a
    /// configuration edit.
    pub fn is_persistent(&self) -> bool {
        matches!(
            self,
            ServiceChangeKind::Enabled
                | ServiceChangeKind::Disabled
                | ServiceChangeKind::Masked
                | ServiceChangeKind::Unmasked
                | ServiceChangeKind::OverrideAdded
                | ServiceChangeKind::OverrideRemoved
                | ServiceChangeKind::UnitAppeared
                | ServiceChangeKind::UnitDisappeared
        )
    }

    pub fn label(&self) -> &'static str {
        match self {
            ServiceChangeKind::Enabled => "Enabled",
            ServiceChangeKind::Disabled => "Disabled",
            ServiceChangeKind::Masked => "Masked",
            ServiceChangeKind::Unmasked => "Unmasked",
            ServiceChangeKind::EnteredFailed => "Entered failed state",
            ServiceChangeKind::RecoveredFromFailed => "Recovered from failed state",
            ServiceChangeKind::OverrideAdded => "Override added",
            ServiceChangeKind::OverrideRemoved => "Override removed",
            ServiceChangeKind::RepeatedRestarts => "Restarted repeatedly",
            ServiceChangeKind::UnitAppeared => "Unit appeared",
            ServiceChangeKind::UnitDisappeared => "Unit removed",
            ServiceChangeKind::ManualRuntimeAction => "Changed from this app",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ServiceChange {
    pub unit: String,
    pub kind: ServiceChangeKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub previous_state: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub new_state: Option<String>,
    #[serde(default)]
    pub user_scope: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub restart_count: Option<u32>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ConfigChangeKind {
    Created,
    Removed,
    Modified,
    SymlinkTargetChanged,
    PermissionsChanged,
    /// A `.pacnew` appeared: the package shipped a new default the admin must merge.
    PacnewCreated,
    /// A `.pacsave` appeared: pacman preserved a config it would otherwise delete.
    PacsaveCreated,
}

impl ConfigChangeKind {
    pub fn label(&self) -> &'static str {
        match self {
            ConfigChangeKind::Created => "Created",
            ConfigChangeKind::Removed => "Removed",
            ConfigChangeKind::Modified => "Modified",
            ConfigChangeKind::SymlinkTargetChanged => "Symlink target changed",
            ConfigChangeKind::PermissionsChanged => "Permissions changed",
            ConfigChangeKind::PacnewCreated => "New default shipped (.pacnew)",
            ConfigChangeKind::PacsaveCreated => "Configuration preserved (.pacsave)",
        }
    }

    pub fn needs_admin_attention(&self) -> bool {
        matches!(
            self,
            ConfigChangeKind::PacnewCreated | ConfigChangeKind::PacsaveCreated
        )
    }
}

/// Note: this deliberately carries *hashes and metadata*, never file contents.
/// Diffs are read on demand, with authorization, and only when the user asks.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ConfigChange {
    pub path: String,
    pub kind: ConfigChangeKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub old_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub new_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub old_symlink_target: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub new_symlink_target: Option<String>,
    /// Package that owns the file, when pacman knows.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub owning_package: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DkmsStatus {
    Installed,
    Built,
    Added,
    BuildFailed,
    Removed,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DkmsChange {
    pub module: String,
    pub module_version: String,
    pub kernel_release: String,
    pub status: DkmsStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BootChange {
    pub boot_id: String,
    pub kernel_release: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub previous_boot_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub previous_kernel_release: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub total_us: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub previous_total_us: Option<u64>,
}

impl BootChange {
    pub fn kernel_changed(&self) -> bool {
        self.previous_kernel_release
            .as_deref()
            .is_some_and(|p| p != self.kernel_release)
    }

    /// Signed difference in boot duration, in microseconds.
    pub fn delta_us(&self) -> Option<i64> {
        Some(self.total_us? as i64 - self.previous_total_us? as i64)
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OperationChange {
    pub operation_id: String,
    pub operation_kind: String,
    pub succeeded: bool,
    #[serde(default)]
    pub requires_reboot: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub summary: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LifecycleKind {
    Booted,
    ShutdownClean,
    /// The previous boot has no clean shutdown record — a crash or hard power-off.
    ShutdownUnclean,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LifecycleChange {
    pub kind: LifecycleKind,
    pub boot_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kernel_release: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CheckpointChange {
    pub snapshot_id: i64,
    pub label: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

/// A normalized, deduplicated system change.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChangeEvent {
    pub id: ChangeEventId,
    pub timestamp: DateTime<Utc>,
    pub source: ChangeSource,
    pub category: ChangeCategory,
    pub relevance: ChangeRelevance,
    pub title: String,
    pub description: Option<String>,
    pub metadata: ChangeMetadata,
    pub related_entities: Vec<EntityRef>,
    pub subsystems: Vec<SubsystemTag>,
    /// Every source that independently reported this change.
    pub provenance: Vec<ChangeSource>,
    /// Operation that produced this change, when it came from this application.
    pub operation_id: Option<String>,
    /// Boot during which the change was observed.
    pub boot_id: Option<String>,
}

/// An event that has not been stored yet (and so has no id).
#[derive(Debug, Clone, PartialEq)]
pub struct NewChangeEvent {
    pub timestamp: DateTime<Utc>,
    pub source: ChangeSource,
    pub category: ChangeCategory,
    pub relevance: ChangeRelevance,
    pub title: String,
    pub description: Option<String>,
    pub metadata: ChangeMetadata,
    pub related_entities: Vec<EntityRef>,
    pub subsystems: Vec<SubsystemTag>,
    /// Deterministic identity of the underlying real-world change.
    pub dedup_key: String,
    pub operation_id: Option<String>,
    pub boot_id: Option<String>,
}

impl NewChangeEvent {
    pub fn builder(
        timestamp: DateTime<Utc>,
        source: ChangeSource,
        category: ChangeCategory,
        title: impl Into<String>,
    ) -> ChangeEventBuilder {
        ChangeEventBuilder {
            event: NewChangeEvent {
                timestamp,
                source,
                category,
                relevance: ChangeRelevance::Info,
                title: title.into(),
                description: None,
                metadata: ChangeMetadata::Other,
                related_entities: Vec::new(),
                subsystems: Vec::new(),
                dedup_key: String::new(),
                operation_id: None,
                boot_id: None,
            },
        }
    }
}

pub struct ChangeEventBuilder {
    event: NewChangeEvent,
}

impl ChangeEventBuilder {
    pub fn relevance(mut self, r: ChangeRelevance) -> Self {
        self.event.relevance = r;
        self
    }

    pub fn description(mut self, d: impl Into<String>) -> Self {
        self.event.description = Some(d.into());
        self
    }

    pub fn metadata(mut self, m: ChangeMetadata) -> Self {
        self.event.metadata = m;
        self
    }

    pub fn entity(mut self, e: EntityRef) -> Self {
        self.event.related_entities.push(e);
        self
    }

    pub fn entities(mut self, e: impl IntoIterator<Item = EntityRef>) -> Self {
        self.event.related_entities.extend(e);
        self
    }

    pub fn subsystems(mut self, tags: Vec<SubsystemTag>) -> Self {
        self.event.subsystems = tags;
        self
    }

    pub fn dedup_key(mut self, k: impl Into<String>) -> Self {
        self.event.dedup_key = k.into();
        self
    }

    pub fn operation_id(mut self, id: impl Into<String>) -> Self {
        self.event.operation_id = Some(id.into());
        self
    }

    pub fn boot_id(mut self, id: impl Into<String>) -> Self {
        self.event.boot_id = Some(id.into());
        self
    }

    /// Finish the event, falling back to a derived dedup key if none was set.
    pub fn build(mut self) -> NewChangeEvent {
        if self.event.dedup_key.is_empty() {
            self.event.dedup_key = derive_dedup_key(&self.event);
        }
        self.event
    }
}

/// Fallback dedup key: the *semantic identity* of the change, with no time
/// component.
///
/// Time is deliberately excluded. An earlier version bucketed the timestamp
/// into fixed 60-second windows, which splits a single change into two events
/// whenever its two sightings straddle a bucket boundary — 30 seconds apart is
/// enough if one lands at :59 and the other at :01.
///
/// Instead, `ChangeStore` matches a candidate against existing events that
/// share this key *and* fall within [`DEDUP_WINDOW_SECS`] of it. A sliding
/// window has no boundary artefact, and it puts the time logic in the one place
/// that can see both events.
fn derive_dedup_key(e: &NewChangeEvent) -> String {
    let mut ents: Vec<String> = e.related_entities.iter().map(|x| x.to_string()).collect();
    ents.sort();
    format!("{}|{}|{}", e.category.slug(), e.title, ents.join(","))
}

/// Two sightings sharing a dedup key within this many seconds are the same
/// real-world change. Applied by `ChangeStore` at insert time, not baked into
/// the key.
///
/// Sized for the gap between a package manager writing its log and a journal
/// entry appearing for the same transaction.
pub const DEDUP_WINDOW_SECS: i64 = 300;

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    fn ts(s: i64) -> DateTime<Utc> {
        Utc.timestamp_opt(s, 0).unwrap()
    }

    #[test]
    fn package_action_is_derived_from_versions_not_from_the_word_upgraded() {
        // pacman logs a downgrade as "upgraded"; we must not repeat that lie.
        let a = PackageAction::from_versions(
            Some(&Version::new("6.18.0-1")),
            Some(&Version::new("6.17.9-1")),
        );
        assert_eq!(a, PackageAction::Downgraded);

        let b = PackageAction::from_versions(None, Some(&Version::new("1.0-1")));
        assert_eq!(b, PackageAction::Installed);
        let c = PackageAction::from_versions(Some(&Version::new("1.0-1")), None);
        assert_eq!(c, PackageAction::Removed);
    }

    #[test]
    fn version_summary_reads_like_the_ui_spec() {
        let p = PackageChange {
            name: "mesa".into(),
            action: PackageAction::Upgraded,
            old_version: Some(Version::new("26.1.3")),
            new_version: Some(Version::new("26.2.0")),
            origin: PackageOrigin::Repository,
            repository: Some("extra".into()),
        };
        assert_eq!(p.version_summary(), "26.1.3 → 26.2.0");
    }

    #[test]
    fn persistent_and_runtime_service_changes_are_distinguished() {
        assert!(ServiceChangeKind::Disabled.is_persistent());
        assert!(ServiceChangeKind::Masked.is_persistent());
        // A failure is real but it is not a configuration change.
        assert!(!ServiceChangeKind::EnteredFailed.is_persistent());
        assert!(!ServiceChangeKind::ManualRuntimeAction.is_persistent());
    }

    #[test]
    fn relevance_orders_from_info_up_to_failure() {
        assert!(ChangeRelevance::Failure > ChangeRelevance::Warning);
        assert!(ChangeRelevance::Warning > ChangeRelevance::PotentiallyRelevant);
        assert!(ChangeRelevance::PotentiallyRelevant > ChangeRelevance::Notable);
        assert!(ChangeRelevance::Notable > ChangeRelevance::Info);
    }

    #[test]
    fn our_own_operations_outrank_the_journal_as_provenance() {
        assert!(
            ChangeSource::OperationEngine.provenance_rank()
                > ChangeSource::Pacman.provenance_rank()
        );
        assert!(ChangeSource::Pacman.provenance_rank() > ChangeSource::Journal.provenance_rank());
    }

    #[test]
    fn derived_dedup_key_is_time_independent() {
        let mk = |t: i64| {
            NewChangeEvent::builder(
                ts(t),
                ChangeSource::Pacman,
                ChangeCategory::Package,
                "mesa updated",
            )
            .entity(EntityRef::package("mesa"))
            .build()
            .dedup_key
        };
        // Two collectors seeing one change at slightly different times must
        // agree on the key, regardless of where the timestamps fall. The old
        // bucketing scheme failed exactly here when the pair straddled a
        // minute boundary.
        assert_eq!(mk(59), mk(61));
        assert_eq!(mk(1_000), mk(1_030));
        assert_eq!(mk(1_000), mk(999_999));
    }

    #[test]
    fn derived_dedup_key_separates_genuinely_different_changes() {
        let mk = |title: &str, pkg: &str| {
            NewChangeEvent::builder(ts(0), ChangeSource::Pacman, ChangeCategory::Package, title)
                .entity(EntityRef::package(pkg))
                .build()
                .dedup_key
        };
        assert_ne!(mk("mesa updated", "mesa"), mk("mesa removed", "mesa"));
        assert_ne!(mk("mesa updated", "mesa"), mk("mesa updated", "libdrm"));
    }

    #[test]
    fn entity_order_does_not_affect_the_key() {
        let mk = |entities: Vec<EntityRef>| {
            NewChangeEvent::builder(
                ts(0),
                ChangeSource::Pacman,
                ChangeCategory::Kernel,
                "kernel updated",
            )
            .entities(entities)
            .build()
            .dedup_key
        };
        // Two collectors may list the same entities in different orders.
        assert_eq!(
            mk(vec![
                EntityRef::package("linux"),
                EntityRef::kernel_release("6.18.0")
            ]),
            mk(vec![
                EntityRef::kernel_release("6.18.0"),
                EntityRef::package("linux")
            ])
        );
    }

    #[test]
    fn explicit_dedup_key_is_preserved() {
        let e = NewChangeEvent::builder(
            ts(0),
            ChangeSource::Pacman,
            ChangeCategory::Package,
            "mesa updated",
        )
        .dedup_key("pkg:mesa:26.1.3->26.2.0")
        .build();
        assert_eq!(e.dedup_key, "pkg:mesa:26.1.3->26.2.0");
    }

    #[test]
    fn metadata_round_trips_through_json_with_its_tag() {
        let m = ChangeMetadata::Dkms(DkmsChange {
            module: "v4l2loopback".into(),
            module_version: "0.13.2".into(),
            kernel_release: "6.18.0-cachyos".into(),
            status: DkmsStatus::BuildFailed,
            detail: None,
        });
        let json = serde_json::to_string(&m).unwrap();
        assert!(json.contains("\"kind\":\"dkms\""));
        assert_eq!(serde_json::from_str::<ChangeMetadata>(&json).unwrap(), m);
    }

    #[test]
    fn unknown_metadata_kinds_decode_to_other_instead_of_failing() {
        // Forward compatibility: a newer version's event must not break an older reader.
        let json = r#"{"kind":"quantum_flux","foo":1}"#;
        assert_eq!(
            serde_json::from_str::<ChangeMetadata>(json).unwrap(),
            ChangeMetadata::Other
        );
    }

    #[test]
    fn boot_change_detects_kernel_switch_and_slowdown() {
        let b = BootChange {
            boot_id: "b2".into(),
            kernel_release: "6.18.0-cachyos".into(),
            previous_boot_id: Some("b1".into()),
            previous_kernel_release: Some("6.17.9-cachyos".into()),
            total_us: Some(11_400_000),
            previous_total_us: Some(7_100_000),
        };
        assert!(b.kernel_changed());
        assert_eq!(b.delta_us(), Some(4_300_000));
    }

    #[test]
    fn pacnew_is_flagged_as_needing_attention() {
        assert!(ConfigChangeKind::PacnewCreated.needs_admin_attention());
        assert!(!ConfigChangeKind::Modified.needs_admin_attention());
    }
}
