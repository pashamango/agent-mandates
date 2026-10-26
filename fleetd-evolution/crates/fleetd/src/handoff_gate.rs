use std::path::{Path, PathBuf};
use std::time::Duration;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HandoffCoordinates {
    pub repo: String,
    pub branch: String,
    pub commit: String,
    pub paths: Vec<String>,
    pub verify_cmd: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GateError {
    EmptyReport,
    DeclaredBlocker,
    LocalOnlyBlocker,
    MissingCoordinates(Vec<String>),
    BranchMismatch { expected: String, actual: String },
    MissingVerificationCommand,
    InvalidVerificationCommand(String),
    RemoteUnreachable(String),
    BranchNotFoundOnRemote(String),
    CommitNotFoundOnBranch(String),
    ArtifactPathNotFound(String),
    GitExecutionError(String),
}

impl GateError {
    pub fn is_blocker(&self) -> bool {
        matches!(self, GateError::DeclaredBlocker | GateError::LocalOnlyBlocker)
    }

    pub fn to_rework_comment(&self, builder: &str) -> String {
        match self {
            GateError::EmptyReport => {
                format!(
                    "🤖 **Handoff Gate Intercept**\n<!-- handoff-gate:auto-rework -->\n\
                    @{builder} REWORK: Отчёт билдера пуст. Предоставьте отчёт по шаблону HANDOFF-GATE-PROTOCOL.md с обязательным разделом Evidence."
                )
            }
            GateError::DeclaredBlocker => {
                format!(
                    "🤖 **Handoff Gate Intercept**\n<!-- handoff-gate:blocker -->\n\
                    @{builder} Задача остановлена в статусе BLOCKER. Необходимы недостающие ресурсы/доступы."
                )
            }
            GateError::LocalOnlyBlocker => {
                format!(
                    "🤖 **Handoff Gate Intercept**\n<!-- handoff-gate:auto-rework -->\n<!-- handoff-gate:blocker -->\n\
                    @{builder} REWORK: Нарушение правила «Локально — значит BLOCKER» (MIN-236 Rule 3).\n\
                    Результат существует только в локальной памяти/диске без пуша в origin. \
                    Статус COMPLETED запрещён («нет пуша — нет сдачи»). \
                    Тикет НЕ переходит в In Review. Семантика переведена в BLOCKER. QA-лейн не вызывается."
                )
            }
            GateError::MissingCoordinates(missing) => {
                let missing_str = missing.join(", ");
                format!(
                    "🤖 **Handoff Gate Intercept**\n<!-- handoff-gate:auto-rework -->\n\
                    @{builder} REWORK: evidence unavailable, верните полные координаты сдачи.\n\
                    Текущий отчёт не проходит Handoff Gate (отсутствуют обязательные координаты: {missing_str}).\n\
                    Тикет не может быть переведён в In Review без полного кортежа: repo + branch + commit + path + verification command.\n\
                    В соответствии с QA-правилом (MIN-236 Rule 4) поиск в неназванных репозиториях запрещён."
                )
            }
            GateError::BranchMismatch { expected, actual } => {
                format!(
                    "🤖 **Handoff Gate Intercept**\n<!-- handoff-gate:auto-rework -->\n\
                    @{builder} REWORK: Ветка в отчёте ('{actual}') не совпадает с закрепленной веткой тикета ('{expected}')."
                )
            }
            GateError::MissingVerificationCommand => {
                format!(
                    "🤖 **Handoff Gate Intercept**\n<!-- handoff-gate:auto-rework -->\n\
                    @{builder} REWORK: Отсутствует воспроизводимая команда независимой проверки (verification command). \
                    Укажите команду в блоке Evidence по шаблону HANDOFF-GATE-PROTOCOL.md."
                )
            }
            GateError::InvalidVerificationCommand(reason) => {
                format!(
                    "🤖 **Handoff Gate Intercept**\n<!-- handoff-gate:auto-rework -->\n\
                    @{builder} REWORK: Команда независимой проверки недействительна: {reason}. \
                    Команда обязана фиксировать и проверять заявленный commit."
                )
            }
            GateError::RemoteUnreachable(repo) => {
                format!(
                    "🤖 **Handoff Gate Intercept**\n<!-- handoff-gate:auto-rework -->\n\
                    @{builder} REWORK: evidence unavailable. Удалённый репозиторий недоступен или не существует: {repo}.\n\
                    Тикет НЕ переходит в In Review. QA-лейн не вызывается."
                )
            }
            GateError::BranchNotFoundOnRemote(details) => {
                format!(
                    "🤖 **Handoff Gate Intercept**\n<!-- handoff-gate:auto-rework -->\n\
                    @{builder} REWORK: evidence unavailable. {details}.\n\
                    Тикет НЕ переходит в In Review. QA-лейн не вызывается."
                )
            }
            GateError::CommitNotFoundOnBranch(details) => {
                format!(
                    "🤖 **Handoff Gate Intercept**\n<!-- handoff-gate:auto-rework -->\n\
                    @{builder} REWORK: evidence unavailable. {details}.\n\
                    Тикет НЕ переходит в In Review. QA-лейн не вызывается."
                )
            }
            GateError::ArtifactPathNotFound(details) => {
                format!(
                    "🤖 **Handoff Gate Intercept**\n<!-- handoff-gate:auto-rework -->\n\
                    @{builder} REWORK: evidence unavailable. {details}.\n\
                    Тикет НЕ переходит в In Review. QA-лейн не вызывается."
                )
            }
            GateError::GitExecutionError(details) => {
                format!(
                    "🤖 **Handoff Gate Intercept**\n<!-- handoff-gate:auto-rework -->\n\
                    @{builder} REWORK: evidence unavailable. Ошибка верификации Git: {details}.\n\
                    Тикет НЕ переходит в In Review. QA-лейн не вызывается."
                )
            }
        }
    }
}

pub fn extract_phi_envelope_git(report_text: &str) -> Option<serde_json::Value> {
    let start_tag = "<!-- phi-envelope:";
    let end_tag = "-->";
    let start = report_text.find(start_tag)?;
    let content = &report_text[start + start_tag.len()..];
    let end = content.find(end_tag)?;
    let raw_json = content[..end].trim();
    let val: serde_json::Value = serde_json::from_str(raw_json).ok()?;
    val.get("git").cloned()
}

fn clean_coordinate_value(raw: &str) -> String {
    let mut s = raw.trim();
    // remove markdown bold or italics
    s = s.trim_start_matches('*').trim_end_matches('*').trim();
    // remove markdown backticks or quotes
    s = s.trim_matches(|c| c == '`' || c == '"' || c == '\'').trim();
    // if markdown link [text](url)
    if let Some(open) = s.find('(') {
        if let Some(close) = s[open..].find(')') {
            let inner = &s[open + 1..open + close];
            return inner.trim().to_string();
        }
    }
    s.to_string()
}

pub fn extract_coordinates_from_markdown(report_text: &str) -> (
    Option<String>,
    Option<String>,
    Option<String>,
    Vec<String>,
    Option<String>,
) {
    let mut repo = None;
    let mut branch = None;
    let mut commit = None;
    let mut paths = Vec::new();
    let mut verify_cmd = None;

    // Check Phi envelope first
    if let Some(git) = extract_phi_envelope_git(report_text) {
        if let Some(r) = git.get("repo_url").or_else(|| git.get("repo")).and_then(|v| v.as_str()) {
            repo = Some(r.to_string());
        }
        if let Some(b) = git.get("branch").or_else(|| git.get("git_branch")).and_then(|v| v.as_str()) {
            branch = Some(b.to_string());
        }
        if let Some(c) = git.get("commit_sha").or_else(|| git.get("commit")).and_then(|v| v.as_str()) {
            commit = Some(c.to_string());
        }
        if let Some(p_arr) = git.get("artifact_paths").and_then(|v| v.as_array()) {
            for item in p_arr {
                if let Some(ps) = item.as_str() {
                    paths.push(ps.to_string());
                }
            }
        }
        if let Some(cmd) = git.get("verification_cmd").and_then(|v| v.as_str()) {
            verify_cmd = Some(cmd.to_string());
        }
    }

    let mut in_artifacts_section = false;
    let mut in_verify_cmd_block = false;
    let mut verify_cmd_buf = String::new();

    for line in report_text.lines() {
        let trimmed = line.trim();

        if in_verify_cmd_block {
            if trimmed.starts_with("```") {
                in_verify_cmd_block = false;
                if verify_cmd.is_none() && !verify_cmd_buf.trim().is_empty() {
                    verify_cmd = Some(verify_cmd_buf.trim().to_string());
                }
            } else {
                if !verify_cmd_buf.is_empty() {
                    verify_cmd_buf.push(' ');
                }
                verify_cmd_buf.push_str(trimmed);
            }
            continue;
        }

        let line_lower = trimmed.to_lowercase();

        // Repository
        if repo.is_none() && (line_lower.contains("repository:") || line_lower.contains("repo_url:") || line_lower.starts_with("- **repository:**")) {
            if let Some(colon) = trimmed.find(':') {
                let after = clean_coordinate_value(&trimmed[colon + 1..]);
                if after.starts_with("http://") || after.starts_with("https://") || after.starts_with("git@") {
                    repo = Some(after);
                }
            }
        }

        // Branch
        if branch.is_none() && (line_lower.contains("branch:") || line_lower.contains("git_branch:") || line_lower.starts_with("- **branch:**")) {
            if let Some(colon) = trimmed.find(':') {
                let after = clean_coordinate_value(&trimmed[colon + 1..]);
                if !after.is_empty() && !after.contains(' ') {
                    branch = Some(after);
                }
            }
        }

        // Commit
        if commit.is_none() && (line_lower.contains("commit:") || line_lower.contains("commit_sha:") || line_lower.starts_with("- **commit:**")) {
            if let Some(colon) = trimmed.find(':') {
                let after = clean_coordinate_value(&trimmed[colon + 1..]);
                if after.len() >= 7 && after.chars().all(|c| c.is_ascii_hexdigit()) {
                    commit = Some(after);
                }
            }
        }

        // Artifact section detection
        if line_lower.contains("artifact paths:") || line_lower.contains("evidence / artifacts") || line_lower.contains("artifacts:") {
            in_artifacts_section = true;
            continue;
        }

        if in_artifacts_section {
            if trimmed.starts_with("- **") || trimmed.starts_with("* **") {
                in_artifacts_section = false;
            } else if trimmed.starts_with("- ") || trimmed.starts_with("* ") {
                let val = clean_coordinate_value(&trimmed[2..]);
                let val_lower = val.to_lowercase();
                if !val.is_empty() && !val_lower.contains("verification command") {
                    paths.push(val);
                }
            } else if trimmed.starts_with('#') || trimmed.starts_with("---") || line_lower.contains("verification command") {
                in_artifacts_section = false;
            }
        }

        // Verification command detection
        if line_lower.contains("verification command") {
            if let Some(idx) = trimmed.find("```") {
                let rest = &trimmed[idx + 3..];
                if let Some(closing) = rest.find("```") {
                    let cmd_str = rest[..closing].trim();
                    if cmd_str.starts_with("git ") {
                        verify_cmd = Some(cmd_str.to_string());
                    }
                } else {
                    in_verify_cmd_block = true;
                }
            }
        } else if verify_cmd.is_none() && (trimmed.starts_with("```bash") || trimmed.starts_with("```sh") || trimmed.starts_with("```")) {
            // Check if upcoming block could be verify command if preceded by heading
            in_verify_cmd_block = true;
            verify_cmd_buf.clear();
        }
    }

    // Fallback search for verification command in quotes if not found in code blocks
    if verify_cmd.is_none() {
        for line in report_text.lines() {
            let trimmed = line.trim();
            if let Some(git_pos) = trimmed.find("git clone ") {
                let cmd_part = &trimmed[git_pos..];
                let end = cmd_part.find('`').or_else(|| cmd_part.find('"')).unwrap_or(cmd_part.len());
                let found = &cmd_part[..end];
                verify_cmd = Some(found.trim().to_string());
                break;
            }
        }
    }

    (repo, branch, commit, paths, verify_cmd)
}

pub fn has_local_only_indicator(report_text: &str) -> bool {
    let lower = report_text.to_lowercase();
    lower.contains("в корне workspace")
        || lower.contains("только локальн")
        || lower.contains("локальная память")
        || lower.contains("без пуша")
        || lower.contains("unpushed")
        || lower.contains("только на диске")
        || lower.contains("локальный каталог")
}

pub fn is_claiming_completed(report_text: &str) -> bool {
    let lower = report_text.to_lowercase();
    lower.contains("status: ready_for_review")
        || lower.contains("status: completed")
        || lower.contains("✅ completed")
}

pub fn is_declared_blocker(report_text: &str) -> bool {
    let lower = report_text.to_lowercase();
    lower.contains("status: blocker") || lower.contains("🛑 blocker")
}

async fn run_git(args: &[&str], cwd: Option<&Path>, timeout_secs: u64) -> Result<(i32, String, String), String> {
    let mut cmd = tokio::process::Command::new("git");
    cmd.args(args);
    if let Some(dir) = cwd {
        cmd.current_dir(dir);
    } else {
        cmd.current_dir(std::env::temp_dir());
    }
    cmd.stdin(std::process::Stdio::null());
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::piped());

    // Discard any credentials or prompts
    cmd.env("GIT_TERMINAL_PROMPT", "0");

    let mut child = cmd.spawn().map_err(|e| format!("Failed to spawn git: {}", e))?;
    let timeout_dur = Duration::from_secs(timeout_secs);
    let wait_res = tokio::time::timeout(timeout_dur, child.wait()).await;

    match wait_res {
        Ok(Ok(status)) => {
            let mut stdout_buf = Vec::new();
            let mut stderr_buf = Vec::new();
            if let Some(mut out) = child.stdout.take() {
                let _ = tokio::io::AsyncReadExt::read_to_end(&mut out, &mut stdout_buf).await;
            }
            if let Some(mut err) = child.stderr.take() {
                let _ = tokio::io::AsyncReadExt::read_to_end(&mut err, &mut stderr_buf).await;
            }
            let code = status.code().unwrap_or(-1);
            let stdout = String::from_utf8_lossy(&stdout_buf).to_string();
            let stderr = String::from_utf8_lossy(&stderr_buf).to_string();
            Ok((code, stdout, stderr))
        }
        Ok(Err(e)) => Err(format!("Git process error: {}", e)),
        Err(_) => {
            let _ = child.kill().await;
            Err(format!("Git command timed out after {}s", timeout_secs))
        }
    }
}

