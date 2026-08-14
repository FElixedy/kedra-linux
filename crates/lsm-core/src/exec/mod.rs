//! Command execution abstraction.
//!
//! Every external program this crate runs goes through [`CommandRunner`]. That
//! gives us three things:
//!
//! 1. **Testability** — tests inject [`FakeRunner`] with recorded fixtures, so
//!    the suite never touches pacman, systemd, or the developer's machine.
//! 2. **Safety** — [`Command`] holds `program` plus an argument *vector*. There
//!    is no string-command constructor and no shell, so argument injection is
//!    not expressible.
//! 3. **Streaming** — long operations report output line by line without
//!    blocking the UI thread.

mod fake;
mod real;

pub use fake::{FakeRunner, RecordedCall};
pub use real::RealRunner;

use std::collections::HashMap;
use std::fmt;
use std::sync::Arc;

use crate::error::{Error, Result};

/// A program invocation.
///
/// Note there is deliberately no `Command::from_shell(&str)`: constructing a
/// command from a string is the bug class this type exists to prevent.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Command {
    pub program: String,
    pub args: Vec<String>,
    /// Extra environment variables. The child otherwise inherits ours.
    pub env: HashMap<String, String>,
    /// Environment variables to remove from the child.
    pub env_remove: Vec<String>,
    /// Kill the command after this many seconds. `None` means no timeout, which
    /// is only appropriate for user-initiated transactions.
    pub timeout_secs: Option<u64>,
}

impl Command {
    pub fn new(program: impl Into<String>) -> Self {
        Command {
            program: program.into(),
            args: Vec::new(),
            env: HashMap::new(),
            env_remove: Vec::new(),
            timeout_secs: Some(DEFAULT_TIMEOUT_SECS),
        }
    }

    pub fn arg(mut self, a: impl Into<String>) -> Self {
        self.args.push(a.into());
        self
    }

    pub fn args<I, S>(mut self, args: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        self.args.extend(args.into_iter().map(Into::into));
        self
    }

    pub fn env(mut self, k: impl Into<String>, v: impl Into<String>) -> Self {
        self.env.insert(k.into(), v.into());
        self
    }

    pub fn env_remove(mut self, k: impl Into<String>) -> Self {
        self.env_remove.push(k.into());
        self
    }

    pub fn timeout_secs(mut self, s: u64) -> Self {
        self.timeout_secs = Some(s);
        self
    }

    /// Remove the timeout. Only for transactions the user explicitly started
    /// and can watch: a large system upgrade legitimately takes many minutes.
    pub fn no_timeout(mut self) -> Self {
        self.timeout_secs = None;
        self
    }

    /// Force machine-readable, locale-independent output. Parsers depend on
    /// this: a translated `pacman` or `systemctl` would otherwise break them.
    pub fn c_locale(self) -> Self {
        self.env("LC_ALL", "C").env("LANG", "C").env("LANGUAGE", "")
    }

    /// A stable key for fixture lookup and logging.
    pub fn key(&self) -> String {
        if self.args.is_empty() {
            self.program.clone()
        } else {
            format!("{} {}", self.program, self.args.join(" "))
        }
    }
}

impl fmt::Display for Command {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.key())
    }
}

/// Default ceiling for informational commands, so a hung tool cannot wedge a page.
pub const DEFAULT_TIMEOUT_SECS: u64 = 30;

/// Result of a completed command.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Output {
    pub status: i32,
    pub stdout: String,
    pub stderr: String,
}

impl Output {
    pub fn success(&self) -> bool {
        self.status == 0
    }

    /// Return stdout, or a structured error carrying stderr.
    pub fn ok_stdout(self, program: &str) -> Result<String> {
        if self.success() {
            Ok(self.stdout)
        } else {
            Err(Error::CommandFailed {
                program: program.to_string(),
                status: self.status,
                stderr: self.stderr,
            })
        }
    }

    pub fn stdout_lines(&self) -> impl Iterator<Item = &str> {
        self.stdout.lines().filter(|l| !l.trim().is_empty())
    }
}

