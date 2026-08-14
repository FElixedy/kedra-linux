//! Configuration tracking model.
//!
//! Privacy is a design constraint, not a feature: the tracker stores *hashes and
//! metadata*, never file contents. Contents are read only when the user
//! explicitly opens a diff, and paths that commonly hold secrets are excluded
//! from hashing entirely (see [`WatchSet::is_sensitive`]).

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

pub use super::change::ConfigChangeKind;

/// Recorded state of one tracked file.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ConfigFileState {
    pub path: String,
    /// SHA-256 of the contents. `None` for symlinks, directories, and files
    /// excluded as sensitive.
    pub hash: Option<String>,
    pub kind: FileKind,
    pub size: u64,
    pub modified: Option<DateTime<Utc>>,
    /// Unix mode bits (permissions only).
    pub mode: u32,
    pub uid: u32,
    pub gid: u32,
    pub symlink_target: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FileKind {
    Regular,
    Symlink,
    Directory,
    Other,
}

impl ConfigFileState {
    /// Whether two observations describe a meaningful change.
    ///
    /// Deliberately ignores mtime on its own: a file touched by a package
    /// upgrade whose content is identical has not changed in any way a
    /// troubleshooter cares about.
    pub fn diff_kind(old: &ConfigFileState, new: &ConfigFileState) -> Option<ConfigChangeKind> {
        if old.kind != new.kind {
            return Some(ConfigChangeKind::Modified);
        }
        match new.kind {
            FileKind::Symlink => {
                if old.symlink_target != new.symlink_target {
                    Some(ConfigChangeKind::SymlinkTargetChanged)
                } else {
                    None
                }
            }
            _ => {
                if old.hash != new.hash {
                    Some(ConfigChangeKind::Modified)
                } else if old.mode != new.mode || old.uid != new.uid || old.gid != new.gid {
                    Some(ConfigChangeKind::PermissionsChanged)
                } else {
                    None
                }
            }
        }
    }
}

/// A pacman-generated configuration artefact that needs an admin decision.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PacnewFile {
    /// The `.pacnew`/`.pacsave` file itself.
    pub path: String,
    /// The live configuration file it relates to.
    pub target_path: String,
    pub kind: PacnewKind,
    pub created: Option<DateTime<Utc>>,
    pub owning_package: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PacnewKind {
    /// The package shipped a new default; the admin's edited file was kept.
    Pacnew,
    /// Pacman preserved the admin's file while removing/replacing the package's.
    Pacsave,
    /// Pacman kept the original alongside a forcibly-installed new file.
    Pacorig,
}

impl PacnewKind {
    pub fn extension(&self) -> &'static str {
        match self {
            PacnewKind::Pacnew => "pacnew",
            PacnewKind::Pacsave => "pacsave",
            PacnewKind::Pacorig => "pacorig",
        }
    }

    pub fn from_extension(ext: &str) -> Option<Self> {
        Some(match ext {
            "pacnew" => PacnewKind::Pacnew,
            "pacsave" => PacnewKind::Pacsave,
            "pacorig" => PacnewKind::Pacorig,
            _ => return None,
        })
    }

    pub fn label(&self) -> &'static str {
        match self {
            PacnewKind::Pacnew => "New default available",
            PacnewKind::Pacsave => "Your file was preserved",
            PacnewKind::Pacorig => "Original file kept",
        }
    }

    pub fn to_change_kind(self) -> ConfigChangeKind {
        match self {
            PacnewKind::Pacnew | PacnewKind::Pacorig => ConfigChangeKind::PacnewCreated,
            PacnewKind::Pacsave => ConfigChangeKind::PacsaveCreated,
        }
    }
}

/// Split `/etc/pacman.conf.pacnew` into `("/etc/pacman.conf", Pacnew)`.
pub fn parse_pacnew_path(path: &str) -> Option<(String, PacnewKind)> {
    let (base, ext) = path.rsplit_once('.')?;
    let kind = PacnewKind::from_extension(ext)?;
    Some((base.to_string(), kind))
}

