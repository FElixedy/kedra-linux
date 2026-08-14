//! systemd unit domain model, normalized away from D-Bus string soup.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Whether a unit belongs to the system manager or the user's session manager.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UnitScope {
    System,
    User,
}

impl UnitScope {
    pub fn label(&self) -> &'static str {
        match self {
            UnitScope::System => "System",
            UnitScope::User => "User",
        }
    }
}

/// Normalized `ActiveState`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ActiveState {
    Active,
    Reloading,
    Inactive,
    Failed,
    Activating,
    Deactivating,
    Unknown,
}

impl ActiveState {
    pub fn parse(s: &str) -> Self {
        match s {
            "active" => ActiveState::Active,
            "reloading" => ActiveState::Reloading,
            "inactive" => ActiveState::Inactive,
            "failed" => ActiveState::Failed,
            "activating" => ActiveState::Activating,
            "deactivating" => ActiveState::Deactivating,
            _ => ActiveState::Unknown,
        }
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            ActiveState::Active => "active",
            ActiveState::Reloading => "reloading",
            ActiveState::Inactive => "inactive",
            ActiveState::Failed => "failed",
            ActiveState::Activating => "activating",
            ActiveState::Deactivating => "deactivating",
            ActiveState::Unknown => "unknown",
        }
    }

    pub fn label(&self) -> &'static str {
        match self {
            ActiveState::Active => "Running",
            ActiveState::Reloading => "Reloading",
            ActiveState::Inactive => "Stopped",
            ActiveState::Failed => "Failed",
            ActiveState::Activating => "Starting",
            ActiveState::Deactivating => "Stopping",
            ActiveState::Unknown => "Unknown",
        }
    }

    pub fn is_failed(&self) -> bool {
        matches!(self, ActiveState::Failed)
    }

    pub fn is_running(&self) -> bool {
        matches!(self, ActiveState::Active | ActiveState::Reloading)
    }
}

/// Normalized `UnitFileState` — whether the unit starts at boot.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum EnablementState {
    Enabled,
    EnabledRuntime,
    Disabled,
    Masked,
    MaskedRuntime,
    Static,
    Indirect,
    Generated,
    Transient,
    Alias,
    Linked,
    LinkedRuntime,
    Bad,
    Unknown,
}

impl EnablementState {
    pub fn parse(s: &str) -> Self {
        match s {
            "enabled" => EnablementState::Enabled,
            "enabled-runtime" => EnablementState::EnabledRuntime,
            "disabled" => EnablementState::Disabled,
            "masked" => EnablementState::Masked,
            "masked-runtime" => EnablementState::MaskedRuntime,
            "static" => EnablementState::Static,
            "indirect" => EnablementState::Indirect,
            "generated" => EnablementState::Generated,
            "transient" => EnablementState::Transient,
            "alias" => EnablementState::Alias,
            "linked" => EnablementState::Linked,
            "linked-runtime" => EnablementState::LinkedRuntime,
            "bad" => EnablementState::Bad,
            _ => EnablementState::Unknown,
        }
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            EnablementState::Enabled => "enabled",
            EnablementState::EnabledRuntime => "enabled-runtime",
            EnablementState::Disabled => "disabled",
            EnablementState::Masked => "masked",
            EnablementState::MaskedRuntime => "masked-runtime",
            EnablementState::Static => "static",
            EnablementState::Indirect => "indirect",
            EnablementState::Generated => "generated",
            EnablementState::Transient => "transient",
            EnablementState::Alias => "alias",
            EnablementState::Linked => "linked",
            EnablementState::LinkedRuntime => "linked-runtime",
            EnablementState::Bad => "bad",
            EnablementState::Unknown => "unknown",
        }
    }

    pub fn label(&self) -> &'static str {
        match self {
            EnablementState::Enabled => "Starts at boot",
            EnablementState::EnabledRuntime => "Starts at boot (until reboot)",
            EnablementState::Disabled => "Does not start at boot",
            EnablementState::Masked => "Masked",
            EnablementState::MaskedRuntime => "Masked (until reboot)",
            EnablementState::Static => "Started by another unit",
            EnablementState::Indirect => "Started indirectly",
            EnablementState::Generated => "Generated",
            EnablementState::Transient => "Transient",
            EnablementState::Alias => "Alias",
            EnablementState::Linked => "Linked",
            EnablementState::LinkedRuntime => "Linked (until reboot)",
            EnablementState::Bad => "Invalid",
            EnablementState::Unknown => "Unknown",
        }
    }

    /// Whether the user can meaningfully toggle enable/disable on this unit.
    pub fn is_toggleable(&self) -> bool {
        matches!(
            self,
            EnablementState::Enabled
                | EnablementState::EnabledRuntime
                | EnablementState::Disabled
                | EnablementState::Masked
                | EnablementState::MaskedRuntime
        )
    }

    pub fn is_enabled(&self) -> bool {
        matches!(
            self,
            EnablementState::Enabled | EnablementState::EnabledRuntime
        )
    }

    pub fn is_masked(&self) -> bool {
        matches!(
            self,
            EnablementState::Masked | EnablementState::MaskedRuntime
        )
    }
}

/// Summary row in the services list.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ServiceSummary {
    pub unit: String,
    pub description: String,
    pub scope: UnitScope,
    pub active_state: ActiveState,
    /// systemd's finer-grained sub-state (`running`, `exited`, `dead`…).
    pub sub_state: String,
    pub enablement: EnablementState,
}