/// A line of streamed output, tagged with the stream it came from.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OutputLine {
    Stdout(String),
    Stderr(String),
}

impl OutputLine {
    pub fn text(&self) -> &str {
        match self {
            OutputLine::Stdout(s) | OutputLine::Stderr(s) => s,
        }
    }

    pub fn is_stderr(&self) -> bool {
        matches!(self, OutputLine::Stderr(_))
    }
}

/// Runs external programs.
///
/// Implementors must never interpret `program` or `args` through a shell.
#[async_trait::async_trait]
pub trait CommandRunner: Send + Sync + 'static {
    /// Run to completion and collect all output.
    async fn run(&self, cmd: Command) -> Result<Output>;

    /// Run, delivering output lines as they arrive. Returns the exit status.
    ///
    /// `on_line` is called from the runner's task, not the UI thread.
    async fn run_streaming(
        &self,
        cmd: Command,
        on_line: Box<dyn FnMut(OutputLine) + Send>,
    ) -> Result<i32>;

    /// Whether a program exists in `PATH`. Used for optional tools (AUR
    /// helpers, flatpak, dkms) so their absence degrades instead of erroring.
    async fn which(&self, program: &str) -> bool;
}

/// Shared handle to the process runner.
///
/// Backends take one of these rather than reaching for `std::process`
/// themselves, which is what makes the whole crate testable offline.
pub type SharedRunner = Arc<dyn CommandRunner>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn commands_carry_an_argument_vector_never_a_shell_string() {
        let c = Command::new("pacman").arg("-Qi").arg("mesa");
        assert_eq!(c.program, "pacman");
        assert_eq!(c.args, vec!["-Qi", "mesa"]);

        // An argument containing shell metacharacters stays one opaque argument.
        let hostile = Command::new("pacman").arg("-Qi").arg("mesa; rm -rf /");
        assert_eq!(hostile.args[1], "mesa; rm -rf /");
        assert_eq!(hostile.args.len(), 2, "no splitting, no interpretation");
    }

    #[test]
    fn c_locale_is_forced_so_parsers_do_not_break_on_translated_output() {
        let c = Command::new("systemctl").c_locale();
        assert_eq!(c.env.get("LC_ALL").map(String::as_str), Some("C"));
        assert_eq!(c.env.get("LANG").map(String::as_str), Some("C"));
    }

    #[test]
    fn informational_commands_time_out_by_default_but_transactions_can_opt_out() {
        assert_eq!(
            Command::new("pacman").timeout_secs,
            Some(DEFAULT_TIMEOUT_SECS)
        );
        assert_eq!(Command::new("pacman").no_timeout().timeout_secs, None);
        assert_eq!(Command::new("pacman").timeout_secs(5).timeout_secs, Some(5));
    }

    #[test]
    fn failed_output_becomes_a_structured_error_carrying_stderr() {
        let out = Output {
            status: 1,
            stdout: String::new(),
            stderr: "error: target not found: nope".into(),
        };
        let err = out.ok_stdout("pacman").unwrap_err();
        match err {
            Error::CommandFailed {
                program,
                status,
                stderr,
            } => {
                assert_eq!(program, "pacman");
                assert_eq!(status, 1);
                assert!(stderr.contains("target not found"));
            }
            other => panic!("unexpected error: {other:?}"),
        }
    }

    #[test]
    fn successful_output_yields_stdout_and_skips_blank_lines() {
        let out = Output {
            status: 0,
            stdout: "a\n\nb\n  \nc\n".into(),
            stderr: String::new(),
        };
        assert_eq!(out.stdout_lines().collect::<Vec<_>>(), vec!["a", "b", "c"]);
        assert_eq!(out.clone().ok_stdout("x").unwrap(), "a\n\nb\n  \nc\n");
    }

    #[test]
    fn command_key_is_stable_for_fixture_lookup() {
        assert_eq!(Command::new("dkms").arg("status").key(), "dkms status");
        assert_eq!(Command::new("flatpak").key(), "flatpak");
    }
}
