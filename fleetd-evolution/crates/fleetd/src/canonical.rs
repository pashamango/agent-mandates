//! Canonical Validation Gate (Section 4)
//! Deterministic pre-checks before task dispatch to builders:
//! 1. Linear project is mandatory.
//! 2. Tier (Tier: ВС/ВП/МС/МП) is mandatory.
//! 3. Submitted applications (Tier MP post-submission) are frozen.
//! 4. Marker discipline & codex-cloud isolation contract.
//! 5. Status check: only 'Todo' or 'Backlog' can be dispatched.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CanonicalVerdict {
    pub passed: bool,
    pub reason: String,
    pub rework_comment: Option<String>,
    pub extracted_tier: Option<String>,
    pub extracted_project: Option<String>,
}

impl CanonicalVerdict {
    pub fn pass(tier: &str, project: Option<&str>) -> Self {
        Self {
            passed: true,
            reason: "CANONICAL_PASS".to_string(),
            rework_comment: None,
            extracted_tier: Some(tier.to_string()),
            extracted_project: project.map(|s| s.to_string()),
        }
    }

    pub fn rework(reason: &str, comment: &str) -> Self {
        Self {
            passed: false,
            reason: reason.to_string(),
            rework_comment: Some(comment.to_string()),
            extracted_tier: None,
            extracted_project: None,
        }
    }
}

pub fn extract_project_from_text(text: &str) -> Option<String> {
    for line in text.lines() {
        let trimmed = line.trim();
        if let Some(pos) = trimmed.find("Project:") {
            let after = trimmed[pos + 8..].trim();
            if after.is_empty() {
                continue;
            }
            if let Some(start_tag) = after.find('>') {
                if let Some(end_tag) = after[start_tag + 1..].find("</project>") {
                    let name = after[start_tag + 1..start_tag + 1 + end_tag].trim();
                    if !name.is_empty() {
                        return Some(name.to_string());
                    }
                }
            }
            let clean = after.trim_matches(|c: char| c == '*' || c == '`' || c == '[' || c == ']' || c == '"').trim();
            if clean.is_empty() {
                continue;
            }
            let name = clean.split('\n').next().unwrap_or("").trim();
            let first_seg = if let Some(end_bold) = name.find("**") {
                name[..end_bold].trim()
            } else if let Some(dash) = name.find(" — ") {
                name[..dash].trim()
            } else {
                name.trim()
            };
            if !first_seg.is_empty() {
                return Some(first_seg.to_string());
            }
        }
    }
    None
}

pub fn extract_tier(text: &str) -> Option<String> {
    for line in text.lines() {
        let trimmed = line.trim();
        if let Some(pos) = trimmed.find("Tier:") {
            let after = trimmed[pos + 5..].trim();
            let tier_part = after.split_whitespace().next().unwrap_or("").trim_matches(|c: char| !c.is_alphabetic());
            let upper = tier_part.to_uppercase();
            if upper == "ВС" || upper == "ВП" || upper == "МС" || upper == "МП" {
                return Some(upper);
            }
        }
    }
    None
}

