//! Boot timing and bootloader model.
//!
//! Timings come from systemd's D-Bus timestamp properties rather than from
//! parsing `systemd-analyze` output, because `--json` is not supported for
//! `time`/`blame` on current systemd and the text format is not a contract.
//! [`BootTiming::from_monotonic`] reproduces `systemd-analyze time` exactly.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Raw monotonic timestamps as systemd reports them, in microseconds.
///
/// Each is measured from the start of the *loader* epoch, except
/// `firmware`, which counts backwards from the same origin — hence the
/// subtractions in [`BootTiming::from_monotonic`].
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct MonotonicTimestamps {
    pub firmware: u64,
    pub loader: u64,
    pub initrd: u64,
    pub userspace: u64,
    pub finish: u64,
}

/// A boot's phase breakdown, all durations in microseconds.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct BootTiming {
    pub firmware_us: u64,
    pub loader_us: u64,
    pub kernel_us: u64,
    pub initrd_us: u64,
    pub userspace_us: u64,
    pub total_us: u64,
}

impl BootTiming {
    /// Derive the phase breakdown from systemd's monotonic timestamps.
    ///
    /// Mirrors `systemd-analyze time`:
    /// * firmware  = Firmware − Loader
    /// * loader    = Loader
    /// * kernel    = InitRD (or Userspace when there is no initrd)
    /// * initrd    = Userspace − InitRD
    /// * userspace = Finish − Userspace
    /// * total     = Firmware + Finish
    pub fn from_monotonic(t: MonotonicTimestamps) -> Self {
        let firmware_us = t.firmware.saturating_sub(t.loader);
        let loader_us = t.loader;
        // A system booting without an initrd reports InitRD as 0.
        let (kernel_us, initrd_us) = if t.initrd > 0 {
            (t.initrd, t.userspace.saturating_sub(t.initrd))
        } else {
            (t.userspace, 0)
        };
        let userspace_us = t.finish.saturating_sub(t.userspace);
        BootTiming {
            firmware_us,
            loader_us,
            kernel_us,
            initrd_us,
            userspace_us,
            total_us: t.firmware.saturating_add(t.finish),
        }
    }

    pub fn is_complete(&self) -> bool {
        self.total_us > 0
    }
}

/// A recorded boot, retained so later boots can be compared against it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BootRecord {
    /// systemd's 32-hex-character boot id.
    pub boot_id: String,
    pub booted_at: DateTime<Utc>,
    pub kernel_release: String,
    pub timing: BootTiming,
    /// Slowest units, already truncated to a useful number.
    pub slow_units: Vec<UnitStartupTime>,
    /// Units that were in a failed state at the end of this boot.
    pub failed_units: Vec<String>,
}

/// How long a single unit took to activate.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UnitStartupTime {
    pub unit: String,
    pub duration_us: u64,
}

/// Difference between two boots, as shown in What Changed?.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BootComparison {
    pub previous: BootRecord,
    pub current: BootRecord,
    /// Signed difference in total boot time.
    pub total_delta_us: i64,
    /// Units whose startup time changed materially, worst regression first.
    pub unit_deltas: Vec<UnitTimingDelta>,
    /// Units that failed in the current boot but not the previous one.
    pub newly_failed_units: Vec<String>,
    /// Units that failed previously and no longer do.
    pub recovered_units: Vec<String>,
}

impl BootComparison {
    pub fn kernel_changed(&self) -> bool {
        self.previous.kernel_release != self.current.kernel_release
    }

