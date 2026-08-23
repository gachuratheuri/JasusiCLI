use std::env;
use std::io;
use std::process::{Command, Stdio};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tokio::process::Command as TokioCommand;
use tokio::runtime::Builder;
use tokio::time::timeout;

use crate::sandbox::{
    build_linux_sandbox_command, resolve_sandbox_status_for_request, unsafe_local_mode,
    validate_execution_allowed, FilesystemIsolationMode, SandboxConfig, SandboxStatus,
};
use crate::ConfigLoader;

/// Bound on how long process-tree termination may take before we give up waiting.
const TREE_KILL_GRACE: Duration = Duration::from_secs(5);

/// Input schema for the built-in bash execution tool.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct BashCommandInput {
    pub command: String,
    pub timeout: Option<u64>,
    pub description: Option<String>,
    #[serde(rename = "run_in_background")]
    pub run_in_background: Option<bool>,
    #[serde(rename = "dangerouslyDisableSandbox")]
    pub dangerously_disable_sandbox: Option<bool>,
    #[serde(rename = "namespaceRestrictions")]
    pub namespace_restrictions: Option<bool>,
    #[serde(rename = "isolateNetwork")]
    pub isolate_network: Option<bool>,
    #[serde(rename = "filesystemMode")]
    pub filesystem_mode: Option<FilesystemIsolationMode>,
    #[serde(rename = "allowedMounts")]
    pub allowed_mounts: Option<Vec<String>>,
}

/// Output returned from a bash tool invocation.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct BashCommandOutput {
    pub stdout: String,
    pub stderr: String,
    #[serde(rename = "rawOutputPath")]
    pub raw_output_path: Option<String>,
    pub interrupted: bool,
    #[serde(rename = "isImage")]
    pub is_image: Option<bool>,
    #[serde(rename = "backgroundTaskId")]
    pub background_task_id: Option<String>,
    #[serde(rename = "backgroundedByUser")]
    pub backgrounded_by_user: Option<bool>,
    #[serde(rename = "assistantAutoBackgrounded")]
    pub assistant_auto_backgrounded: Option<bool>,
    #[serde(rename = "dangerouslyDisableSandbox")]
    pub dangerously_disable_sandbox: Option<bool>,
    #[serde(rename = "returnCodeInterpretation")]
    pub return_code_interpretation: Option<String>,
    #[serde(rename = "noOutputExpected")]
    pub no_output_expected: Option<bool>,
    #[serde(rename = "structuredContent")]
    pub structured_content: Option<Vec<serde_json::Value>>,
    #[serde(rename = "persistedOutputPath")]
    pub persisted_output_path: Option<String>,
    #[serde(rename = "persistedOutputSize")]
    pub persisted_output_size: Option<u64>,
    #[serde(rename = "sandboxStatus")]
    pub sandbox_status: Option<SandboxStatus>,
}

/// Executes a shell command with the requested sandbox settings.
pub fn execute_bash(input: BashCommandInput) -> io::Result<BashCommandOutput> {
    let cwd = env::current_dir()?;
    let sandbox_status = sandbox_status_for_input(&input, &cwd);

    // F05: fail closed. Shell execution is denied when no effective OS isolation is
    // available unless the operator explicitly opted into unsafe local mode.
    validate_execution_allowed(&sandbox_status, unsafe_local_mode(), true)
        .map_err(io::Error::other)?;

    if input.run_in_background.unwrap_or(false) {
        let mut child = prepare_command(&input.command, &cwd, &sandbox_status, false);
        let child = child
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()?;

        return Ok(BashCommandOutput {
            stdout: String::new(),
            stderr: String::new(),
            raw_output_path: None,
            interrupted: false,
            is_image: None,
            background_task_id: Some(child.id().to_string()),
            backgrounded_by_user: Some(false),
            assistant_auto_backgrounded: Some(false),
            dangerously_disable_sandbox: input.dangerously_disable_sandbox,
            return_code_interpretation: None,
            no_output_expected: Some(true),
            structured_content: None,
            persisted_output_path: None,
            persisted_output_size: None,
            sandbox_status: Some(sandbox_status),
        });
    }

    let runtime = Builder::new_current_thread().enable_all().build()?;
    runtime.block_on(execute_bash_async(input, sandbox_status, cwd))
}