impl ServiceSummary {
    /// Units that only exist because something generated them are noise in a
    /// browser aimed at humans.
    pub fn is_user_manageable(&self) -> bool {
        !matches!(
            self.enablement,
            EnablementState::Generated | EnablementState::Transient | EnablementState::Alias
        )
    }
}

/// Detail pane content, fetched when a service is opened.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ServiceDetails {
    pub summary: ServiceSummary,
    pub fragment_path: Option<String>,
    /// Drop-in override files, which are exactly the kind of persistent change
    /// What Changed? cares about.
    pub drop_in_paths: Vec<String>,
    pub main_pid: Option<u32>,
    pub memory_current: Option<u64>,
    pub tasks_current: Option<u64>,
    pub cpu_usage_nsec: Option<u64>,
    pub active_since: Option<DateTime<Utc>>,
    /// systemd's restart counter for the current run.
    pub n_restarts: Option<u32>,
    pub result: Option<String>,
    pub documentation: Vec<String>,
    pub triggered_by: Vec<String>,
}

impl ServiceDetails {
    pub fn uptime(&self, now: DateTime<Utc>) -> Option<chrono::Duration> {
        let since = self.active_since?;
        if !self.summary.active_state.is_running() {
            return None;
        }
        Some(now - since)
    }

    pub fn has_overrides(&self) -> bool {
        !self.drop_in_paths.is_empty()
    }
}

/// A systemd timer.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TimerInfo {
    pub unit: String,
    pub description: String,
    pub scope: UnitScope,
    /// The service this timer activates.
    pub activates: Option<String>,
    pub next_elapse: Option<DateTime<Utc>>,
    pub last_trigger: Option<DateTime<Utc>>,
    pub active_state: ActiveState,
    pub enablement: EnablementState,
}

/// A desktop autostart entry (`~/.config/autostart`, `/etc/xdg/autostart`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AutostartEntry {
    pub name: String,
    pub path: String,
    pub exec: String,
    pub comment: Option<String>,
    /// `Hidden=true` or `X-GNOME-Autostart-enabled=false`.
    pub enabled: bool,
    /// True when the entry lives in the user's own config rather than system-wide.
    pub user_scope: bool,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn active_states_normalize_from_dbus_strings() {
        assert_eq!(ActiveState::parse("active"), ActiveState::Active);
        assert_eq!(ActiveState::parse("failed"), ActiveState::Failed);
        // Anything systemd adds later must not panic or be silently misread.
        assert_eq!(ActiveState::parse("teleporting"), ActiveState::Unknown);
        assert!(ActiveState::Failed.is_failed());
        assert!(ActiveState::Active.is_running());
        assert!(!ActiveState::Inactive.is_running());
    }

    #[test]
    fn enablement_states_round_trip_and_classify() {
        for s in [
            "enabled",
            "disabled",
            "masked",
            "static",
            "enabled-runtime",
            "generated",
        ] {
            assert_eq!(EnablementState::parse(s).as_str(), s);
        }
        assert!(EnablementState::Enabled.is_enabled());
        assert!(EnablementState::EnabledRuntime.is_enabled());
        assert!(EnablementState::Masked.is_masked());
        assert!(
            !EnablementState::Static.is_toggleable(),
            "static units have nothing to toggle"
        );
        assert!(EnablementState::Disabled.is_toggleable());
    }

    #[test]
    fn generated_units_are_hidden_from_the_manageable_list() {
        let mk = |e: EnablementState| ServiceSummary {
            unit: "x.service".into(),
            description: String::new(),
            scope: UnitScope::System,
            active_state: ActiveState::Active,
            sub_state: "running".into(),
            enablement: e,
        };
        assert!(mk(EnablementState::Enabled).is_user_manageable());
        assert!(!mk(EnablementState::Generated).is_user_manageable());
        assert!(!mk(EnablementState::Transient).is_user_manageable());
    }

    #[test]
    fn uptime_is_none_for_a_stopped_service_even_with_a_timestamp() {
        let base = ServiceSummary {
            unit: "x.service".into(),
            description: String::new(),
            scope: UnitScope::System,
            active_state: ActiveState::Inactive,
            sub_state: "dead".into(),
            enablement: EnablementState::Enabled,
        };
        let now = Utc::now();
        let d = ServiceDetails {
            summary: base,
            fragment_path: None,
            drop_in_paths: vec![],
            main_pid: None,
            memory_current: None,
            tasks_current: None,
            cpu_usage_nsec: None,
            active_since: Some(now - chrono::Duration::hours(2)),
            n_restarts: None,
            result: None,
            documentation: vec![],
            triggered_by: vec![],
        };
        assert!(d.uptime(now).is_none());

        let running = ServiceDetails {
            summary: ServiceSummary {
                active_state: ActiveState::Active,
                ..d.summary.clone()
            },
            ..d
        };
        assert_eq!(running.uptime(now).unwrap().num_hours(), 2);
    }

    #[test]
    fn drop_in_overrides_are_detectable() {
        let d = ServiceDetails {
            summary: ServiceSummary {
                unit: "docker.service".into(),
                description: String::new(),
                scope: UnitScope::System,
                active_state: ActiveState::Active,
                sub_state: "running".into(),
                enablement: EnablementState::Enabled,
            },
            fragment_path: Some("/usr/lib/systemd/system/docker.service".into()),
            drop_in_paths: vec!["/etc/systemd/system/docker.service.d/override.conf".into()],
            main_pid: Some(1234),
            memory_current: None,
            tasks_current: None,
            cpu_usage_nsec: None,
            active_since: None,
            n_restarts: None,
            result: None,
            documentation: vec![],
            triggered_by: vec![],
        };
        assert!(d.has_overrides());
    }
}
