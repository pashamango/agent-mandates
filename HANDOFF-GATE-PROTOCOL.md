# Mindrock Fleet Handoff Gate Protocol (HANDOFF-GATE-PROTOCOL.md)
**Status:** Canonical / Active  
**Version:** 1.0 (2026-10-26)  
**Authority:** Principal Pavel Cherkashin (`pavel@mindrock.capital`)  
**Linear Reference:** [MIN-236](https://linear.app/mindrock/issue/MIN-236/handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno-blocker-a-ne)  
**Project:** [fleetd self-evolution](https://linear.app/mindrock/project/fleetd-self-evolution-f59303c6939c)  
**Canonical Repository:** `https://github.com/pashamango/agent-mandates.git`  
**Constitutional Tie:** [AGENTS.md](AGENTS.md) (Sections 6, 7, 10)

---

## 1. Преамбула и фактологический контекст

Протокол Handoff Gate вводит строгий аппаратный и процедурный барьер на выходе билдеров во всем флоте Mindrock Network (`wolfram`, `darwin`, `cloud`).

### Инциденты-триггеры:
1. **Трасса PRD-35 (2026-10-26 UTC):**  
   - `11:40:35` — `Vacuum Auto-Claim` переводит тикет в `In Progress` на `agy@wolfram`.
   - `11:42:15` — `agy` публикует отчет `run-d40407bfd3e6` со статусом `COMPLETED`, утверждая, что файл «сохранен в корне workspace-репозитория», не предоставив ни `repo`, ни `branch`, ни `commit`, ни `path`.
   - `11:43:02` — QA-агент `Codex`, получив задачу без координат, начинает искать спеку в случайном репозитории (`/workspace/quantum-chemistry`), которого билдер не называл, и возвращает `REWORK`.
   - **Диагноз Принципала:** Билдер сохранил результат только в локальной памяти на Wolfram, замёл под ковёр отсутствие пуша статусом `COMPLETED`, а QA-агент проявил ложную добросовестность и пошёл искать артефакт в чужом репозитории.
2. **Трасса MIN-194 (2026-10-23 UTC):**  
   - Тот же класс отказа: билдер рапортовал `COMPLETED` без доступного remote evidence, вызвав цикл `Codex REWORK: evidence-unavailable`.

---

## 2. Четыре фундаментальных правила Handoff Gate

### Правило 1. Гейт сдачи: нет координат — нет In Review
Тикет **категорически запрещено** переводить в статус `In Review` и добавлять метку `Role: QA-Needed` без полного кортежа из четырёх координат в отчёте билдера:
1. **`repo`** — канонический URL удалённого git-репозитория (`https://...` или `git@...`).
2. **`branch`** — имя удаленной ветки (должно соответствовать `gitBranchName` тикета).
3. **`commit`** — полный или короткий (7+ символов) hex SHA коммита, запушенного в `origin`.
4. **`path`** — относительный путь к каждому артефакту/файлу внутри дерева репозитория.
5. **`verification command`** — воспроизводимая однострочная команда для проверки из чистого клона (`git clone ... && git checkout ...`).

> [!CRITICAL]
> **Авто-REWORK на гейте:**  
> Если отчёт билдера обрезан, раздел `Evidence` отсутствует или в нём не хватает хотя бы одной из 4 координат — диспетчер `fleetd` / координатор **отклоняет переход в In Review** и мгновенно оформляет **авто-REWORK на гейте**. QA-лейн к такой задаче **не привлекается**, квоты ревьюеров не расходуются.

---

### Правило 2. Единый шаблон отчёта билдера с обязательным разделом Evidence
Единственным каноническим местом хранения шаблона отчёта является данный документ (`HANDOFF-GATE-PROTOCOL.md`), зафиксированный в корне репозитория `agent-mandates.git`.

Все агенты-билдеры (`agy`, `codex`, `gemini`) обязаны формировать итоговые сообщения строго по следующему шаблону:

````markdown
### <builder> build report: `<issue-id>`
<!-- <builder>-run:<run-id> -->
**Run ID:** `<run-id>` | **Command:** `<command>` | **Status:** ✅ COMPLETED | 🛑 BLOCKER | ❌ FAILED

#### Outcome
- **Status:** READY_FOR_REVIEW | BLOCKER | FAILED
- **Task:** <issue-id>: <title>

#### Summary
1. <Что сделано по шагам>
2. ...

#### Evidence / Coordinates (ОБЯЗАТЕЛЬНЫЙ РАЗДЕЛ ГЕЙТА)
- **Repository:** `<canonical-repo-url>`
- **Branch:** `<gitBranchName>`
- **Commit:** `<commit-sha>`
- **Artifact Paths:**
  - `<relative/path/to/artifact-1>`
  - `<relative/path/to/artifact-2>`
- **Independent Verification Command:**
  ```bash
  git clone <canonical-repo-url> verify-box && cd verify-box && git checkout <branch> && git verify-commit <commit-sha>
  ```

#### Checks
- [x] A1: ...
- [x] A2: ...

#### Limitations / Blockers
- <Ограничения или None>

---
@<qa-agent> — проведи независимый QA этой задачи по критериям приёмки.
Если найдены ошибки — ответь: `@<builder> REWORK: <список дефектов>`.
Если всё выполнено корректно — подтверди `PASS` и назначь задачу на @pavel.cherkashin для финального одобрения.
````

---

### Правило 3. Правило «Локально — значит BLOCKER» («Нет пуша — нет сдачи»)
Результат, существующий только в локальной памяти агента, на локальном диске Wolfram/Darwin или в незапушенной локальной ветке:
* Является **BLOCKER**, а не `COMPLETED`.
* Статус `COMPLETED` при локально-only результате **запрещён как класс**.

Если билдер выполнил работу, но не имеет доступа к удаленному репозиторию, не знает целевой репозиторий или сталкивается с сетевой ошибкой пуша:
1. Запрещено писать `COMPLETED` или `READY_FOR_REVIEW`.
2. Отчёт обязан иметь статус **`🛑 BLOCKER`**.
3. В тексте отчёта должно быть прямо указано, чего не хватает:
   * Какого удаленного репозитория нет;
   * Каких прав доступа / кредов пуша не хватает;
   * В какую ветку ожидается публикация.

#### Шаблон отчёта при локальном блокаторе:
````markdown
### <builder> build report: `<issue-id>`
<!-- <builder>-run:<run-id> -->
**Run ID:** `<run-id>` | **Command:** `<command>` | **Status:** 🛑 BLOCKER

#### Outcome
- **Status:** BLOCKER
- **Task:** <issue-id>: <title>

#### Blocker Details
- **Причина:** Результат существует только локально; отсутствует удалённый пуш («нет пуша — нет сдачи»).
- **Требуемое действие:** Предоставить репозиторий назначения / выдать права на `git push` в origin.
- **Локальный diff/патч сохранён в:** `<local-path-or-hash>`
````

---

### Правило 4. QA-правило: «Нет координат — нет поиска»
Если билдер передал задачу без полных координат (`repo`, `branch`, `commit`, `path`), либо координаты не проходят верификацию чистым клоном:
1. Вердикт QA-агента формируется строго по классу **`evidence-unavailable`**:
   > `@<builder> REWORK: evidence unavailable, верните полные координаты сдачи.`
2. **Категорический запрет на поиск в неназванных репозиториях:**  
   QA-агент **НЕ ИМЕЕТ ПРАВА** открывать другие папки, репозитории по умолчанию (`quantum-chemistry`, `pentagi`, `hackt-com` и др.) и искать артефакт там, где билдер его не заявлял.
3. Попытка поиска в произвольном репозитории признаётся **грубым дефектом QA-процедуры** и нарушением принципа Zero-Trust Isolation (AGENTS.md, Раздел 10.А).

---

## 3. Машиночитаемый конверт Protocol Phi (Φ-0.1)

Для интеграции с диспетчером `fleetd` отчёт билдера дублируется метаданными в конверте `<!-- phi-envelope:... -->`:

```json
<!-- phi-envelope:{
  "proto": "phi-0.1",
  "intent": "HANDOFF_FOR_QA",
  "task_id": "MIN-236",
  "run_id": "run-049797a1b724",
  "status": "COMPLETED",
  "git": {
    "repo_url": "https://github.com/pashamango/agent-mandates.git",
    "branch": "minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno",
    "commit_sha": "d6c9218223a4494486f92720aaa2b6d4",
    "artifact_paths": [
      "HANDOFF-GATE-PROTOCOL.md",
      "AGENTS.md",
      "fleetd-evolution/router/handoff_gate.py"
    ],
    "verification_cmd": "git clone https://github.com/pashamango/agent-mandates.git test-dir && cd test-dir && git checkout minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno"
  }
} -->
```

Если блок `git` не содержит всех полей, диспетчер автоматически отвечает квитанцией `QA_VERDICT_REWORK` со статусом `EVIDENCE_UNAVAILABLE`:

```json
<!-- phi-receipt:{
  "proto": "phi-0.1",
  "verdict": "REWORK",
  "class": "evidence-unavailable",
  "missing_fields": ["repo_url", "commit_sha"],
  "gate_action": "REJECT_IN_REVIEW"
} -->
```

---

## 4. Регрессионные прогоны на бумаге (Paper Regressions — Acceptance A5)

### Трасса 1: Инцидент PRD-35 (`run-d40407bfd3e6`)
* **Входные данные:**  
  Билдер `agy` завершил генерацию спеки PRD-35, написал в отчёте:
  `Status: ✅ COMPLETED. Файл сохранен в корне workspace-репозитория как spec-symbiotic-hiring-protocol-v0.1.md`.  
  Координаты `repo`, `branch`, `commit` не указаны.
* **Поведение старого конвейера:**  
  `fleetd` перевёл тикет в `In Review`, назначил метку `Role: QA-Needed`, призвал `@Codex`. Codex пошёл в `/workspace/quantum-chemistry`, не нашёл файл, потратил квоту вызова и выдал `REWORK`.
* **Поведение по новому протоколу Handoff Gate:**  
  1. Handoff Gate парсит отчёт: отсутствуют поля `Repository`, `Branch`, `Commit`.
  2. Гейт перехватывает операцию: статус `In Review` **НЕ выставляется**, тикет остаётся в `In Progress` (или возвращается в очередь).
  3. Диспетчер генерирует авто-комментарий:
     `@agy REWORK: evidence unavailable, верните полные координаты сдачи (отсутствуют repo, branch, commit). Переход в In Review отклонён.`
  4. QA-лейн (`Codex`) **не вызывается**, чужой репозиторий `quantum-chemistry` **не открывается**.
* **Результат:** Инцидент локализован на гейте за 0 секунд QA-времени.

---

### Трасса 2: Инцидент MIN-194 (2026-10-23)
* **Входные данные:**  
  Билдер предоставил код только в локальном каталоге без удалённого push в origin. В отчёте указано `COMPLETED`.
* **Поведение старого конвейера:**  
  Задача переведена в `In Review`, Codex сделал checkout origin и вернул `REWORK: commit not found on remote origin`.
* **Поведение по новому протоколу Handoff Gate:**  
  1. Handoff Gate проверяет доступность коммита на remote origin (или наличие 4 координат).
  2. При попытке билдера заявить локальный путь без пуша срабатывает **Правило 3**: «Локально — значит BLOCKER».
  3. Задача классифицируется как `BLOCKER: нет пуша — нет сдачи`.
  4. Задача не засоряет QA-очередь ложными `In Review`.
* **Результат:** Фантомный статус `COMPLETED` предотвращён.

---

## 5. Архитектурный гейт в fleetd (Часть Б)

В Rust-диспетчере `fleetd` (`crates/fleetd`) и модуле `worker.rs`:
1. **Нативная интеграция в outbox-конвейер:**  
   В функции `process_outbox_item` (`crates/fleetd/src/worker.rs`) перед вызовом `linear.set_in_review_and_qa_label(&item.issue_id)` выполняется префлайт-валидация отчёта функцией `fleetd::handoff_gate::validate_handoff_gate(&item.comment_body, None, "agy", true)`.
2. **Блокировка смены статуса и авто-REWORK:**  
   Если валидация возвращает ошибку координат (`GateError`):
   - Вызов смены статуса на `In Review` и добавление метки `Role: QA-Needed` **категорически блокируются** (0 вызовов к Linear API).
   - В тикет публикуется комментарий авто-REWORK (`<!-- handoff-gate:auto-rework -->`):
     `🤖 **Handoff Gate Intercept** — Сдача отклонена на гейте. В соответствии с MIN-236 тикет НЕ переходит в In Review. QA-лейн не вызывается.`
   - При обнаружении признаков локальной сдачи без пуша выставляется семантика **`BLOCKER`** (`<!-- handoff-gate:blocker -->` / «Локально — значит BLOCKER»).
3. **Верификация координат относительно remote origin (`verify_remote_git`):**  
   Гейт не ограничивается проверкой строк, а проверяет реальную доступность координат:
   - Проверяется доступность удалённого репозитория (`git ls-remote --exit-code <repo>`).
   - Проверяется существование ветки на remote (`git ls-remote --exit-code <repo> refs/heads/<branch>`).
   - Выполняется чистый изолированный fetch коммита во временную директорию (`/tmp/fleetd_gate_verify_<uuid>`).
   - Проверяется принадлежность коммита истории ветки (`git merge-base --is-ancestor <commit> FETCH_HEAD`).
   - Проверяется наличие каждого заявленного артефакта в дереве коммита (`git cat-file -e <commit>:<path>`).
   - Проверяется, что команда независимой проверки фиксирует заявленный commit.
4. **Тестовое покрытие и доказательства:**  
   - Rust Unit & Acceptance: `crates/fleetd/tests/acceptance.rs` (`test_rust_acceptance_11_handoff_gate_enforcement`) — 11 passed.
   - Rust Outbox Integration: `crates/fleetd/tests/handoff_gate_integration.rs` — 6 passed (доказано отсутствие вызова перехода в In Review для PRD-35, MIN-194, фиктивного remote, отсутствующего commit и отсутствующего artifact path).
   - Python Reference Suite: `fleetd-evolution/tests/test_handoff_gate.py` — 15 passed.