async fn execute_bash_async(
    input: BashCommandInput,
    sandbox_status: SandboxStatus,
    cwd: std::path::PathBuf,
) -> io::Result<BashCommandOutput> {
    let mut command = prepare_tokio_command(&input.command, &cwd, &sandbox_status, true);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    // Defence in depth only: kill_on_drop reaps the direct child, never the tree.
    command.kill_on_drop(true);
    // Put the child in its own process group so the whole tree can be signalled.
    #[cfg(unix)]
    command.process_group(0);

    let child = command.spawn()?;
    let child_pid = child.id();

    // Held for the lifetime of the run. On drop, every descendant is killed —
    // including any that detached from the parent/child tree.
    #[cfg(windows)]
    let _job_guard = contain_in_job_object(&child);

    let output_result = if let Some(timeout_ms) = input.timeout {
        if let Ok(result) =
            timeout(Duration::from_millis(timeout_ms), child.wait_with_output()).await
        {
            (result?, false)
        } else {
            {
                // F08: terminate and reap the entire descendant tree, not just the
                // direct child. Dropping the future alone orphans grandchildren.
                terminate_process_tree(child_pid).await;
                return Ok(BashCommandOutput {
                    stdout: String::new(),
                    stderr: format!("Command exceeded timeout of {timeout_ms} ms"),
                    raw_output_path: None,
                    interrupted: true,
                    is_image: None,
                    background_task_id: None,
                    backgrounded_by_user: None,
                    assistant_auto_backgrounded: None,
                    dangerously_disable_sandbox: input.dangerously_disable_sandbox,
                    return_code_interpretation: Some(String::from("timeout")),
                    no_output_expected: Some(true),
                    structured_content: None,
                    persisted_output_path: None,
                    persisted_output_size: None,
                    sandbox_status: Some(sandbox_status),
                });
            }
        }
    } else {
        (child.wait_with_output().await?, false)
    };

    let (output, interrupted) = output_result;
    let stdout = truncate_output(&String::from_utf8_lossy(&output.stdout));
    let stderr = truncate_output(&String::from_utf8_lossy(&output.stderr));
    let no_output_expected = Some(stdout.trim().is_empty() && stderr.trim().is_empty());
    let return_code_interpretation = output.status.code().and_then(|code| {
        if code == 0 {
            None
        } else {
            Some(format!("exit_code:{code}"))
        }
    });

    Ok(BashCommandOutput {
        stdout,
        stderr,
        raw_output_path: None,
        interrupted,
        is_image: None,
        background_task_id: None,
        backgrounded_by_user: None,
        assistant_auto_backgrounded: None,
        dangerously_disable_sandbox: input.dangerously_disable_sandbox,
        return_code_interpretation,
        no_output_expected,
        structured_content: None,
        persisted_output_path: None,
        persisted_output_size: None,
        sandbox_status: Some(sandbox_status),
    })
}

fn sandbox_status_for_input(input: &BashCommandInput, cwd: &std::path::Path) -> SandboxStatus {
    let config = ConfigLoader::default_for(cwd).load().map_or_else(
        |_| SandboxConfig::default(),
        |runtime_config| runtime_config.sandbox().clone(),
    );
    let request = config.resolve_request(
        input.dangerously_disable_sandbox.map(|disabled| !disabled),
        input.namespace_restrictions,
        input.isolate_network,
        input.filesystem_mode,
        input.allowed_mounts.clone(),
    );
    resolve_sandbox_status_for_request(&request, cwd)
}

