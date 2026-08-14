//! Package version handling, including an implementation of pacman's `vercmp`.
//!
//! We need real version comparison (not string equality) so that snapshot diffs
//! can say *upgraded* vs *downgraded* — a downgrade is far more interesting to a
//! troubleshooter than an upgrade, and it is the thing users do to recover.
//!
//! The algorithm mirrors `alpm_pkg_vercmp`, which itself derives from rpmvercmp:
//! versions are `[epoch:]version[-release]`; each part is split into runs of
//! digits and runs of letters, compared segment by segment, with numeric runs
//! compared numerically and always sorting above alphabetic runs.

use std::cmp::Ordering;
use std::fmt;

use serde::{Deserialize, Serialize};

/// A package version string, retained verbatim for display.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Version(pub String);

impl Version {
    pub fn new(s: impl Into<String>) -> Self {
        Version(s.into())
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// Split into `(epoch, version, release)`.
    fn parts(&self) -> (&str, &str, Option<&str>) {
        let s = self.0.as_str();
        let (epoch, rest) = match s.split_once(':') {
            // Only treat it as an epoch when it is actually numeric; some
            // versions legitimately contain a colon elsewhere.
            Some((e, r)) if !e.is_empty() && e.bytes().all(|b| b.is_ascii_digit()) => (e, r),
            _ => ("0", s),
        };
        match rest.rsplit_once('-') {
            Some((v, r)) => (epoch, v, Some(r)),
            None => (epoch, rest, None),
        }
    }
}

impl fmt::Display for Version {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl PartialOrd for Version {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for Version {
    fn cmp(&self, other: &Self) -> Ordering {
        let (ae, av, ar) = self.parts();
        let (be, bv, br) = other.parts();

        match rpmvercmp(ae, be) {
            Ordering::Equal => {}
            o => return o,
        }
        match rpmvercmp(av, bv) {
            Ordering::Equal => {}
            o => return o,
        }
        // A missing release compares equal: `1.0` vs `1.0-2` is not a difference
        // pacman considers meaningful unless both sides carry a release.
        match (ar, br) {
            (Some(a), Some(b)) => rpmvercmp(a, b),
            _ => Ordering::Equal,
        }
    }
}

/// Compare two version segments using the rpmvercmp / alpm algorithm.
fn rpmvercmp(a: &str, b: &str) -> Ordering {
    if a == b {
        return Ordering::Equal;
    }
    let a: Vec<char> = a.chars().collect();
    let b: Vec<char> = b.chars().collect();
    let (mut i, mut j) = (0usize, 0usize);

    loop {
        // Skip anything that is neither alphanumeric nor a tilde.
        while i < a.len() && !a[i].is_ascii_alphanumeric() && a[i] != '~' {
            i += 1;
        }
        while j < b.len() && !b[j].is_ascii_alphanumeric() && b[j] != '~' {
            j += 1;
        }

        // A tilde sorts *before* everything, including the empty string, so
        // `1.0~rc1` < `1.0`. This is how pre-release versions are expressed.
        let a_tilde = i < a.len() && a[i] == '~';
        let b_tilde = j < b.len() && b[j] == '~';
        if a_tilde || b_tilde {
            match (a_tilde, b_tilde) {
                (true, true) => {
                    i += 1;
                    j += 1;
                    continue;
                }
                (true, false) => return Ordering::Less,
                (false, true) => return Ordering::Greater,
                _ => unreachable!(),
            }
        }

        if i >= a.len() || j >= b.len() {
            break;
        }

        let numeric = a[i].is_ascii_digit();
        let start_i = i;
        let start_j = j;
        if numeric {
            while i < a.len() && a[i].is_ascii_digit() {
                i += 1;
            }
            while j < b.len() && b[j].is_ascii_digit() {
                j += 1;
            }
        } else {
            while i < a.len() && a[i].is_ascii_alphabetic() {
                i += 1;
            }
            while j < b.len() && b[j].is_ascii_alphabetic() {
                j += 1;
            }
        }

        let seg_a: String = a[start_i..i].iter().collect();
        let seg_b: String = b[start_j..j].iter().collect();

        // One side ran out of this segment type: a numeric run outranks an
        // alphabetic one, so `1.1` > `1.a`.
        if seg_b.is_empty() {
            return if numeric {
                Ordering::Greater
            } else {
                Ordering::Less
            };
        }

        let ord = if numeric {
            let na = seg_a.trim_start_matches('0');
            let nb = seg_b.trim_start_matches('0');
            match na.len().cmp(&nb.len()) {
                Ordering::Equal => na.cmp(nb),
                o => o,
            }
        } else {
            seg_a.cmp(&seg_b)
        };
        if ord != Ordering::Equal {
            return ord;
        }
    }

    // Whichever side still has content is the newer one, except that trailing
    // alphabetic content marks a pre-release and sorts lower.
    match (i >= a.len(), j >= b.len()) {
        (true, true) => Ordering::Equal,
        (true, false) => {
            if b[j].is_ascii_alphabetic() {
                Ordering::Greater
            } else {
                Ordering::Less
            }
        }
        (false, true) => {
            if a[i].is_ascii_alphabetic() {
                Ordering::Less
            } else {
                Ordering::Greater
            }
        }
        (false, false) => Ordering::Equal,
    }
}

/// The direction of a version change, as shown in What Changed?.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum VersionDelta {
    Upgrade,
    Downgrade,
    /// Same version, different build/release only.
    Rebuild,
    Unchanged,
}

impl VersionDelta {
    pub fn between(old: &Version, new: &Version) -> Self {
        match old.cmp(new) {
            Ordering::Less => VersionDelta::Upgrade,
            Ordering::Greater => VersionDelta::Downgrade,
            Ordering::Equal if old.0 != new.0 => VersionDelta::Rebuild,
            Ordering::Equal => VersionDelta::Unchanged,
        }
    }