pub async fn verify_remote_git(coords: &HandoffCoordinates) -> Result<(), GateError> {
    // 1. Verification command check: must explicitly verify the commit or branch
    let verify_cmd = &coords.verify_cmd;
    let commit_short = if coords.commit.len() >= 7 { &coords.commit[..7] } else { &coords.commit };
    if !verify_cmd.contains(&coords.commit)
        && !verify_cmd.contains(commit_short)
        && !verify_cmd.contains(&coords.branch)
    {
        return Err(GateError::InvalidVerificationCommand(
            "Команда проверки не фиксирует заявленный commit или branch".to_string(),
        ));
    }

    // 2. Check remote repository accessibility
    let (code, _stdout, stderr) = run_git(&["ls-remote", "--exit-code", &coords.repo], None, 15)
        .await
        .map_err(|e| GateError::RemoteUnreachable(format!("{}: {}", coords.repo, e)))?;
    if code != 0 {
        return Err(GateError::RemoteUnreachable(format!("{}: {}", coords.repo, stderr.trim())));
    }

    // 3. Check remote branch existence
    let branch_ref = format!("refs/heads/{}", coords.branch);
    let (b_code, b_stdout, _) = run_git(&["ls-remote", "--exit-code", &coords.repo, &branch_ref], None, 15)
        .await
        .map_err(|e| GateError::BranchNotFoundOnRemote(format!("{}: {}", coords.branch, e)))?;
    if b_code != 0 || b_stdout.trim().is_empty() {
        let (f_code, f_stdout, _) = run_git(&["ls-remote", "--exit-code", &coords.repo, &coords.branch], None, 15)
            .await
            .unwrap_or((-1, String::new(), String::new()));
        if f_code != 0 || f_stdout.trim().is_empty() {
            return Err(GateError::BranchNotFoundOnRemote(format!(
                "Ветка '{}' не найдена в репозитории '{}'",
                coords.branch, coords.repo
            )));
        }
    }

    // 4. Clean clone/fetch into temp directory to verify commit and artifact paths
    let temp_id = uuid::Uuid::new_v4().to_string();
    let temp_dir: PathBuf = std::env::temp_dir().join(format!("fleetd_gate_verify_{}", temp_id));
    if let Err(e) = tokio::fs::create_dir_all(&temp_dir).await {
        return Err(GateError::GitExecutionError(format!("Не удалось создать временный каталог: {}", e)));
    }

    let cleanup = |dir: PathBuf| async move {
        let _ = tokio::fs::remove_dir_all(dir).await;
    };

    // git init
    let (init_code, _, init_err) = run_git(&["init"], Some(&temp_dir), 10).await
        .map_err(|e| GateError::GitExecutionError(e))?;
    if init_code != 0 {
        cleanup(temp_dir).await;
        return Err(GateError::GitExecutionError(format!("git init failed: {}", init_err)));
    }

    // git remote add origin <repo>
    let (rem_code, _, rem_err) = run_git(&["remote", "add", "origin", &coords.repo], Some(&temp_dir), 10).await
        .map_err(|e| GateError::GitExecutionError(e))?;
    if rem_code != 0 {
        cleanup(temp_dir).await;
        return Err(GateError::GitExecutionError(format!("git remote add failed: {}", rem_err)));
    }

    // git fetch --depth 50 origin <branch>
    let (fetch_code, _, fetch_err) = run_git(&["fetch", "--depth", "50", "origin", &coords.branch], Some(&temp_dir), 30).await
        .map_err(|e| GateError::GitExecutionError(e))?;
    if fetch_code != 0 {
        cleanup(temp_dir).await;
        return Err(GateError::GitExecutionError(format!("git fetch failed: {}", fetch_err)));
    }

    // Check if commit exists
    let rev_target = format!("{}^{{commit}}", coords.commit);
    let (rev_code, _, _) = run_git(&["rev-parse", "--verify", &rev_target], Some(&temp_dir), 10).await
        .map_err(|e| GateError::GitExecutionError(e))?;
    if rev_code != 0 {
        // Try direct fetch of commit if server supports it
        let (direct_code, _, _) = run_git(&["fetch", "--depth", "1", "origin", &coords.commit], Some(&temp_dir), 20).await
            .unwrap_or((-1, String::new(), String::new()));
        if direct_code != 0 {
            cleanup(temp_dir).await;
            return Err(GateError::CommitNotFoundOnBranch(format!(
                "Коммит '{}' не найден в ветке '{}' на remote",
                coords.commit, coords.branch
            )));
        }
    }

    // Check if commit is ancestor of branch FETCH_HEAD
    let (anc_code, _, _) = run_git(&["merge-base", "--is-ancestor", &coords.commit, "FETCH_HEAD"], Some(&temp_dir), 10).await
        .unwrap_or((-1, String::new(), String::new()));
    if anc_code != 0 {
        cleanup(temp_dir).await;
        return Err(GateError::CommitNotFoundOnBranch(format!(
            "Коммит '{}' не принадлежит истории ветки '{}'",
            coords.commit, coords.branch
        )));
    }

    // Verify each artifact path exists in commit tree
    for path in &coords.paths {
        let object_ref = format!("{}:{}", coords.commit, path);
        let (cat_code, _, cat_err) = run_git(&["cat-file", "-e", &object_ref], Some(&temp_dir), 10).await
            .map_err(|e| GateError::GitExecutionError(e))?;
        if cat_code != 0 {
            cleanup(temp_dir).await;
            return Err(GateError::ArtifactPathNotFound(format!(
                "Артефакт '{}' не найден в коммите '{}' (cat-file: {})",
                path, coords.commit, cat_err.trim()
            )));
        }
    }

    cleanup(temp_dir).await;
    Ok(())
}