    /// Units that got slower, phrased as *possible contributors* — never as causes.
    pub fn possible_contributors(&self) -> impl Iterator<Item = &UnitTimingDelta> {
        self.unit_deltas.iter().filter(|d| d.delta_us > 0)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UnitTimingDelta {
    pub unit: String,
    pub previous_us: u64,
    pub current_us: u64,
    pub delta_us: i64,
}

/// Only report a unit's timing change when it is big enough to matter; noise
/// below this threshold would bury the real regression.
pub const UNIT_TIMING_NOISE_FLOOR_US: i64 = 200_000; // 0.2s

/// Detected bootloader. Read-only in this release.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BootloaderKind {
    SystemdBoot,
    Grub,
    Limine,
    Refind,
    Unknown,
}

impl BootloaderKind {
    pub fn label(&self) -> &'static str {
        match self {
            BootloaderKind::SystemdBoot => "systemd-boot",
            BootloaderKind::Grub => "GRUB",
            BootloaderKind::Limine => "Limine",
            BootloaderKind::Refind => "rEFInd",
            BootloaderKind::Unknown => "Unknown",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BootloaderInfo {
    pub kind: BootloaderKind,
    pub version: Option<String>,
    pub default_entry: Option<String>,
    pub timeout_seconds: Option<u32>,
    pub entries: Vec<BootEntry>,
    /// True when the system booted via UEFI.
    pub uefi: bool,
    pub secure_boot: Option<bool>,
    /// Set when we could detect the bootloader but not read its configuration
    /// (e.g. the ESP is root-only). The UI explains this rather than showing
    /// an empty list as if there were no entries.
    pub config_unreadable: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BootEntry {
    pub id: String,
    pub title: String,
    pub kernel_version: Option<String>,
    pub is_default: bool,
    pub is_selected: bool,
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Values captured from the development machine's systemd D-Bus properties,
    /// alongside what `systemd-analyze time` printed for the same boot.
    fn real_timestamps() -> MonotonicTimestamps {
        MonotonicTimestamps {
            firmware: 37_804_114,
            loader: 5_946_531,
            initrd: 1_940_300,
            userspace: 5_177_478,
            finish: 13_927_364,
        }
    }

    #[test]
    fn reproduces_systemd_analyze_time_exactly() {
        // systemd-analyze reported:
        //   31.857s (firmware) + 5.946s (loader) + 1.940s (kernel)
        //   + 3.237s (initrd) + 8.749s (userspace) = 51.731s
        let t = BootTiming::from_monotonic(real_timestamps());
        assert_eq!(t.firmware_us, 31_857_583);
        assert_eq!(t.loader_us, 5_946_531);
        assert_eq!(t.kernel_us, 1_940_300);
        assert_eq!(t.initrd_us, 3_237_178);
        assert_eq!(t.userspace_us, 8_749_886);
        assert_eq!(t.total_us, 51_731_478);

        // Rounded to milliseconds, each phase matches the printed figure.
        let ms = |us: u64| (us as f64 / 1000.0).round() as u64;
        assert_eq!(ms(t.firmware_us), 31_858);
        assert_eq!(ms(t.userspace_us), 8_750);
        assert_eq!(ms(t.total_us), 51_731);
    }

    #[test]
    fn handles_a_system_with_no_initrd() {
        let t = BootTiming::from_monotonic(MonotonicTimestamps {
            firmware: 3_000_000,
            loader: 1_000_000,
            initrd: 0,
            userspace: 2_000_000,
            finish: 9_000_000,
        });
        assert_eq!(t.initrd_us, 0);
        assert_eq!(
            t.kernel_us, 2_000_000,
            "without an initrd, kernel runs until userspace"
        );
        assert_eq!(t.userspace_us, 7_000_000);
    }

    #[test]
    fn never_underflows_on_inconsistent_timestamps() {
        // A container or an incomplete boot can report zeroes; this must not panic.
        let t = BootTiming::from_monotonic(MonotonicTimestamps {
            firmware: 0,
            loader: 5_000_000,
            initrd: 0,
            userspace: 0,
            finish: 0,
        });
        assert_eq!(t.firmware_us, 0);
        assert!(!t.is_complete());
    }

    fn record(id: &str, kernel: &str, total: u64) -> BootRecord {
        BootRecord {
            boot_id: id.into(),
            booted_at: Utc::now(),
            kernel_release: kernel.into(),
            timing: BootTiming {
                total_us: total,
                ..Default::default()
            },
            slow_units: vec![],
            failed_units: vec![],
        }
    }

    #[test]
    fn comparison_reports_slowdown_and_kernel_switch_without_claiming_causality() {
        let cmp = BootComparison {
            previous: record("b1", "6.17.9-cachyos", 7_100_000),
            current: record("b2", "6.18.0-cachyos", 11_400_000),
            total_delta_us: 4_300_000,
            unit_deltas: vec![
                UnitTimingDelta {
                    unit: "NetworkManager-wait-online.service".into(),
                    previous_us: 800_000,
                    current_us: 4_200_000,
                    delta_us: 3_400_000,
                },
                UnitTimingDelta {
                    unit: "fast.service".into(),
                    previous_us: 900_000,
                    current_us: 500_000,
                    delta_us: -400_000,
                },
            ],
            newly_failed_units: vec!["bluetooth.service".into()],
            recovered_units: vec![],
        };
        assert!(cmp.kernel_changed());
        let contributors: Vec<_> = cmp
            .possible_contributors()
            .map(|d| d.unit.as_str())
            .collect();
        assert_eq!(contributors, vec!["NetworkManager-wait-online.service"]);
        assert_eq!(cmp.newly_failed_units, vec!["bluetooth.service"]);
    }
}
