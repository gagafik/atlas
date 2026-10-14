# Атлас: правила работы над проектом

## Контекст

Локальная школьная платформа: React/TypeScript/Vite, Python 3.12/FastAPI,
SQLite и OR-Tools. Цель — функции aSc Timetables и EduPage. Статусы и пробелы:
`FEATURES.md`, `EDUPAGE_ROADMAP.md`; измерения и ограничения: `PERFORMANCE.md`.
Эти файлы не доказывают готовность сами по себе: проверяйте код и поведение.

## Данные и доступ

- `data/` содержит настоящие школьные данные, базу, вложения и копии. Никогда
  не добавляйте их в Git, Docker-образ, публичные артефакты или логи CI.
- Исходные XLS/XLSX/MHTML, `.env`, ключи и токены также остаются вне репозитория.
  Единственное исключение XLS — искусственный `tests/fixtures/legacy-import.xls`.
- Проверки и эксперименты выполняйте на `initial_state('demo')` и временных БД.
  Не меняйте расписание школы ради тестов. Перед обновлением сервера создайте
  полную копию через `python -m tools.backup_school` и сверьте данные после.
- Доступ к GitHub не даёт автоматически доступа к школе, серверу или Supabase.
  Каждый человек и его AI-инструменты используют собственные учётные записи.
- Сохраняйте проверки ролей, актуальных учебных связей, версий и атомарность
  записи. Новые API должны работать только с разрешёнными пользователю данными.

## Совместная работа

- Один человек/агент — отдельная задача, ветка и локальная копия либо worktree.
  Не редактируйте один рабочий каталог одновременно с коллегой.
- Основная ветка — `main`. Для Codex используйте `codex/<задача>`, для Claude —
  `claude/<задача>`. Перед началом обновите `main`; изменения отправляйте через PR.
- Не делайте force push, не откатывайте чужие изменения и не подменяйте БД.
- Описывайте результат, выполненные проверки и оставшиеся ограничения в PR.
  Наличие кнопки или зелёного теста не равно полному аналогу EduPage.
- Обновляйте документацию, когда меняются возможности, запуск или ограничения.

## Запуск и проверки

Windows: `./start.ps1`. Linux/macOS: инструкции в `CONTRIBUTING.md`.
Проверки: `python -m pytest -q`, `npm ci`, `npm test`, `npm run build`,
`python tools/check_repository.py`. Python берите из `.venv`.
Веб-интерфейс: http://127.0.0.1:8000. В dev-режиме Vite использует порт 5173.
Для сервера используйте `DEPLOYMENT.md`: один процесс API, предварительный
администратор, HTTPS, постоянный том данных, проверяемые резервные копии.

## gstack

Use the `/browse` skill from gstack for all web browsing.
Never use `mcp__claude-in-chrome__*` tools directly.
If this skill is not installed in the current agent environment, explain what
is missing before doing browser work. Other project work can continue.
Use relevant installed skills deliberately; do not claim a skill was executed
without actually reading its instructions and performing its checks.
