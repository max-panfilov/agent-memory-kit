# Протокол MCP

Сервер предоставляет ровно восемь инструментов. Ответы содержат исходные данные и предупреждения, а не сгенерированный пересказ. Ошибки возвращаются как безопасные `ToolError` без SQL, токенов и внутренних адресов.

## Событие памяти

| Поле | Правило |
|---|---|
| `request_id` | UUID; совпадает с идентификатором события |
| `project_id` | Целое число больше нуля |
| `application_id` | Идентификатор приложения; обязателен, если у принципала их несколько |
| `event_type` | `milestone`, `decision`, `feature`, `communication`, `release`, `incident`, `handoff`, `correction` |
| `event_status` | `proposed`, `observed` или `confirmed` |
| `title` | Непустая строка до 160 символов |
| `summary` | Непустая строка до 1 000 символов |
| `details` | Непустая строка до 8 000 символов |
| `occurred_at` | Дата и время с часовым поясом; сохраняются в UTC |
| `source_refs` | От 1 до 8 объектов `{"kind": "…", "ref": "…"}` |
| `supersedes_id` | UUID прежнего события в той же области или `null` |
| `shared_safe` | Должно быть `true`; это утверждение автора, а не проверка безопасности |

Допустимые виды источников: `git`, `plane`, `directus_note`, `user_instruction`, `ci`, `release`, `document`. Название `directus_note` сохранено для совместимости контракта и не подключает Directus.

Сервер добавляет `recorded_at`, `payload_hash` и возвращает событие после чтения из PostgreSQL. `shared_safe` не входит в хэш, поскольку обязательно всегда.

## Инструменты памяти

### `record_project_memory`

Добавляет событие. Требуется область `memory:write`.

- Новый UUID: `replayed: false`, `readback_verified: true`.
- Тот же UUID и хэш: `replayed: true`, новой записи нет.
- Тот же UUID с другим содержимым: `MEMORY_REQUEST_ID_CONFLICT`.
- Потерянный исход транзакции: `MEMORY_WRITE_OUTCOME_UNKNOWN`, автоматического повтора нет.
- Отменённая сериализация без сохранённой записи: `MEMORY_WRITE_RETRY_REQUIRED`; повтор только с тем же UUID и payload.
- Исправление: новое событие с `supersedes_id`; старая строка не изменяется.

Пример аргументов находится в `examples/synthetic-event.json`.

### `read_project_memory`

Аргументы: `project_id`, `request_id`. Возвращает `{"event": …, "data_only": true}`. Чужой проект или недоступное приложение не раскрывается.

### `get_project_brief`

Аргументы: `project_id`, необязательный `application_id`. Возвращает проект, 12 последних активных событий, 15 решений, 3 handoff, число прочитанных строк и `history_truncated`. LLM не вызывается. Подтверждённое исправление наследует категорию исходного решения или handoff, оставаясь исходным событием типа `correction`. Для определения категории допускается до 1 000 дополнительных чтений предшественников; неполнота помечается `history_truncated`.

### `get_project_timeline`

Аргументы: `project_id`, `cursor`, `limit`, необязательные `event_type` и `application_id`.

- `cursor` — смещение от нуля;
- `limit` — от 1 до 50;
- порядок — `occurred_at DESC, id DESC`;
- `next_cursor` — число или `null`;
- исправленные события сохраняются.

### `search_project_memory`

Аргументы: `project_id`, `query`, `limit`, необязательный `application_id`.

Запрос содержит от 1 до 1 000 символов, `limit` — от 1 до 20. Взвешенный Unicode BM25 использует `title`, `summary`, `details` и служебные поля. Идентификатор ранжирования — `canonical-bm25-v1`. Возвращаются только канонические строки, исключая события, закрытые подтверждённой коррекцией.

## Инструменты знаний

### `get_work_context`

Аргументы: `project`, `application_id`, `repo_sha`, `task_kind`, `task_summary`.

`repo_sha` — полный шестнадцатеричный SHA или `null`. Поддерживаются задачи `development`, `review`, `diagnosis`, `release`, `content`. Ответ содержит обязательные и справочные документы, `snapshot_id`, предупреждения и `grants_permissions: false`.

### `read_document`

Аргументы: `document_id`, `cursor`, `limit`, необязательный `snapshot_id`.

`limit` — от 1 до 12 000 Unicode-символов. Для продолжения передайте `next_cursor` из предыдущего ответа. Старый снимок вызывает `CONTENT_VERSION_GONE`.

### `search_knowledge`

Аргументы: `query`, необязательные `profile`, `kind`, `cursor`, `limit`, `snapshot_id`.

Поиск использует SQLite FTS5 и возвращает фрагменты, метаданные и `next_cursor`. `drafts_are_reference_only` всегда истинно.

## Типовые ошибки

| Код | Значение |
|---|---|
| `INVALID_MEMORY_REQUEST_ID` | Некорректный UUID |
| `INVALID_PROJECT` / `PROJECT_NOT_AVAILABLE` | Некорректный или недоступный проект |
| `APPLICATION_NOT_AVAILABLE` | Приложение отсутствует или недоступно |
| `INVALID_MEMORY_TYPE_OR_STATUS` | Неизвестный тип или статус |
| `INVALID_MEMORY_TEXT` | Пустой или слишком длинный текст |
| `POSSIBLE_SECRET_REFUSED` | Сработала ограниченная проверка секрета |
| `MEMORY_REQUEST_ID_CONFLICT` | UUID уже связан с другой полезной нагрузкой |
| `MEMORY_WRITE_RETRY_REQUIRED` | Сериализация отменена, запись не найдена; повтор только с тем же UUID/payload |
| `MEMORY_WRITE_OUTCOME_UNKNOWN` | Исход записи неизвестен; нужен readback по исходному UUID |
| `SUPERSEDED_MEMORY_NOT_AVAILABLE` | Цель исправления отсутствует в той же области |
| `CONTENT_VERSION_GONE` | Снимок документа устарел |
| `INVALID_PAGINATION` | Некорректные курсор или размер страницы |

## `/health`

Возвращает версию, `snapshot_id`, число документов и `semantic_provider: not-configured`. Health не содержит счётчиков очереди, токенов или данных проектов.
