use std::path::Path;
use std::process::Stdio;
use std::time::Duration;
use tokio::io::AsyncReadExt;
use tokio::process::Command;
use tracing::{error, info};

pub struct RunResult {
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
    pub timed_out: bool,
    pub error_msg: String,
}

pub async fn run_agent(
    builder: &str,
    bin_path: &Path,
    prompt: &str,
    cwd: &Path,
    timeout_secs: u64,
) -> RunResult {
    info!("Spawning {} CLI: {:?}", builder, bin_path);

    let mut cmd = Command::new(bin_path);

    let is_gemini_cli = bin_path.file_name().map(|n| n == "gemini").unwrap_or(false);

    if builder == "codex" {
        cmd.args([
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--color",
            "never",
            prompt,
        ]);
    } else if builder == "gemini" || is_gemini_cli {
        cmd.args([
            "-y",
            "--skip-trust",
            "-p",
            prompt,
        ]);
    } else {
        // default: agy
        cmd.args(["--dangerously-skip-permissions", "-p", prompt]);
    }

    let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/pavelcherkashin".to_string());
    let current_path = std::env::var("PATH").unwrap_or_else(|_| "/usr/bin:/bin:/usr/sbin:/sbin".to_string());
    let full_path = format!(
        "{}/.local/bin:{}/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:{}",
        home, home, current_path
    );

    cmd.current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env("PATH", full_path)
        // Clean environment of dispatcher secrets
        .env_remove("LINEAR_API_KEY")
        .env_remove("LINEAR_WEBHOOK_SECRET");

    // Unix process group separation
    #[cfg(unix)]
    cmd.process_group(0);

    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            return RunResult {
                exit_code: -1,
                stdout: String::new(),
                stderr: String::new(),
                timed_out: false,
                error_msg: format!("Failed to spawn {} process: {}", builder, e),
            };
        }
    };

    let mut stdout_handle = child.stdout.take().unwrap();
    let mut stderr_handle = child.stderr.take().unwrap();

    let mut stdout_buf = Vec::new();
    let mut stderr_buf = Vec::new();

    let timeout_duration = Duration::from_secs(timeout_secs);
    let wait_res = tokio::time::timeout(timeout_duration, child.wait()).await;

    match wait_res {
        Ok(Ok(status)) => {
            let _ = stdout_handle.read_to_end(&mut stdout_buf).await;
            let _ = stderr_handle.read_to_end(&mut stderr_buf).await;

            let exit_code = status.code().unwrap_or(-1);
            let stdout = String::from_utf8_lossy(&stdout_buf).to_string();
            let stderr = String::from_utf8_lossy(&stderr_buf).to_string();

            RunResult {
                exit_code,
                stdout,
                stderr,
                timed_out: false,
                error_msg: String::new(),
            }
        }
        Ok(Err(e)) => RunResult {
            exit_code: -1,
            stdout: String::new(),
            stderr: String::new(),
            timed_out: false,
            error_msg: format!("Error waiting for {} process: {}", builder, e),
        },
        Err(_) => {
            error!("{} process timed out after {}s, killing process group", builder, timeout_secs);
            let _ = child.kill().await;

            let _ = stdout_handle.read_to_end(&mut stdout_buf).await;
            let _ = stderr_handle.read_to_end(&mut stderr_buf).await;

            RunResult {
                exit_code: -1,
                stdout: String::from_utf8_lossy(&stdout_buf).to_string(),
                stderr: String::from_utf8_lossy(&stderr_buf).to_string(),
                timed_out: true,
                error_msg: format!("Process timed out after {}s", timeout_secs),
            }
        }
    }
}
