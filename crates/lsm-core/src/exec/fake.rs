//! Test double for [`CommandRunner`].
//!
//! `FakeRunner` answers from a fixture table and records every call, so tests
//! can assert on the *exact argument vector* a backend constructed — which is
//! how we verify that privileged commands are built correctly without ever
//! running one.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use super::{Command, CommandRunner, Output, OutputLine};
use crate::error::{Error, Result};

/// A call the runner was asked to make.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecordedCall {
    pub program: String,
    pub args: Vec<String>,
}

impl RecordedCall {
    pub fn key(&self) -> String {
        if self.args.is_empty() {
            self.program.clone()
        } else {
            format!("{} {}", self.program, self.args.join(" "))
        }
    }
}

/// Scripted responder.
#[derive(Debug, Default, Clone)]
pub struct FakeRunner {
    /// Exact `program args…` key → canned output.
    responses: Arc<Mutex<HashMap<String, Output>>>,
    /// Program name → canned output, used when no exact key matches.
    program_responses: Arc<Mutex<HashMap<String, Output>>>,
    available: Arc<Mutex<Vec<String>>>,
    calls: Arc<Mutex<Vec<RecordedCall>>>,
    /// Returned when nothing matches. Defaults to an error so a test that
    /// forgets a fixture fails loudly instead of silently seeing empty output.
    strict: Arc<Mutex<bool>>,
}

impl FakeRunner {
    pub fn new() -> Self {
        FakeRunner {
            responses: Arc::new(Mutex::new(HashMap::new())),
            program_responses: Arc::new(Mutex::new(HashMap::new())),
            available: Arc::new(Mutex::new(Vec::new())),
            calls: Arc::new(Mutex::new(Vec::new())),
            strict: Arc::new(Mutex::new(true)),
        }
    }

    /// Respond to an exact `program args…` invocation.
    pub fn with_response(self, key: &str, stdout: &str) -> Self {
        self.responses.lock().unwrap().insert(
            key.to_string(),
            Output {
                status: 0,
                stdout: stdout.to_string(),
                stderr: String::new(),
            },
        );
        self.mark_available_from_key(key);
        self
    }

    /// Respond to any invocation of `program`, whatever the arguments.
    pub fn with_program_response(self, program: &str, stdout: &str) -> Self {
        self.program_responses.lock().unwrap().insert(
            program.to_string(),
            Output {
                status: 0,
                stdout: stdout.to_string(),
                stderr: String::new(),
            },
        );
        self.available.lock().unwrap().push(program.to_string());
        self
    }

    /// Respond with a failure.
    pub fn with_failure(self, key: &str, status: i32, stderr: &str) -> Self {
        self.responses.lock().unwrap().insert(
            key.to_string(),
            Output {
                status,
                stdout: String::new(),
                stderr: stderr.to_string(),
            },
        );
        self.mark_available_from_key(key);
        self
    }

    /// Declare a program present for `which` without giving it output.
    pub fn with_program(self, program: &str) -> Self {
        self.available.lock().unwrap().push(program.to_string());
        self
    }

    /// Return empty success instead of erroring on an unmatched command.
    pub fn lenient(self) -> Self {
        *self.strict.lock().unwrap() = false;
        self
    }

    fn mark_available_from_key(&self, key: &str) {
        if let Some(program) = key.split_whitespace().next() {
            let mut a = self.available.lock().unwrap();
            if !a.iter().any(|p| p == program) {
                a.push(program.to_string());
            }
        }
    }

    /// Every call made so far, in order.
    pub fn calls(&self) -> Vec<RecordedCall> {
        self.calls.lock().unwrap().clone()
    }

    pub fn call_keys(&self) -> Vec<String> {
        self.calls().iter().map(RecordedCall::key).collect()
    }

    pub fn call_count(&self) -> usize {
        self.calls.lock().unwrap().len()
    }

    /// Whether any recorded call used exactly these arguments.
    pub fn called_with(&self, program: &str, args: &[&str]) -> bool {
        self.calls().iter().any(|c| {
            c.program == program && c.args.iter().map(String::as_str).eq(args.iter().copied())
        })
    }

    pub fn clear_calls(&self) {
        self.calls.lock().unwrap().clear();
    }