pub async fn validate_handoff_gate(
    comment_body: &str,
    expected_branch: Option<&str>,
    _builder: &str,
    verify_remote: bool,
) -> Result<HandoffCoordinates, GateError> {
    let trimmed = comment_body.trim();
    if trimmed.is_empty() {
        return Err(GateError::EmptyReport);
    }

    // 1. Explicit blocker declaration (Rule 3)
    if is_declared_blocker(trimmed) {
        return Err(GateError::DeclaredBlocker);
    }

    // 2. Extract coordinates
    let (repo_opt, branch_opt, commit_opt, paths, verify_cmd_opt) =
        extract_coordinates_from_markdown(trimmed);

    let mut missing = Vec::new();
    if repo_opt.is_none() {
        missing.push("repo".to_string());
    }
    if branch_opt.is_none() {
        missing.push("branch".to_string());
    }
    if commit_opt.is_none() {
        missing.push("commit".to_string());
    }
    if paths.is_empty() {
        missing.push("path".to_string());
    }

    // 3. Rule 3: "Локально — значит BLOCKER"
    let local_indicator = has_local_only_indicator(trimmed);
    let claiming_completed = is_claiming_completed(trimmed);
    if claiming_completed && (local_indicator || missing.len() >= 3) {
        return Err(GateError::LocalOnlyBlocker);
    }

    // 4. Rule 1: Missing coordinates -> reject
    if !missing.is_empty() {
        return Err(GateError::MissingCoordinates(missing));
    }

    let repo = repo_opt.unwrap();
    let branch = branch_opt.unwrap();
    let commit = commit_opt.unwrap();

    // 5. Check expected branch if specified
    if let Some(exp) = expected_branch {
        if branch != exp {
            return Err(GateError::BranchMismatch {
                expected: exp.to_string(),
                actual: branch,
            });
        }
    }

    // 6. Verification command check
    let verify_cmd = verify_cmd_opt.ok_or(GateError::MissingVerificationCommand)?;

    let coords = HandoffCoordinates {
        repo,
        branch,
        commit,
        paths,
        verify_cmd,
    };

    // 7. Remote verification
    if verify_remote {
        verify_remote_git(&coords).await?;
    }

    Ok(coords)
}
