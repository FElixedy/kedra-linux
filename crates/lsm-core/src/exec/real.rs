//! The production [`CommandRunner`], backed by `tokio::process`.

use std::process::Stdio;

use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command as TokioCommand;

use super::{Command, CommandRunner, Output, OutputLine};
use crate::error::{Error, Result};

/// Executes commands as real child processes.
#[derive(Debug, Default, Clone)]
pub struct RealRunner;

impl RealRunner {
    pub fn new() -> Self {
        RealRunner
    }

    /// Build the tokio command. Note `TokioCommand::new` takes the program and
    /// arguments separately and never involves a shell.
    fn build(cmd: &Command) -> TokioCommand {
        let mut c = TokioCommand::new(&cmd.program);
        c.args(&cmd.args);
        for (k, v) in &cmd.env {
            c.env(k, v);
        }
        for k in &cmd.env_remove {
            c.env_remove(k);
        }
        // Never let a child inherit our stdin; an unexpected password prompt
        // would otherwise hang the process invisibly.
        c.stdin(Stdio::null());
        c.kill_on_drop(true);
        c
    }

    fn map_spawn_error(cmd: &Command, e: std::io::Error) -> Error {
        match e.kind() {
            std::io::ErrorKind::NotFound => Error::ToolMissing {
                program: cmd.program.clone(),
            },
            std::io::ErrorKind::PermissionDenied => {
                Error::PermissionDenied(format!("cannot execute {}", cmd.program))
            }
            _ => Error::Internal(format!("failed to start {}: {e}", cmd.program)),
        }
    }
}

#[async_trait::async_trait]
impl CommandRunner for RealRunner {
    async fn run(&self, cmd: Command) -> Result<Output> {
        let mut c = Self::build(&cmd);
        c.stdout(Stdio::piped()).stderr(Stdio::piped());

        let child = c.spawn().map_err(|e| Self::map_spawn_error(&cmd, e))?;

        let output = match cmd.timeout_secs {
            Some(secs) => {
                let dur = std::time::Duration::from_secs(secs);
                match tokio::time::timeout(dur, child.wait_with_output()).await {
                    Ok(r) => r,
                    Err(_) => {
                        return Err(Error::CommandFailed {
                            program: cmd.program.clone(),
                            status: -1,
                            stderr: format!("timed out after {secs}s"),
                        })
                    }
                }
            }
            None => child.wait_with_output().await,
        }
        .map_err(|e| Error::Internal(format!("{} failed: {e}", cmd.program)))?;

        Ok(Output {
            // A process killed by a signal has no exit code; report it as -1
            // rather than silently succeeding.
            status: output.status.code().unwrap_or(-1),
            stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        })
    }

    async fn run_streaming(
        &self,
        cmd: Command,
        mut on_line: Box<dyn FnMut(OutputLine) + Send>,
    ) -> Result<i32> {
        let mut c = Self::build(&cmd);
        c.stdout(Stdio::piped()).stderr(Stdio::piped());

        let mut child = c.spawn().map_err(|e| Self::map_spawn_error(&cmd, e))?;

        let stdout = child.stdout.take().expect("stdout was piped");
        let stderr = child.stderr.take().expect("stderr was piped");

        // Interleave both streams so ordering is preserved as closely as the
        // pipes allow, and neither can block the other by filling its buffer.
        let (tx, mut rx) = tokio::sync::mpsc::channel::<OutputLine>(256);
        let tx_err = tx.clone();

        let stdout_task = tokio::spawn(async move {
            let mut lines = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if tx.send(OutputLine::Stdout(line)).await.is_err() {
                    break;
                }
            }
        });
        let stderr_task = tokio::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if tx_err.send(OutputLine::Stderr(line)).await.is_err() {
                    break;
                }
            }
        });

        let pump = async {
            while let Some(line) = rx.recv().await {
                on_line(line);
            }
        };

        let wait = async {
            let status = child.wait().await;
            let _ = stdout_task.await;
            let _ = stderr_task.await;
            status
        };

        let (status, ()) = tokio::join!(wait, pump);
        let status = status.map_err(|e| Error::Internal(format!("{} failed: {e}", cmd.program)))?;
        Ok(status.code().unwrap_or(-1))
    }

    async fn which(&self, program: &str) -> bool {
        // Resolve against PATH ourselves rather than shelling out to `which`,
        // which may not be installed.
        let Some(path) = std::env::var_os("PATH") else {
            return false;
        };
        for dir in std::env::split_paths(&path) {
            let candidate = dir.join(program);
            if let Ok(meta) = tokio::fs::metadata(&candidate).await {
                if meta.is_file() {
                    use std::os::unix::fs::PermissionsExt;
                    if meta.permissions().mode() & 0o111 != 0 {
                        return true;
                    }
                }
            }
        }
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// These tests run only trivial, always-present coreutils. They never touch
    /// package or service state.
    #[tokio::test]
    async fn runs_a_command_and_captures_stdout() {
        let r = RealRunner::new();
        let out = r
            .run(Command::new("echo").arg("hello world"))
            .await
            .unwrap();
        assert!(out.success());
        assert_eq!(out.stdout.trim(), "hello world");
    }

    #[tokio::test]
    async fn arguments_are_not_interpreted_by_a_shell() {
        let r = RealRunner::new();
        // If this went through a shell, the semicolon would start a second
        // command and the output would be two lines.
        let out = r
            .run(Command::new("echo").arg("a; echo INJECTED"))
            .await
            .unwrap();
        assert_eq!(out.stdout.trim(), "a; echo INJECTED");
        assert_eq!(
            out.stdout.lines().count(),
            1,
            "a second command must not have run"
        );
    }

    #[tokio::test]
    async fn a_missing_program_is_a_structured_error_not_a_panic() {
        let r = RealRunner::new();
        let err = r
            .run(Command::new("lsm-definitely-not-a-real-program"))
            .await
            .unwrap_err();
        assert!(matches!(err, Error::ToolMissing { .. }));
    }

    #[tokio::test]
    async fn nonzero_exit_is_reported_with_stderr_intact() {
        let r = RealRunner::new();
        let out = r.run(Command::new("false")).await.unwrap();
        assert!(!out.success());
        assert_ne!(out.status, 0);
    }

    #[tokio::test]
    async fn a_hung_command_is_killed_by_its_timeout() {
        let r = RealRunner::new();
        let err = r
            .run(Command::new("sleep").arg("30").timeout_secs(1))
            .await
            .unwrap_err();
        match err {
            Error::CommandFailed { stderr, .. } => assert!(stderr.contains("timed out")),
            other => panic!("expected a timeout, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn streaming_delivers_lines_from_both_streams() {
        use std::sync::{Arc, Mutex};
        let r = RealRunner::new();
        let collected = Arc::new(Mutex::new(Vec::new()));
        let sink = collected.clone();

        let status = r
            .run_streaming(
                Command::new("printf").arg("one\ntwo\nthree\n"),
                Box::new(move |line| sink.lock().unwrap().push(line.text().to_string())),
            )
            .await
            .unwrap();

        assert_eq!(status, 0);
        assert_eq!(*collected.lock().unwrap(), vec!["one", "two", "three"]);
    }

    #[tokio::test]
    async fn which_finds_real_programs_and_rejects_imaginary_ones() {
        let r = RealRunner::new();
        assert!(r.which("sh").await);
        assert!(!r.which("lsm-definitely-not-a-real-program").await);
    }
}
