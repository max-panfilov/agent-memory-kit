# Проверка и приёмка

## Полный путь

```sh
ci/verify.sh
```

Скрипт использует отдельный `COMPOSE_PROJECT_NAME=agent-memory-kit-verify`, собирает образы и удаляет только свои ресурсы после проверки.

## Что проверяется

| Область | Ожидаемое доказательство |
|---|---|
| Запуск | PostgreSQL проходит healthcheck; миграции и seed завершаются |
| Контракт | Валидация текста, UUID, статусов, источников и UTC |
| Запись | Readback, replay, конфликт, параллельный append, одна строка |
| Неизвестный исход | Реальный COMMIT проходит, ACK теряется, второй insert не выполняется |
| Исправления | Confirmed correction скрывает старое из brief/search, timeline сохраняет обе записи |
| Области | Reader не пишет; другой проект не читает и не изменяет чужую память |
| Знания | SHA документов, снимки, пагинация, FTS5, draft/approved, отказ traversal |
| MCP | Ровно восемь инструментов, record/read/search/context по HTTP и STDIO |
| Мост | JSON и multiline SSE, correlation по ID, запрет редиректа и повторного запроса |
| Backup | Манифест, tamper refusal, свежий restore, одинаковые ID/hash, неизменный source |
| Публикация | Отсутствие запрещённых маркеров и symlink, `git diff --check` |
| Mermaid | Все блоки Mermaid разбираются `mermaid.parse` в Node-контейнере без сети |

## Ручная проверка MCP

После `docker compose up --build -d`:

```sh
docker compose --profile test run --rm smoke
```

Smoke выводит JSON с подтверждениями:

- `replay_and_conflict`;
- `correction_and_timeline`;
- `read_only_denied`;
- `cross_scope_denied`;
- `invalid_auth_denied`.

## Ручная проверка восстановления

```sh
PYTHONPATH=src python3 -m agent_memory_kit.backup
```

Успешный `receipt.json` содержит `status: confirmed`, `restore_verified: true`, `source_unchanged: true` и `tamper_rejected: true`.

## Публичная приёмка

Перед публикацией дополнительно проверяются:

- вычитка русского текста через `ru-check`;
- секреты и частная инфраструктура в дереве и истории;
- итоговые ссылки и Mermaid на GitHub;
- свежий клон main, переключённый на проверяемый SHA, и повторный критический путь.

Результат относится к конкретному SHA и окружению. Один зелёный запуск не является гарантией для других версий, клиентов или production.
