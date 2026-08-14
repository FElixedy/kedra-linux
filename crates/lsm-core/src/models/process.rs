//! Process model. A basic manager for finding and stopping a stuck process —
//! deliberately not a replacement for `btop`.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProcessInfo {
    pub pid: u32,
    pub ppid: u32,
    /// Short name from `/proc/<pid>/stat`.
    pub name: String,
    pub uid: u32,
    pub user: Option<String>,
    /// Share of one core..all cores, as a fraction (1.0 = one core saturated).
    pub cpu_usage: f32,
    /// Resident set size in bytes.
    pub memory_rss: u64,
    pub executable: Option<String>,
    /// Full argv, joined. May be empty for kernel threads.
    pub command_line: String,
    pub state: ProcessState,
    pub threads: u32,
    pub start_time_ticks: u64,
}

impl ProcessInfo {
    /// A kernel thread has no executable and no command line.
    pub fn is_kernel_thread(&self) -> bool {
        self.command_line.is_empty() && self.executable.is_none()
    }

    /// Whether killing this process needs an explicit confirmation.
    ///
    /// Anything owned by another user (including root) or belonging to the
    /// system's core plumbing gets a confirmation dialog first.
    pub fn requires_confirmation(&self, current_uid: u32) -> bool {
        self.uid != current_uid || self.is_kernel_thread() || self.is_critical()
    }

    /// Processes whose death takes the session or the machine with them.
    pub fn is_critical(&self) -> bool {
        if self.pid == 1 {
            return true;
        }
        CRITICAL_PROCESS_NAMES.contains(&self.name.as_str())
    }
}

/// Names whose termination breaks the running session or system outright.
const CRITICAL_PROCESS_NAMES: &[&str] = &[
    "systemd",
    "systemd-journald",
    "systemd-logind",
    "systemd-udevd",
    "dbus-daemon",
    "dbus-broker",
    "gnome-shell",
    "kwin_wayland",
    "kwin_x11",
    "Xorg",
    "Xwayland",
    "plasmashell",
    "gdm",
    "sddm",
    "pipewire",
    "wireplumber",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProcessState {
    Running,
    Sleeping,
    DiskSleep,
    Stopped,
    Zombie,
    Idle,
    Unknown,
}

impl ProcessState {
    /// Parse the single-character state field from `/proc/<pid>/stat`.
    pub fn from_char(c: char) -> Self {
        match c {
            'R' => ProcessState::Running,
            'S' => ProcessState::Sleeping,
            'D' => ProcessState::DiskSleep,
            'T' | 't' => ProcessState::Stopped,
            'Z' => ProcessState::Zombie,
            'I' => ProcessState::Idle,
            _ => ProcessState::Unknown,
        }
    }

    pub fn label(&self) -> &'static str {
        match self {
            ProcessState::Running => "Running",
            ProcessState::Sleeping => "Sleeping",
            ProcessState::DiskSleep => "Waiting for disk",
            ProcessState::Stopped => "Stopped",
            ProcessState::Zombie => "Zombie",
            ProcessState::Idle => "Idle",
            ProcessState::Unknown => "Unknown",
        }
    }
}

/// How to stop a process.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SignalKind {
    /// SIGTERM — ask it to exit and clean up.
    Terminate,
    /// SIGKILL — unconditional, no cleanup.
    Kill,
}

impl SignalKind {
    pub fn number(&self) -> i32 {
        match self {
            SignalKind::Terminate => 15,
            SignalKind::Kill => 9,
        }
    }

    pub fn label(&self) -> &'static str {
        match self {
            SignalKind::Terminate => "End process",
            SignalKind::Kill => "Force quit",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn proc(pid: u32, uid: u32, name: &str, cmdline: &str) -> ProcessInfo {
        ProcessInfo {
            pid,
            ppid: 1,
            name: name.into(),
            uid,
            user: None,
            cpu_usage: 0.0,
            memory_rss: 0,
            executable: if cmdline.is_empty() {
                None
            } else {
                Some("/usr/bin/x".into())
            },
            command_line: cmdline.into(),
            state: ProcessState::Sleeping,
            threads: 1,
            start_time_ticks: 0,
        }
    }

    #[test]
    fn killing_your_own_ordinary_process_needs_no_confirmation() {
        let p = proc(2000, 1000, "firefox", "/usr/lib/firefox/firefox");
        assert!(!p.requires_confirmation(1000));
    }

    #[test]
    fn another_users_process_always_needs_confirmation() {
        let p = proc(500, 0, "sshd", "/usr/bin/sshd -D");
        assert!(p.requires_confirmation(1000));
    }

    #[test]
    fn init_and_session_critical_processes_need_confirmation_even_when_owned() {
        assert!(proc(1, 0, "systemd", "/sbin/init").is_critical());
        let shell = proc(3000, 1000, "gnome-shell", "/usr/bin/gnome-shell");
        assert!(shell.is_critical());
        assert!(
            shell.requires_confirmation(1000),
            "killing your own compositor still warrants a prompt"
        );
    }

    #[test]
    fn kernel_threads_are_recognised_and_guarded() {
        let kt = proc(42, 0, "kworker/0:1", "");
        assert!(kt.is_kernel_thread());
        assert!(kt.requires_confirmation(0));
    }

    #[test]
    fn process_states_parse_from_proc_stat_characters() {
        assert_eq!(ProcessState::from_char('R'), ProcessState::Running);
        assert_eq!(ProcessState::from_char('Z'), ProcessState::Zombie);
        assert_eq!(ProcessState::from_char('D'), ProcessState::DiskSleep);
        assert_eq!(ProcessState::from_char('t'), ProcessState::Stopped);
        assert_eq!(ProcessState::from_char('?'), ProcessState::Unknown);
    }

    #[test]
    fn signal_numbers_are_correct() {
        assert_eq!(SignalKind::Terminate.number(), 15);
        assert_eq!(SignalKind::Kill.number(), 9);
    }
}
