# План сборки и доставки Rust-бинарника fleetd на Wolfram (§7)

**Проект:** [fleetd self-evolution](https://linear.app/mindrock/project/fleetd-self-evolution-f59303c6939c)  
**Тикет:** MIN-229  
**Узел развертывания:** MacBook Wolfram (Master Gateway)  
**Архитектура:** Apple Silicon (aarch64-apple-darwin)  

---

## 1. Верификация окружения компилятора на Wolfram

В ходе аудита 25.10.2026 подтверждено:
* Компилятор Rust и инструментарий Cargo установлены и работоспособны:
  ```text
  /Users/pavelcherkashin/.cargo/bin/cargo 1.99.1 (5f94df478 2026-08-27)
  /Users/pavelcherkashin/.cargo/bin/rustc 1.99.1 (5f94df478 2026-08-27)
  ```
* Проверочная сборка зависимостей и кода `fleetd` в режиме `dev`:
  ```text
  cd /Users/pavelcherkashin/ChitAkasha-Wolfram/bin/fleetd
  /Users/pavelcherkashin/.cargo/bin/cargo check
  Finished `dev` profile [unoptimized + debuginfo] target(s) in 7.03s (0 errors)
  ```
* Вывод: сборка на Wolfram полностью осуществима локально, предположение от 23.10 об отсутствии компилятора опровергнуто фактической проверкой.

---

## 2. Пошаговая процедура сборки релиза

```bash
# 1. Экспорт путей инструментария Cargo
export PATH="$HOME/.cargo/bin:$PATH"

# 2. Переход в исходники демона
cd /Users/pavelcherkashin/ChitAkasha-Wolfram/bin/fleetd

# 3. Релизная компиляция
cargo build --release

# 4. Проверка контрольной суммы
shasum -a 256 target/release/fleetd > target/release/fleetd.sha256
cat target/release/fleetd.sha256
```

---

## 3. Процедура атомарного деплоя и перезапуска службы

```bash
# 1. Копирование во временный файл
cp target/release/fleetd /Users/pavelcherkashin/ChitAkasha-Wolfram/bin/fleetd/fleetd.vNext

# 2. Атомарная замена исполняемого файла
mv /Users/pavelcherkashin/ChitAkasha-Wolfram/bin/fleetd/fleetd.vNext /Users/pavelcherkashin/ChitAkasha-Wolfram/bin/fleetd/fleetd
chmod +x /Users/pavelcherkashin/ChitAkasha-Wolfram/bin/fleetd/fleetd

# 3. Перезапуск демона через launchd
launchctl unload /Users/pavelcherkashin/ChitAkasha-Wolfram/bin/fleetd/com.mindrock.fleetd.plist
launchctl load /Users/pavelcherkashin/ChitAkasha-Wolfram/bin/fleetd/com.mindrock.fleetd.plist

# 4. Проверка логов запуска
tail -n 30 /Users/pavelcherkashin/ChitAkasha-Wolfram/bin/fleetd/fleetd.stdout.log
```

---

## 4. Защитные инварианты

* Подмена бинарника скриптами Python или моками строго запрещена.
* При недоступности локальной сборки резервный канал — сборка через GitHub Actions с верификацией SHA256 перед запуском.