fn prepare_command(
    command: &str,
    cwd: &std::path::Path,
    sandbox_status: &SandboxStatus,
    create_dirs: bool,
) -> Command {
    if create_dirs {
        prepare_sandbox_dirs(cwd);
    }

    if let Some(launcher) = build_linux_sandbox_command(command, cwd, sandbox_status) {
        let mut prepared = Command::new(launcher.program);
        prepared.args(launcher.args);
        prepared.current_dir(cwd);
        prepared.envs(launcher.env);
        return prepared;
    }

    let (shell, flag) = if cfg!(windows) {
        ("cmd", "/C")
    } else {
        ("sh", "-lc")
    };
    let mut prepared = Command::new(shell);
    prepared.arg(flag).arg(command).current_dir(cwd);
    if sandbox_status.filesystem_active {
        prepared.env("HOME", cwd.join(".sandbox-home"));
        prepared.env("TMPDIR", cwd.join(".sandbox-tmp"));
    }
    prepared
}

fn prepare_tokio_command(
    command: &str,
    cwd: &std::path::Path,
    sandbox_status: &SandboxStatus,
    create_dirs: bool,
) -> TokioCommand {
    if create_dirs {
        prepare_sandbox_dirs(cwd);
    }

    if let Some(launcher) = build_linux_sandbox_command(command, cwd, sandbox_status) {
        let mut prepared = TokioCommand::new(launcher.program);
        prepared.args(launcher.args);
        prepared.current_dir(cwd);
        prepared.envs(launcher.env);
        return prepared;
    }

    let (shell, flag) = if cfg!(windows) {
        ("cmd", "/C")
    } else {
        ("sh", "-lc")
    };
    let mut prepared = TokioCommand::new(shell);
    prepared.arg(flag).arg(command).current_dir(cwd);
    if sandbox_status.filesystem_active {
        prepared.env("HOME", cwd.join(".sandbox-home"));
        prepared.env("TMPDIR", cwd.join(".sandbox-tmp"));
    }
    prepared
}

/// Windows containment for a spawned child and all of its descendants.
///
/// `taskkill /T` walks the *current* parent/child tree, so it cannot reach a
/// process that detached itself (`start /b`) and whose intermediate parent has
/// already exited. A Job Object has no such gap: descendants inherit job
/// membership, and closing the job with `KILL_ON_JOB_CLOSE` terminates all of
/// them. Dropping the returned guard kills the job.
///
/// There is a small window between `spawn` and assignment; eliminating it
/// requires `CREATE_SUSPENDED`, which needs `unsafe` and this workspace forbids
/// it. `taskkill` remains as a secondary sweep.
#[cfg(windows)]
fn contain_in_job_object(child: &tokio::process::Child) -> Option<win32job::Job> {
    let handle = child.raw_handle()?;

    let job = win32job::Job::create().ok()?;
    let mut info = job.query_extended_limit_info().ok()?;
    info.limit_kill_on_job_close();
    job.set_extended_limit_info(&info).ok()?;
    job.assign_process(handle as isize).ok()?;
    Some(job)
}

/// Terminate a child and every descendant it spawned.
///
/// `kill_on_drop` and `Child::kill` only reach the direct child; a shell that has
/// forked background work leaves those descendants running. On Unix the child is
/// placed in its own process group (see `execute_bash_async`) so a negative PID
/// signals the whole group. On Windows the Job Object above is authoritative and
/// `taskkill /T` is a secondary sweep.
///
/// The kill itself is bounded so a wedged reaper cannot block the caller.
async fn terminate_process_tree(pid: Option<u32>) {
    let Some(pid) = pid else {
        return;
    };

    #[cfg(unix)]
    let mut killer = {
        let mut cmd = TokioCommand::new("kill");
        cmd.arg("-KILL").arg(format!("-{pid}"));
        cmd
    };

    #[cfg(windows)]
    let mut killer = {
        let mut cmd = TokioCommand::new("taskkill");
        cmd.arg("/T").arg("/F").arg("/PID").arg(pid.to_string());
        cmd
    };

    killer
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .kill_on_drop(true);

    if let Ok(mut child) = killer.spawn() {
        // Reap the reaper; ignore failure (the tree may already be gone).
        let _ = timeout(TREE_KILL_GRACE, child.wait()).await;
    }
}

