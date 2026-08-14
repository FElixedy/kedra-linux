//! Structured error type shared by every subsystem.
//!
//! Errors carry enough structure for the UI to render a concise, human message
//! (`user_message`) plus an expandable technical detail (`technical_detail`).
//! Raw panics must never reach the UI; see [`Error::from_panic`].

use std::fmt;
use std::path::PathBuf;

pub type Result<T, E = Error> = std::result::Result<T, E>;

/// The kind of a package manager that holds a lock.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LockHolder {
    Pacman,
    Unknown,
}

impl fmt::Display for LockHolder {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            LockHolder::Pacman => write!(f, "pacman"),
            LockHolder::Unknown => write!(f, "another package manager"),
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("the package database is locked by {holder}")]
    PackageManagerLocked {
        holder: LockHolder,
        lock_path: PathBuf,
    },

    #[error("authentication was denied")]
    AuthenticationDenied,

    #[error("permission denied: {0}")]
    PermissionDenied(String),

    #[error("the network is unavailable")]
    NetworkUnavailable { detail: String },

    #[error("package `{0}` was not found")]
    PackageNotFound(String),

    #[error("dependency conflict")]
    DependencyConflict { detail: String },

    #[error("operation on service `{unit}` failed")]
    ServiceOperationFailed { unit: String, detail: String },

    #[error("unsupported distribution: {0}")]
    UnsupportedDistribution(String),

    /// A collector could not run; What Changed? degrades gracefully rather than failing.
    #[error("collector `{collector}` is unavailable: {reason}")]
    CollectorUnavailable {
        collector: &'static str,
        reason: String,
    },

    #[error("snapshot failed: {0}")]
    SnapshotFailure(String),

    #[error("database failure: {0}")]
    DatabaseFailure(String),

    #[error("`{program}` is not installed")]
    ToolMissing { program: String },

    #[error("`{program}` exited with status {status}")]
    CommandFailed {
        program: String,
        status: i32,
        stderr: String,
    },

    #[error("could not parse {what}")]
    Parse { what: &'static str, detail: String },

    #[error("the privileged helper failed: {0}")]
    HelperFailure(String),

    /// The requested privileged action failed validation and was never executed.
    #[error("rejected unsafe request: {0}")]
    ValidationRejected(String),

    #[error("the operation was cancelled")]
    Cancelled,

    #[error("i/o error on {path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },

    #[error("{0}")]
    Internal(String),
}

impl Error {
    pub fn io(path: impl Into<PathBuf>, source: std::io::Error) -> Self {
        Error::Io {
            path: path.into(),
            source,
        }
    }

    pub fn parse(what: &'static str, detail: impl Into<String>) -> Self {
        Error::Parse {
            what,
            detail: detail.into(),
        }
    }

    pub fn internal(msg: impl Into<String>) -> Self {
        Error::Internal(msg.into())
    }

    /// Wrap a caught panic so it surfaces as a normal error instead of aborting
    /// or leaking a backtrace into the UI.
    pub fn from_panic(payload: Box<dyn std::any::Any + Send>) -> Self {
        let msg = payload
            .downcast_ref::<&'static str>()
            .map(|s| (*s).to_string())
            .or_else(|| payload.downcast_ref::<String>().cloned())
            .unwrap_or_else(|| "unknown panic".to_string());
        Error::Internal(format!("internal error: {msg}"))
    }

    /// Short, non-technical message suitable for a dialog heading or toast.
    pub fn user_message(&self) -> String {
        match self {
            Error::PackageManagerLocked { holder, .. } => {
                format!("Another {holder} operation is already running. Wait for it to finish and try again.")
            }
            Error::AuthenticationDenied => "Authentication was cancelled or denied.".into(),
            Error::PermissionDenied(_) => "You do not have permission to do that.".into(),
            Error::NetworkUnavailable { .. } => {
                "Could not reach the network. Check your connection and try again.".into()
            }
            Error::PackageNotFound(p) => format!("No package named “{p}” was found."),
            Error::DependencyConflict { .. } => {
                "This change conflicts with packages already installed.".into()
            }
            Error::ServiceOperationFailed { unit, .. } => format!("Could not change “{unit}”."),
            Error::UnsupportedDistribution(d) => {
                format!("{d} is not supported yet. Package management is unavailable.")
            }
            Error::CollectorUnavailable { .. } => {
                "Some change history could not be collected.".into()
            }
            Error::SnapshotFailure(_) => "The system snapshot could not be created.".into(),
            Error::DatabaseFailure(_) => "The change history database could not be read.".into(),
            Error::ToolMissing { program } => format!("“{program}” is not installed."),
            Error::CommandFailed { program, .. } => format!("“{program}” reported an error."),
            Error::Parse { what, .. } => format!("Could not read {what}."),
            Error::HelperFailure(_) => "The privileged helper stopped unexpectedly.".into(),
            Error::ValidationRejected(_) => "That request was refused as unsafe.".into(),
            Error::Cancelled => "The operation was cancelled.".into(),
            Error::Io { .. } => "A file could not be read.".into(),
            Error::Internal(_) => "Something went wrong.".into(),
        }
    }

