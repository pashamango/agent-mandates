# План сборки, поставки исходников и деплоя Rust-бинарника fleetd на Wolfram (§7)

**Проект:** [fleetd self-evolution](https://linear.app/mindrock/project/fleetd-self-evolution-f59303c6939c)  
**Тикет:** MIN-229  
**Узел развертывания:** MacBook Wolfram (Master Gateway)  
**Архитектура:** Apple Silicon (aarch64-apple-darwin)  
**Коммит / Артефакт:** Репозиторий `agent-mandates`, ветка `minutta/min-229-fleetd-vnext-umnaya-marshrutizaciya-proverka-kanona`  

---

## 1. Включенные в репозиторий Rust-исходники демона fleetd

В рамках разрешения замечания независимого QA по дефекту №11, полные исходники и конфигурация пакета `fleetd` включены непосредственно в репозиторий:
- Координаты в дереве: `fleetd-evolution/crates/fleetd/`
- Манифест пакета: `fleetd-evolution/crates/fleetd/Cargo.toml` (версия `0.2.0`)
- Фиксация зависимостей: `fleetd-evolution/crates/fleetd/Cargo.lock`
- Исходный код:
  - `fleetd-evolution/crates/fleetd/src/main.rs`: Точка входа демона, запуск многопоточного сервера axum и циклов
  - `fleetd-evolution/crates/fleetd/src/config.rs`: Конфигурация слотов agy и codex, интервалов pacing (300с)
  - `fleetd-evolution/crates/fleetd/src/worker.rs`: Воркеры слотов, pacing agy, кросс-агентный запуск QA
  - `fleetd-evolution/crates/fleetd/src/runner.rs`: Изоляция процессов, группы процессов unix, передача prompt
  - `fleetd-evolution/crates/fleetd/src/db.rs`: SQLite хранилище очереди задач, outbox и reconciliation
  - `fleetd-evolution/crates/fleetd/src/vacuum.rs`: Проактивный вакуум нераспределённого бэклога
  - `fleetd-evolution/crates/fleetd/src/linear.rs`: Клиент GraphQL API Linear
  - `fleetd-evolution/crates/fleetd/src/server.rs`: Webhook-приёмник Linear

---

## 2. Верификация компилятора и воспроизводимая сборка

Компилятор Rust и инструментарий Cargo установлены и валидированы на MacBook Wolfram:
```text
rustc 1.99.1 (58cf8f9c3 2026-10-13)
cargo 1.99.1 (5f94df478 2026-08-27)
```

### Воспроизводимый запуск сборки из чистого клона:
```bash
cd fleetd-evolution/crates/fleetd
~/.cargo/bin/cargo check
~/.cargo/bin/cargo build --release
```

Лог компиляции зафиксирован в репозитории:
`fleetd-evolution/evidence/CARGO_BUILD.log`

### Контрольная сумма собранного бинарника release:
```text
SHA256: e96d8e85361828800d480641f76fe404d3fb213e42efe462d6c0197eac5a3d52  fleetd-evolution/crates/fleetd/target/release/fleetd
```

---

## 3. Процедура атомарного деплоя на хосте Wolfram

```bash
# 1. Сборка релиза
cd fleetd-evolution/crates/fleetd
~/.cargo/bin/cargo build --release

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
- Контрольные суммы манифеста `MANIFEST.sha256` гарантируют целостность всех модулей поставки.