fn prepare_sandbox_dirs(cwd: &std::path::Path) {
    let _ = std::fs::create_dir_all(cwd.join(".sandbox-home"));
    let _ = std::fs::create_dir_all(cwd.join(".sandbox-tmp"));
}

#[cfg(test)]
mod tests {
    use std::sync::{Mutex, MutexGuard, OnceLock};

    use super::{execute_bash, BashCommandInput};
    use crate::sandbox::{set_unsafe_local_mode, FilesystemIsolationMode};

    /// The unsafe-local-mode opt-in is process-wide by design: it is an operator
    /// decision, not a per-call argument. Tests that read or write it must
    /// therefore not run concurrently.
    fn exclusive() -> MutexGuard<'static, ()> {
        static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| Mutex::new(()))
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// Most tests exercise behaviour *after* the fail-closed gate. CI hosts have no
    /// effective sandbox, so they must opt in explicitly — exactly as an operator
    /// would. `fails_closed_without_sandbox_or_opt_in` covers the gate itself.
    fn allow_unsandboxed_execution() {
        set_unsafe_local_mode(true);
    }

    #[test]
    fn fails_closed_without_sandbox_or_opt_in() {
        let _guard = exclusive();
        set_unsafe_local_mode(false);
        std::env::remove_var("JASUSI_UNSAFE_LOCAL_MODE");

        let cwd = std::env::current_dir().expect("cwd");
        let status = crate::resolve_sandbox_status(&crate::SandboxConfig::default(), &cwd);

        let result = execute_bash(BashCommandInput {
            command: String::from("echo should-not-run"),
            timeout: Some(1_000),
            description: None,
            run_in_background: Some(false),
            dangerously_disable_sandbox: Some(false),
            namespace_restrictions: Some(true),
            isolate_network: Some(false),
            filesystem_mode: Some(FilesystemIsolationMode::WorkspaceOnly),
            allowed_mounts: None,
        });

        if status.active {
            // A genuinely sandboxed host is allowed to run the command.
            assert!(result.is_ok(), "sandboxed host should permit execution");
        } else {
            let error = result.expect_err("unsandboxed execution must be denied");
            assert!(
                error.to_string().contains("Security Denial"),
                "unexpected error: {error}"
            );
        }
    }

    #[test]
    fn executes_simple_command() {
        let _guard = exclusive();
        allow_unsandboxed_execution();
        let output = execute_bash(BashCommandInput {
            command: String::from("echo hello"),
            timeout: Some(1_000),
            description: None,
            run_in_background: Some(false),
            dangerously_disable_sandbox: Some(false),
            namespace_restrictions: Some(false),
            isolate_network: Some(false),
            filesystem_mode: Some(FilesystemIsolationMode::WorkspaceOnly),
            allowed_mounts: None,
        })
        .expect("bash command should execute");

        assert_eq!(output.stdout.trim(), "hello");
        assert!(!output.interrupted);
        assert!(output.sandbox_status.is_some());
    }

    #[test]
    fn disables_sandbox_when_requested() {
        let _guard = exclusive();
        allow_unsandboxed_execution();
        let output = execute_bash(BashCommandInput {
            command: String::from("echo hello"),
            timeout: Some(1_000),
            description: None,
            run_in_background: Some(false),
            dangerously_disable_sandbox: Some(true),
            namespace_restrictions: None,
            isolate_network: None,
            filesystem_mode: None,
            allowed_mounts: None,
        })
        .expect("bash command should execute");

        assert!(!output.sandbox_status.expect("sandbox status").enabled);
    }

    /// F08: a timeout must terminate the whole descendant tree, not just the shell.
    ///
    /// The command spawns a descendant that writes a marker file well after the
    /// timeout fires. If any descendant survives cancellation, the marker appears.
    #[test]
    fn timeout_terminates_the_entire_process_tree() {
        let _guard = exclusive();
        allow_unsandboxed_execution();

        let dir = tempfile::tempdir().expect("temp dir");
        let marker = dir.path().join("survivor");
        let marker_display = marker.display().to_string();

        // The descendant is driven from a script file rather than an inline string:
        // Rust's Windows argument escaping mangles nested quotes passed to cmd.exe.
        #[cfg(unix)]
        let (script, command) = {
            let script = dir.path().join("spawn.sh");
            std::fs::write(
                &script,
                format!("(sleep 6; echo alive > '{marker_display}') &\nsleep 60\n"),
            )
            .expect("write script");
            (script.clone(), format!("sh '{}'", script.display()))
        };
        #[cfg(windows)]
        let (script, command) = {
            let script = dir.path().join("spawn.bat");
            std::fs::write(
                &script,
                format!(
                    "@echo off\r\nstart \"\" /b cmd /c \"ping -n 7 127.0.0.1 >nul & echo alive>\"\"{marker_display}\"\"\"\r\nping -n 60 127.0.0.1 >nul\r\n"
                ),
            )
            .expect("write script");
            // No quotes: Rust escapes inner quotes as \" which cmd.exe cannot parse.
            (script.clone(), script.display().to_string())
        };
        assert!(
            !script.display().to_string().contains(' '),
            "test requires a space-free temp path; got {}",
            script.display()
        );

        let output = execute_bash(BashCommandInput {
            command,
            timeout: Some(1_000),
            description: None,
            run_in_background: Some(false),
            dangerously_disable_sandbox: Some(true),
            namespace_restrictions: Some(false),
            isolate_network: Some(false),
            filesystem_mode: Some(FilesystemIsolationMode::Off),
            allowed_mounts: None,
        })
        .expect("bash command should return a timeout result");

        assert!(output.interrupted, "command should report interruption");
        assert_eq!(
            output.return_code_interpretation.as_deref(),
            Some("timeout")
        );

        // Outlive the descendant's own delay; if it was orphaned it writes by now.
        std::thread::sleep(std::time::Duration::from_secs(10));

        assert!(
            !marker.exists(),
            "descendant process survived cancellation and wrote {}",
            marker.display()
        );
    }
}