    /// Full technical text for the "Details" expander. May be multi-line.
    pub fn technical_detail(&self) -> String {
        match self {
            Error::PackageManagerLocked { lock_path, .. } => {
                format!("Lock file present: {}", lock_path.display())
            }
            Error::NetworkUnavailable { detail } => detail.clone(),
            Error::DependencyConflict { detail } => detail.clone(),
            Error::ServiceOperationFailed { detail, .. } => detail.clone(),
            Error::CollectorUnavailable { collector, reason } => format!("{collector}: {reason}"),
            Error::CommandFailed {
                program,
                status,
                stderr,
            } => {
                format!("{program} exited with status {status}\n\n{stderr}")
            }
            Error::Parse { what, detail } => format!("while parsing {what}: {detail}"),
            Error::Io { path, source } => format!("{}: {source}", path.display()),
            other => other.to_string(),
        }
    }

    /// Whether retrying the identical request could plausibly succeed later.
    pub fn is_transient(&self) -> bool {
        matches!(
            self,
            Error::PackageManagerLocked { .. }
                | Error::NetworkUnavailable { .. }
                | Error::Cancelled
        )
    }
}

impl From<rusqlite::Error> for Error {
    fn from(e: rusqlite::Error) -> Self {
        Error::DatabaseFailure(e.to_string())
    }
}

impl From<serde_json::Error> for Error {
    fn from(e: serde_json::Error) -> Self {
        Error::Parse {
            what: "structured data",
            detail: e.to_string(),
        }
    }
}

impl From<zbus::Error> for Error {
    fn from(e: zbus::Error) -> Self {
        match &e {
            zbus::Error::MethodError(name, detail, _) => {
                let n = name.as_str();
                if n.contains("AccessDenied") || n.contains("AuthFailed") {
                    Error::PermissionDenied(detail.clone().unwrap_or_else(|| n.to_string()))
                } else if n.contains("NoSuchUnit") {
                    Error::ServiceOperationFailed {
                        unit: "unknown".into(),
                        detail: detail.clone().unwrap_or_else(|| n.to_string()),
                    }
                } else {
                    Error::Internal(format!(
                        "D-Bus: {n}: {}",
                        detail.clone().unwrap_or_default()
                    ))
                }
            }
            _ => Error::Internal(format!("D-Bus: {e}")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn user_messages_are_concise_and_free_of_jargon() {
        let e = Error::PackageManagerLocked {
            holder: LockHolder::Pacman,
            lock_path: "/var/lib/pacman/db.lck".into(),
        };
        let msg = e.user_message();
        assert!(msg.len() < 160, "user message should stay short: {msg}");
        assert!(
            !msg.contains("/var/lib"),
            "paths belong in technical_detail"
        );
        assert!(e.technical_detail().contains("/var/lib/pacman/db.lck"));
    }

    #[test]
    fn panics_become_errors_instead_of_escaping() {
        let payload = std::panic::catch_unwind(|| panic!("collector exploded")).unwrap_err();
        let err = Error::from_panic(payload);
        assert!(err.technical_detail().contains("collector exploded"));
        assert_eq!(err.user_message(), "Something went wrong.");
    }

    #[test]
    fn lock_and_network_errors_are_retryable_but_conflicts_are_not() {
        assert!(Error::NetworkUnavailable {
            detail: String::new()
        }
        .is_transient());
        assert!(!Error::DependencyConflict {
            detail: String::new()
        }
        .is_transient());
    }
}