    fn lookup(&self, cmd: &Command) -> Result<Output> {
        self.calls.lock().unwrap().push(RecordedCall {
            program: cmd.program.clone(),
            args: cmd.args.clone(),
        });

        let key = cmd.key();
        if let Some(o) = self.responses.lock().unwrap().get(&key) {
            return Ok(o.clone());
        }
        if let Some(o) = self.program_responses.lock().unwrap().get(&cmd.program) {
            return Ok(o.clone());
        }
        if *self.strict.lock().unwrap() {
            Err(Error::Internal(format!(
                "FakeRunner has no fixture for `{key}`"
            )))
        } else {
            Ok(Output {
                status: 0,
                stdout: String::new(),
                stderr: String::new(),
            })
        }
    }
}

#[async_trait::async_trait]
impl CommandRunner for FakeRunner {
    async fn run(&self, cmd: Command) -> Result<Output> {
        self.lookup(&cmd)
    }

    async fn run_streaming(
        &self,
        cmd: Command,
        mut on_line: Box<dyn FnMut(OutputLine) + Send>,
    ) -> Result<i32> {
        let out = self.lookup(&cmd)?;
        for line in out.stdout.lines() {
            on_line(OutputLine::Stdout(line.to_string()));
        }
        for line in out.stderr.lines() {
            on_line(OutputLine::Stderr(line.to_string()));
        }
        Ok(out.status)
    }

    async fn which(&self, program: &str) -> bool {
        self.available.lock().unwrap().iter().any(|p| p == program)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn answers_exact_invocations_from_fixtures() {
        let r =
            FakeRunner::new().with_response("dkms status", "nvidia/570, 6.18.0, x86_64: installed");
        let out = r.run(Command::new("dkms").arg("status")).await.unwrap();
        assert!(out.stdout.contains("nvidia"));
    }

    #[tokio::test]
    async fn records_the_exact_argument_vector_for_assertions() {
        let r = FakeRunner::new().lenient();
        let _ = r
            .run(Command::new("pacman").args(["-S", "--noconfirm", "mesa"]))
            .await;

        assert!(r.called_with("pacman", &["-S", "--noconfirm", "mesa"]));
        // Argument order and content are both checked, not just presence.
        assert!(!r.called_with("pacman", &["-S", "mesa", "--noconfirm"]));
        assert_eq!(r.call_count(), 1);
    }

    #[tokio::test]
    async fn a_missing_fixture_fails_the_test_instead_of_returning_nothing() {
        let r = FakeRunner::new();
        let err = r.run(Command::new("pacman").arg("-Q")).await.unwrap_err();
        assert!(err.to_string().contains("no fixture"));
    }

    #[tokio::test]
    async fn lenient_mode_returns_empty_success() {
        let r = FakeRunner::new().lenient();
        let out = r.run(Command::new("anything")).await.unwrap();
        assert!(out.success());
        assert!(out.stdout.is_empty());
    }

    #[tokio::test]
    async fn program_level_fixtures_match_any_arguments() {
        let r = FakeRunner::new().with_program_response("flatpak", "runtime/org.gnome.Platform");
        let a = r.run(Command::new("flatpak").arg("list")).await.unwrap();
        let b = r
            .run(Command::new("flatpak").args(["remote-ls", "--updates"]))
            .await
            .unwrap();
        assert_eq!(a.stdout, b.stdout);
    }

    #[tokio::test]
    async fn which_reflects_only_declared_programs() {
        let r = FakeRunner::new().with_program("paru");
        assert!(r.which("paru").await);
        assert!(!r.which("yay").await);
    }

    #[tokio::test]
    async fn fixtures_imply_the_program_exists() {
        let r = FakeRunner::new().with_response("yay -Qua", "");
        assert!(
            r.which("yay").await,
            "declaring output for a tool implies it is installed"
        );
    }

    #[tokio::test]
    async fn streaming_replays_output_and_preserves_the_stream_tag() {
        let r =
            FakeRunner::new().with_failure("pacman -Syu", 1, "error: failed to commit transaction");
        let lines: Arc<Mutex<Vec<(bool, String)>>> = Arc::new(Mutex::new(Vec::new()));
        let sink = lines.clone();

        let status = r
            .run_streaming(
                Command::new("pacman").arg("-Syu"),
                Box::new(move |l| {
                    sink.lock()
                        .unwrap()
                        .push((l.is_stderr(), l.text().to_string()))
                }),
            )
            .await
            .unwrap();

        assert_eq!(status, 1);
        let lines = lines.lock().unwrap();
        assert_eq!(lines.len(), 1);
        assert!(lines[0].0, "the message came from stderr");
        assert!(lines[0].1.contains("failed to commit"));
    }
}