    pub fn label(&self) -> &'static str {
        match self {
            VersionDelta::Upgrade => "Upgraded",
            VersionDelta::Downgrade => "Downgraded",
            VersionDelta::Rebuild => "Rebuilt",
            VersionDelta::Unchanged => "Unchanged",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cmp(a: &str, b: &str) -> Ordering {
        Version::new(a).cmp(&Version::new(b))
    }

    #[test]
    fn compares_numeric_segments_numerically_not_lexically() {
        // The classic trap: "10" must be greater than "9".
        assert_eq!(cmp("1.10.0", "1.9.0"), Ordering::Greater);
        assert_eq!(cmp("6.18.0", "6.17.9"), Ordering::Greater);
        assert_eq!(cmp("1.0.0", "1.0.0"), Ordering::Equal);
    }

    #[test]
    fn honours_epoch_above_everything_else() {
        // ffmpeg 2:9.0.1-1 outranks 9.9.9 despite the smaller version.
        assert_eq!(cmp("2:9.0.1-1", "9.9.9-1"), Ordering::Greater);
        assert_eq!(cmp("1:1.0-1", "2:1.0-1"), Ordering::Less);
    }

    #[test]
    fn compares_release_when_version_is_equal() {
        assert_eq!(cmp("26.1.3-1", "26.1.3-2"), Ordering::Less);
        // CachyOS-style releases with a dotted suffix.
        assert_eq!(cmp("50.5-1.1", "50.6-1.1"), Ordering::Less);
        assert_eq!(cmp("2.5.7-1.1", "2.6.0-1"), Ordering::Less);
    }

    #[test]
    fn tilde_sorts_below_the_release_it_precedes() {
        assert_eq!(cmp("1.0~rc1", "1.0"), Ordering::Less);
        assert_eq!(cmp("1.0~rc1", "1.0~rc2"), Ordering::Less);
    }

    #[test]
    fn alphabetic_suffix_marks_a_prerelease() {
        assert_eq!(cmp("1.0alpha", "1.0"), Ordering::Less);
        assert_eq!(cmp("1.0.1", "1.0"), Ordering::Greater);
    }

    #[test]
    fn leading_zeroes_do_not_change_the_value() {
        assert_eq!(cmp("1.007", "1.7"), Ordering::Equal);
    }

    #[test]
    fn missing_release_is_not_treated_as_a_difference() {
        assert_eq!(cmp("1.0", "1.0-3"), Ordering::Equal);
    }

    #[test]
    fn delta_detects_downgrade_which_matters_most_for_troubleshooting() {
        let old = Version::new("6.18.0-1");
        let new = Version::new("6.17.9-1");
        assert_eq!(VersionDelta::between(&old, &new), VersionDelta::Downgrade);
        assert_eq!(VersionDelta::between(&new, &old), VersionDelta::Upgrade);
        assert_eq!(
            VersionDelta::between(&Version::new("1.0-1"), &Version::new("1.0-1")),
            VersionDelta::Unchanged
        );
    }

    #[test]
    fn real_world_pacman_log_versions_order_correctly() {
        // Taken from an actual pacman.log upgrade line.
        assert_eq!(cmp("26.803.81509-1", "26.810.41047-1"), Ordering::Less);
        assert_eq!(cmp("1.28929.0-1", "1.30096.1-1"), Ordering::Less);
        assert_eq!(cmp("2:1.26.5-2", "2:1.26.6-2"), Ordering::Less);
        assert_eq!(cmp("2.3.10-7", "2.3.10-8"), Ordering::Less);
    }
}
