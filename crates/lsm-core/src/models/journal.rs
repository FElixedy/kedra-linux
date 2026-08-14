//! Journal model.
//!
//! The viewer is bounded by construction: every query carries a limit and a
//! cursor, so no code path can load the whole journal into memory.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// syslog priority levels.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Priority {
    Emergency = 0,
    Alert = 1,
    Critical = 2,
    Error = 3,
    Warning = 4,
    Notice = 5,
    Info = 6,
    Debug = 7,
}

impl Priority {
    pub const ALL: &'static [Priority] = &[
        Priority::Emergency,
        Priority::Alert,
        Priority::Critical,
        Priority::Error,
        Priority::Warning,
        Priority::Notice,
        Priority::Info,
        Priority::Debug,
    ];

    pub fn from_number(n: u8) -> Self {
        match n {
            0 => Priority::Emergency,
            1 => Priority::Alert,
            2 => Priority::Critical,
            3 => Priority::Error,
            4 => Priority::Warning,
            5 => Priority::Notice,
            6 => Priority::Info,
            _ => Priority::Debug,
        }
    }

    pub fn as_number(&self) -> u8 {
        *self as u8
    }

    pub fn label(&self) -> &'static str {
        match self {
            Priority::Emergency => "Emergency",
            Priority::Alert => "Alert",
            Priority::Critical => "Critical",
            Priority::Error => "Error",
            Priority::Warning => "Warning",
            Priority::Notice => "Notice",
            Priority::Info => "Info",
            Priority::Debug => "Debug",
        }
    }

    /// Style class for the severity dot.
    pub fn css_class(&self) -> &'static str {
        match self {
            Priority::Emergency | Priority::Alert | Priority::Critical => "lsm-priority-critical",
            Priority::Error => "lsm-priority-error",
            Priority::Warning => "lsm-priority-warning",
            Priority::Notice => "lsm-priority-notice",
            Priority::Info => "lsm-priority-info",
            Priority::Debug => "lsm-priority-debug",
        }
    }

    /// True when this level is at least as severe as `threshold`.
    /// Lower numbers are more severe, which is easy to get backwards.
    pub fn at_least_as_severe_as(&self, threshold: Priority) -> bool {
        self.as_number() <= threshold.as_number()
    }
}

/// One journal entry, normalized from journalctl's JSON output.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JournalEntry {
    /// Opaque cursor, used to resume paging exactly where we left off.
    pub cursor: String,
    pub timestamp: DateTime<Utc>,
    pub priority: Priority,
    pub message: String,
    /// `SYSLOG_IDENTIFIER`, the name shown in the list.
    pub identifier: Option<String>,
    /// `_SYSTEMD_UNIT` or `_SYSTEMD_USER_UNIT`; links to the service page.
    pub unit: Option<String>,
    pub pid: Option<u32>,
    pub boot_id: Option<String>,
    pub hostname: Option<String>,
}

/// A bounded journal query. There is no "everything" variant on purpose.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JournalQuery {
    /// Maximum entries to return. Always set.
    pub limit: usize,
    /// Only entries at or above this severity.
    pub max_priority: Option<Priority>,
    /// Restrict to these units.
    pub units: Vec<String>,
    /// Restrict to a boot: `0` is the current boot, `-1` the previous one.
    pub boot_offset: Option<i32>,
    pub since: Option<DateTime<Utc>>,
    pub until: Option<DateTime<Utc>>,
    /// Free-text match applied by journalctl (`--grep`).
    pub grep: Option<String>,
    /// Resume after this cursor.
    pub after_cursor: Option<String>,
    /// Read the user journal instead of the system one.
    pub user_scope: bool,
}

impl Default for JournalQuery {
    fn default() -> Self {
        JournalQuery {
            limit: DEFAULT_JOURNAL_PAGE,
            max_priority: None,
            units: Vec::new(),
            boot_offset: Some(0),
            since: None,
            until: None,
            grep: None,
            after_cursor: None,
            user_scope: false,
        }
    }
}

impl JournalQuery {
    /// Clamp the limit so a caller cannot ask for an unbounded read.
    pub fn sanitized(mut self) -> Self {
        self.limit = self.limit.clamp(1, MAX_JOURNAL_PAGE);
        self
    }
}

/// Entries fetched per page during incremental scrolling.
pub const DEFAULT_JOURNAL_PAGE: usize = 250;
/// Hard ceiling on a single journal read.
pub const MAX_JOURNAL_PAGE: usize = 5_000;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn priority_numbers_map_both_ways() {
        for p in Priority::ALL {
            assert_eq!(Priority::from_number(p.as_number()), *p);
        }
        // Anything above 7 is debug rather than a panic.
        assert_eq!(Priority::from_number(9), Priority::Debug);
    }

    #[test]
    fn severity_comparison_is_not_accidentally_inverted() {
        // Errors are more severe than warnings despite having a lower number.
        assert!(Priority::Error.at_least_as_severe_as(Priority::Warning));
        assert!(!Priority::Info.at_least_as_severe_as(Priority::Warning));
        assert!(Priority::Emergency.at_least_as_severe_as(Priority::Debug));
        assert!(Priority::Warning.at_least_as_severe_as(Priority::Warning));
    }

    #[test]
    fn queries_are_always_bounded() {
        let q = JournalQuery::default();
        assert_eq!(q.limit, DEFAULT_JOURNAL_PAGE);

        // A caller asking for everything gets clamped instead.
        let huge = JournalQuery {
            limit: usize::MAX,
            ..Default::default()
        }
        .sanitized();
        assert_eq!(huge.limit, MAX_JOURNAL_PAGE);

        let zero = JournalQuery {
            limit: 0,
            ..Default::default()
        }
        .sanitized();
        assert_eq!(zero.limit, 1);
    }
}