/// Which paths the configuration tracker watches.
///
/// Central and configurable — the tracker itself contains no path literals.
/// Scope is `/etc` only: never the whole filesystem, never `$HOME`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WatchSet {
    /// Directory roots to walk, in order.
    pub roots: Vec<String>,
    /// Subtrees excluded entirely.
    pub excluded_prefixes: Vec<String>,
    /// Path fragments whose *contents* are never hashed or read, because they
    /// routinely contain credentials or keys. Their existence and permissions
    /// are still tracked.
    pub sensitive_fragments: Vec<String>,
    /// Maximum size of a file we will hash; larger files are tracked by
    /// metadata only, to keep scans fast.
    pub max_hash_bytes: u64,
    /// How deep to walk below each root.
    pub max_depth: usize,
}

impl Default for WatchSet {
    fn default() -> Self {
        WatchSet {
            roots: DEFAULT_WATCH_ROOTS.iter().map(|s| s.to_string()).collect(),
            excluded_prefixes: DEFAULT_EXCLUDED.iter().map(|s| s.to_string()).collect(),
            sensitive_fragments: DEFAULT_SENSITIVE.iter().map(|s| s.to_string()).collect(),
            max_hash_bytes: 4 * 1024 * 1024,
            max_depth: 8,
        }
    }
}

impl WatchSet {
    pub fn is_excluded(&self, path: &str) -> bool {
        self.excluded_prefixes
            .iter()
            .any(|p| path.starts_with(p.as_str()))
    }

    /// Whether the file's *contents* must never be read or hashed.
    pub fn is_sensitive(&self, path: &str) -> bool {
        self.sensitive_fragments
            .iter()
            .any(|f| path.contains(f.as_str()))
    }

    pub fn covers(&self, path: &str) -> bool {
        self.roots.iter().any(|r| path.starts_with(r.as_str())) && !self.is_excluded(path)
    }
}

/// Watched roots. `/etc` as a whole, because a change anywhere under it can
/// break a system; the interesting subtrees are called out in the UI.
const DEFAULT_WATCH_ROOTS: &[&str] = &["/etc"];

/// Excluded: churn with no troubleshooting value, and anything that is
/// effectively runtime state rather than configuration.
const DEFAULT_EXCLUDED: &[&str] = &[
    "/etc/ssl/certs",       // regenerated wholesale by ca-certificates
    "/etc/ca-certificates", // ditto
    "/etc/pacman.d/gnupg",  // keyring state, changes constantly, sensitive
    "/etc/mtab",            // symlink to /proc, changes on every mount
    "/etc/adjtime",         // rewritten by the RTC on every boot
    "/etc/machine-id",      // stable, but a hardware identifier we never store
    "/etc/.updated",
    "/etc/.pwd.lock",
    "/etc/cups/subscriptions.conf", // rewritten continuously by cupsd
];

/// Contents never read. Existence, mode and ownership are still tracked,
/// because *permissions* on these files matter for troubleshooting.
const DEFAULT_SENSITIVE: &[&str] = &[
    "/etc/shadow",
    "/etc/gshadow",
    "/etc/sudoers",
    "/etc/ssh/ssh_host_",
    "/etc/wpa_supplicant/",
    "/etc/NetworkManager/system-connections/",
    "/etc/iwd/",
    "/etc/wireguard/",
    "/etc/ppp/chap-secrets",
    "/etc/ppp/pap-secrets",
    "/etc/krb5.keytab",
];

/// Subtrees highlighted in the UI as commonly implicated in breakage.
pub const NOTABLE_CONFIG_AREAS: &[(&str, &str)] = &[
    ("/etc/systemd", "systemd units and overrides"),
    ("/etc/NetworkManager", "Network configuration"),
    ("/etc/modprobe.d", "Kernel module options"),
    ("/etc/modules-load.d", "Modules loaded at boot"),
    ("/etc/pacman.d", "Package manager configuration"),
    ("/etc/mkinitcpio.d", "Initramfs presets"),
    ("/etc/mkinitcpio.conf.d", "Initramfs configuration"),
    ("/etc/default", "Bootloader and daemon defaults"),
    ("/etc/udev", "Device rules"),
    ("/etc/X11", "Display server configuration"),
    ("/etc/environment", "System-wide environment"),
];

