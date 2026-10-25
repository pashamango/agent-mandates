# План сборки, поставки исходников и деплоя Rust-бинарника fleetd на Wolfram (§7)

**Проект:** [fleetd self-evolution](https://linear.app/mindrock/project/fleetd-self-evolution-f59303c6939c)  
**Тикет:** MIN-229  
**Узел развертывания:** MacBook Wolfram (Master Gateway)  
**Архитектура:** Apple Silicon (aarch64-apple-darwin)  
**Коммит / Артефакт:** Репозиторий `agent-mandates`, ветка `minutta/min-229-fleetd-vnext-umnaya-marshrutizaciya-proverka-kanona`  

---

## 1. Включенные в репозиторий Rust-исходники демона fleetd

В рамках разрешения замечаний независимого QA по дефектам №6 и №11, полные исходники, модули vNext и интеграционные тесты пакета `fleetd` включены непосредственно в репозиторий:
- Координаты в дереве: `fleetd-evolution/crates/fleetd/`
- Манифест пакета: `fleetd-evolution/crates/fleetd/Cargo.toml` (версия `0.2.0`)
- Фиксация зависимостей: `fleetd-evolution/crates/fleetd/Cargo.lock`
- Исходный код:
  - `fleetd-evolution/crates/fleetd/src/lib.rs`: Библиотечный корень демона, экспортирующий все модули
  - `fleetd-evolution/crates/fleetd/src/main.rs`: Точка входа демона, запуск многопоточного сервера axum и рабочих циклов
  - `fleetd-evolution/crates/fleetd/src/canonical.rs`: Канонический гейт валидации Linear-проекта, обязательного тира (ВС/ВП/МС/МП), заморозки отправленных заявок МП, допустимости статуса (только Todo/Backlog) и изоляции codex-cloud
  - `fleetd-evolution/crates/fleetd/src/lease.rs`: Межпроцессный durable atomic lease с файловой блокировкой flock, фиксацией `commit_sha`, защитой от двойного захвата и fail-closed при повреждении базы leases
  - `fleetd-evolution/crates/fleetd/src/ledger.rs`: Атомарный routing ledger с межпроцессной блокировкой и персистентной записью в JSONL
  - `fleetd-evolution/crates/fleetd/src/router.rs`: Маршрутизатор задач между agy, codex-local и codex-cloud с проверкой триггеров fallback, fail-closed pilot gate (§6) и независимым учетом квот
  - `fleetd-evolution/crates/fleetd/src/vacuum.rs`: Проактивный вакуум нераспределённого бэклога с каноническим гейтом и умной маршрутизацией
  - `fleetd-evolution/crates/fleetd/src/config.rs`: Конфигурация слотов agy и codex, интервалов pacing (300с)
  - `fleetd-evolution/crates/fleetd/src/worker.rs`: Воркеры слотов, pacing agy, кросс-агентный запуск QA
  - `fleetd-evolution/crates/fleetd/src/runner.rs`: Изоляция процессов, группы процессов unix, передача prompt
  - `fleetd-evolution/crates/fleetd/src/db.rs`: SQLite хранилище очереди задач, outbox и reconciliation
  - `fleetd-evolution/crates/fleetd/src/linear.rs`: Клиент GraphQL API Linear
  - `fleetd-evolution/crates/fleetd/src/model.rs`: Wolfram Offline Model Inference Engine (§5.1, §5.2) с калиброванной линейной моделью политики (mindrock-router-linear-policy-v1) и детерминированным fallback
  - `fleetd-evolution/crates/fleetd/src/server.rs`: Webhook-приёмник Linear
  - `fleetd-evolution/crates/fleetd/tests/acceptance.rs`: Интеграционные acceptance-тесты на Rust, покрывающие все 9 критериев приемки MIN-229 и проверку интеграции daemon hot path (10 интеграционных тестов)

---

## 2. Верификация компилятора и воспроизводимая сборка

Компилятор Rust и инструментарий Cargo установлены и валидированы на MacBook Wolfram:
```text
rustc 1.99.1 (58cf8f9c3 2026-10-13)
cargo 1.99.1 (5f94df478 2026-08-27)
```

### Воспроизводимый запуск тестов и сборки из чистого клона:
```bash
cd fleetd-evolution/crates/fleetd
cargo test --locked
cargo build --release --locked
```

Логи компиляции зафиксированы в репозитории:
- `fleetd-evolution/evidence/CARGO_BUILD.log`

### Контрольная сумма и верификация бинарника release:
```text
SHA256: d27c8c52b836dcdd6d13f04d178cb1be500d9b23edbb5b0a8e49c70571a3e31d  fleetd-evolution/evidence/bin/fleetd
```

Бинарник скомпилирован на MacBook Wolfram (Apple Silicon, aarch64-apple-darwin), протестирован нативно (`evidence/DEPLOY_WOLFRAM.log`) и включён непосредственно в дерево репозитория (`fleetd-evolution/evidence/bin/fleetd`) с фиксацией в `MANIFEST.sha256`. Любой проверяющий из чистого клона может напрямую проверить контрольную сумму бинарника (`sha256sum -c MANIFEST.sha256`), а также пересобрать бинарник из исходников `crates/fleetd` на любой платформе (`cargo test --locked`, `cargo build --release --locked`).

---

## 3. Процедура атомарного деплоя на хосте Wolfram

```bash
# 1. Сборка релиза
cd fleetd-evolution/crates/fleetd
cargo build --release --locked

# 2. Атомарное обновление исполняемого файла
cp target/release/fleetd /Users/pavelcherkashin/ChitAkasha-Wolfram/bin/fleetd/fleetd.vNext
mv /Users/pavelcherkashin/ChitAkasha-Wolfram/bin/fleetd/fleetd.vNext /Users/pavelcherkashin/ChitAkasha-Wolfram/bin/fleetd/fleetd
chmod +x /Users/pavelcherkashin/ChitAkasha-Wolfram/bin/fleetd/fleetd

# 3. Перезапуск демона через launchd
launchctl unload /Users/pavelcherkashin/ChitAkasha-Wolfram/bin/fleetd/com.mindrock.fleetd.plist
launchctl load /Users/pavelcherkashin/ChitAkasha-Wolfram/bin/fleetd/com.mindrock.fleetd.plist

# 4. Проверка статуса службы
tail -n 30 /Users/pavelcherkashin/ChitAkasha-Wolfram/bin/fleetd/fleetd.stdout.log
```

---

## 4. Защитные инварианты

- Запрет на подмену бинарника Python-моками или фиктивными заглушками соблюдён.
- Исходный код Rust полностью воспроизводим из чистого клона репозитория.
- Все 9 acceptance-тестов проходят как в Python reference-suite, так и в нативных Rust integration-тестах (`cargo test`).
- Контрольные суммы манифеста `MANIFEST.sha256` гарантируют целостность всех модулей поставки.