pub fn validate_canonical_rules(
    project: Option<&str>,
    title: &str,
    description: &str,
    status: Option<&str>,
    explicit_tier: Option<&str>,
    assignee: Option<&str>,
) -> CanonicalVerdict {
    // 0. Status check: status is mandatory and only 'Todo' or 'Backlog' are allowed
    let st = match status {
        Some(s) if !s.trim().is_empty() => s.trim(),
        _ => {
            return CanonicalVerdict::rework(
                "MISSING_STATUS",
                "@PM REWORK: Отсутствует статус задачи. Допустимы только 'Todo' и 'Backlog'.",
            );
        }
    };

    let st_lower = st.to_lowercase();
    if st_lower == "done" || st_lower == "completed" || st_lower == "closed" {
        return CanonicalVerdict::rework(
            "ALREADY_DONE",
            "@PM REWORK: Задача уже находится в статусе Done. Повторный захват запрещён.",
        );
    }
    if st_lower != "todo" && st_lower != "backlog" {
        return CanonicalVerdict::rework(
            "INVALID_STATUS",
            &format!("@PM REWORK: Задача в статусе '{}' не готова к выдаче. Допустимы только 'Todo' или 'Backlog'.", st),
        );
    }

    // 1. Mandatory Project check
    let combined_text = format!("{}\n{}", title, description);
    let resolved_project: Option<String> = match project {
        Some(p) if !p.trim().is_empty() => Some(p.trim().to_string()),
        _ => extract_project_from_text(&combined_text),
    };

    if resolved_project.is_none() {
        return CanonicalVerdict::rework(
            "MISSING_PROJECT",
            "@PM REWORK: Задача не привязана к Linear Project. Привязка к проекту обязательна для выдачи билдеру.",
        );
    }

    // 2. Mandatory Tier check
    let extracted = match extract_tier(&combined_text) {
        Some(t) => t,
        None => {
            return CanonicalVerdict::rework(
                "MISSING_TIER",
                "@PM REWORK: Отсутствует классификация тира задачи (Tier: ВС/ВП/МС/МП). Укажите тир в описании.",
            );
        }
    };

    // Reconcile explicit tier if passed
    if let Some(exp) = explicit_tier {
        let exp_upper = exp.trim().to_uppercase();
        if !exp_upper.is_empty() && exp_upper != extracted {
            return CanonicalVerdict::rework(
                "TIER_MISMATCH",
                &format!(
                    "@PM REWORK: Конфликт тира в задаче. Значение поля tier ('{}') противоречит тиру в описании ('{}').",
                    exp_upper, extracted
                ),
            );
        }
    }

    // 3. Submitted = Frozen (Tier MP post-submission)
    if extracted == "МП" {
        let lower = combined_text.to_lowercase();
        let is_submitted = lower.contains("submitted")
            || lower.contains("подана")
            || lower.contains("поданная заявка")
            || lower.contains("awaiting-tap")
            || lower.contains("cv-ready");
        let has_exception = lower.contains("рекрутер попросил")
            || lower.contains("материальная ошибка")
            || lower.contains("recruiter-request");

        if is_submitted && !has_exception {
            return CanonicalVerdict::rework(
                "SUBMITTED_FROZEN",
                "@PM REWORK: Задача тира МП уже отправлена (submitted). Дальнейшая полировка заморожена для сохранения квоты билдера.",
            );
        }
    }

    // 4. Codex-cloud isolation & marker discipline
    let assignee_str = assignee.unwrap_or("").trim().to_lowercase();
    let is_codex_assignee = assignee_str == "codex" || assignee_str == "3bc09df8-b112-4d8d-8d0c-519043c77030";
    let has_codex_mention = combined_text.to_lowercase().contains("@codex");
    let has_cloud_builder_text = combined_text.to_lowercase().contains("builder: codex-cloud");

    if is_codex_assignee && has_codex_mention {
        return CanonicalVerdict {
            passed: false,
            reason: "CODEX_CLOUD_LANE".to_string(),
            rework_comment: None,
            extracted_tier: Some(extracted),
            extracted_project: resolved_project,
        };
    }

    if has_cloud_builder_text {
        return CanonicalVerdict::rework(
            "AMBIGUOUS_BUILDER_MARKER",
            "@PM REWORK: Неоднозначная маркировка 'Builder: codex-cloud'. Облачные задачи Codex должны назначаться строго через assignee: Codex и @Codex.",
        );
    }

    if is_codex_assignee && !has_codex_mention {
        return CanonicalVerdict::rework(
            "AMBIGUOUS_BUILDER_MARKER",
            "@PM REWORK: Задача назначена на Codex, но отсутствует обязательное упоминание @Codex в тикете.",
        );
    }

    // Check ambiguous builder strings like "Builder: codex-custom"
    for line in combined_text.lines() {
        let trimmed = line.trim();
        if let Some(pos) = trimmed.find("Builder:") {
            let val = trimmed[pos + 8..].trim();
            let b = val.split_whitespace().next().unwrap_or("").trim_matches(|c: char| !c.is_alphanumeric());
            let b_lower = b.to_lowercase();
            if !b_lower.is_empty() && b_lower != "agy" && b_lower != "codex" && b_lower != "gemini" {
                return CanonicalVerdict::rework(
                    "AMBIGUOUS_BUILDER_MARKER",
                    &format!("@PM REWORK: Неоднозначный маркер билдера 'Builder: {}'. Исправьте маркировку лейна на agy или codex.", b),
                );
            }
        }
    }

    CanonicalVerdict::pass(&extracted, resolved_project.as_deref())
}