#[cfg(test)]
mod tests {
    use super::*;

    fn state(hash: &str, mode: u32) -> ConfigFileState {
        ConfigFileState {
            path: "/etc/fstab".into(),
            hash: Some(hash.into()),
            kind: FileKind::Regular,
            size: 100,
            modified: None,
            mode,
            uid: 0,
            gid: 0,
            symlink_target: None,
        }
    }

    #[test]
    fn identical_content_is_not_a_change_even_if_touched() {
        let mut a = state("abc", 0o644);
        let mut b = state("abc", 0o644);
        a.modified = Some(Utc::now() - chrono::Duration::days(1));
        b.modified = Some(Utc::now());
        b.size = 100;
        assert_eq!(ConfigFileState::diff_kind(&a, &b), None);
    }

    #[test]
    fn content_and_permission_changes_are_distinguished() {
        assert_eq!(
            ConfigFileState::diff_kind(&state("abc", 0o644), &state("def", 0o644)),
            Some(ConfigChangeKind::Modified)
        );
        assert_eq!(
            ConfigFileState::diff_kind(&state("abc", 0o644), &state("abc", 0o600)),
            Some(ConfigChangeKind::PermissionsChanged)
        );
    }

    #[test]
    fn symlink_retargeting_is_its_own_change_kind() {
        let link = |target: &str| ConfigFileState {
            path: "/etc/resolv.conf".into(),
            hash: None,
            kind: FileKind::Symlink,
            size: 0,
            modified: None,
            mode: 0o777,
            uid: 0,
            gid: 0,
            symlink_target: Some(target.into()),
        };
        // The classic DNS breakage: resolv.conf repointed away from resolved.
        assert_eq!(
            ConfigFileState::diff_kind(
                &link("/run/systemd/resolve/stub-resolv.conf"),
                &link("/run/NetworkManager/resolv.conf")
            ),
            Some(ConfigChangeKind::SymlinkTargetChanged)
        );
        assert_eq!(ConfigFileState::diff_kind(&link("/a"), &link("/a")), None);
    }

    #[test]
    fn pacnew_paths_are_split_into_target_and_kind() {
        assert_eq!(
            parse_pacnew_path("/etc/pacman.conf.pacnew"),
            Some(("/etc/pacman.conf".to_string(), PacnewKind::Pacnew))
        );
        assert_eq!(
            parse_pacnew_path("/etc/makepkg.conf.pacsave"),
            Some(("/etc/makepkg.conf".to_string(), PacnewKind::Pacsave))
        );
        assert_eq!(parse_pacnew_path("/etc/pacman.conf"), None);
        assert_eq!(parse_pacnew_path("/etc/foo.conf.bak"), None);
    }

    #[test]
    fn secret_bearing_files_are_never_hashed_but_are_still_watched() {
        let w = WatchSet::default();
        for p in [
            "/etc/shadow",
            "/etc/ssh/ssh_host_ed25519_key",
            "/etc/NetworkManager/system-connections/Home.nmconnection",
        ] {
            assert!(w.is_sensitive(p), "{p} must never have its contents read");
            // ...but it is still inside the watch scope, so permission changes show up.
            assert!(w.covers(p), "{p} should still be tracked by metadata");
        }
        assert!(!w.is_sensitive("/etc/fstab"));
    }

    #[test]
    fn scope_is_etc_only_and_never_the_home_directory() {
        let w = WatchSet::default();
        assert!(w.covers("/etc/fstab"));
        assert!(!w.covers("/home/someone/.bashrc"));
        assert!(!w.covers("/var/lib/pacman/local"));
        assert!(!w.covers("/usr/lib/systemd/system/foo.service"));
    }

    #[test]
    fn high_churn_paths_are_excluded_so_the_timeline_stays_readable() {
        let w = WatchSet::default();
        assert!(w.is_excluded("/etc/ssl/certs/ca-certificates.crt"));
        assert!(w.is_excluded("/etc/pacman.d/gnupg/trustdb.gpg"));
        assert!(w.is_excluded("/etc/adjtime"));
        assert!(!w.is_excluded("/etc/pacman.d/mirrorlist"));
    }
}
