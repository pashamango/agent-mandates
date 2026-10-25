# fleetd vNext: Умная маршрутизация + проверка канона (MIN-229)

**Тикет:** [MIN-229](https://linear.app/mindrock/issue/MIN-229/fleetd-vnext-umnaya-marshrutizaciya-proverka-kanona-specifikaciya-v1)  
**Проект:** [fleetd self-evolution](https://linear.app/mindrock/project/fleetd-self-evolution-f59303c6939c)  
**Тир:** **ВП**  
**Билдер:** `agy@wolfram`  
**Независимый QA:** `Codex`  
**Финальный тап:** Принципал Павел Черкашин (`pavel@mindrock.capital`)  

---

## 1. Назначение репозитория и артефакты

В рамках задачи **MIN-229** разработана полная спецификация v1, архитектурный дизайн, модель угроз, план сборки и тестовый пакет для умной маршрутизации и канонической валидации задач в диспетчере флота `fleetd`.

### Основные документы:
1. **[Спецификация v1 и План реализации](fleetd-evolution/FLEETD-VNEXT-IMPLEMENTATION-PLAN.md)**: исчерпывающее описание §1–§9, правила маршрутизации, детерминированный гейт канона, контракт офлайн-модели, health-check пилот и программа приёмочных испытаний.
2. **[Архитектурный дизайн](fleetd-evolution/FLEETD-EVOLUTION-DESIGN.md)**: архитектура «Репозиторий как Организм» (Frozen Core на Rust + Evolver Loop на Python).
3. **[Модель угроз и безопасность](fleetd-evolution/FLEETD-EVOLUTION-THREAT-MODEL.md)**: защита от wireheading, silent mutation, double claims, quota exhaustion.
4. **[План сборки и деплоя Rust на Wolfram](fleetd-evolution/evidence/BUILD_PLAN.md)**: пошаговая процедура сборки релиза с верификацией инструментария cargo 1.99.1.
5. **[Протокол приёмочных испытаний](fleetd-evolution/evidence/TEST_RUN.log)**: лог выполнения 9/9 автоматизированных acceptance-тестов.

---

## 2. Исполняемые компоненты

Модули маршрутизатора расположены в каталоге `fleetd-evolution/router/`:
* `canonical_gate.py`: проверка проекта (Linear project), тира (`Tier: ВС/ВП/МС/МП`), заморозки поданных заявок (`МП`) и дисциплины маркеров билдера.
* `smart_router.py`: двухлейновая маршрутизация (`agy` primary, `codex-local` fallback), единый орган аренды (single lease), fail-closed квоты и pacing, адаптер локальной модели с детерминированным fallback.
* `ledger.py`: запись телеметрии в `routing_ledger.jsonl` и регрессионный сьют на исторических инцидентах (MIN-124, MIN-173, MIN-213, MIN-221).
* `health_pilot.py`: health-check слота codex и запуск пилотной задачи MIN-221 (Collatz M3).

---

## 3. Воспроизведение и независимый QA (Clean Clone)

Для проведения независимой проверки в чистом окружении выполните:

```bash
# 1. Чистый клон из публичного репозитория
git clone -b minutta/min-229-fleetd-vnext-umnaya-marshrutizaciya-proverka-kanona https://github.com/pashamango/agent-mandates.git clean-test-dir
cd clean-test-dir

# 2. Проверка наличия спецификации и артефактов
test -f fleetd-evolution/FLEETD-VNEXT-IMPLEMENTATION-PLAN.md && echo "Plan: OK"
test -f fleetd-evolution/FLEETD-EVOLUTION-DESIGN.md && echo "Design: OK"
test -f fleetd-evolution/FLEETD-EVOLUTION-THREAT-MODEL.md && echo "Threat Model: OK"
test -f fleetd-evolution/evidence/BUILD_PLAN.md && echo "Build Plan: OK"

# 3. Запуск приёмочных тестов (все 9 сценариев)
cd fleetd-evolution
python3 -m unittest -v tests/test_fleetd_vnext.py
```