/// Maximum output bytes before truncation (16 KiB, matching upstream).
const MAX_OUTPUT_BYTES: usize = 16_384;

/// Truncate output to `MAX_OUTPUT_BYTES`, appending a marker when trimmed.
fn truncate_output(s: &str) -> String {
    if s.len() <= MAX_OUTPUT_BYTES {
        return s.to_string();
    }
    // Find the last valid UTF-8 boundary at or before MAX_OUTPUT_BYTES
    let mut end = MAX_OUTPUT_BYTES;
    while end > 0 && !s.is_char_boundary(end) {
        end -= 1;
    }
    let mut truncated = s[..end].to_string();
    truncated.push_str("\n\n[output truncated — exceeded 16384 bytes]");
    truncated
}

#[cfg(test)]
mod truncation_tests {
    use super::*;

    #[test]
    fn short_output_unchanged() {
        let s = "hello world";
        assert_eq!(truncate_output(s), s);
    }

    #[test]
    fn long_output_truncated() {
        let s = "x".repeat(20_000);
        let result = truncate_output(&s);
        assert!(result.len() < 20_000);
        assert!(result.ends_with("[output truncated — exceeded 16384 bytes]"));
    }

    #[test]
    fn exact_boundary_unchanged() {
        let s = "a".repeat(MAX_OUTPUT_BYTES);
        assert_eq!(truncate_output(&s), s);
    }

    #[test]
    fn one_over_boundary_truncated() {
        let s = "a".repeat(MAX_OUTPUT_BYTES + 1);
        let result = truncate_output(&s);
        assert!(result.contains("[output truncated"));
    }
}
