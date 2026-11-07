# Подключение MCP-клиентов

## Способы подключения

1. **Streamable HTTP напрямую.** Подходит клиенту, который умеет передавать bearer-токен в заголовке.
2. **STDIO через `agent-memory-bridge`.** Подходит Claude Code, Codex и другим клиентам, которые запускают локальный процесс. Токен хранится только в защищённом `client.json`.

Для удалённого доступа используйте SSH-туннель или HTTPS с отдельной серверной конфигурацией. Не открывайте незашифрованный MCP-порт наружу и не помещайте токен в URL.

## Файл инструкций агента

Регистрация сервера и инструкция агенту — две разные вещи. Команда `mcp add` делает инструменты доступными, но не объясняет, когда их вызывать. Поведение задаёт файл инструкций в корне проекта:

- Codex читает `AGENTS.md` напрямую;
- Claude Code версии 2.1.277 и новее читает `AGENTS.md`, если в проекте нет `CLAUDE.md`; если `CLAUDE.md` уже есть, добавьте в него строку `@AGENTS.md`;
- другим клиентам передайте тот же текст в их файле инструкций.

Скопируйте `examples/AGENTS.md` в корень проекта и дополните правилами проекта. Без этого файла сервер останется доступным, но агент может не обращаться к памяти.

## Подготовка

Запустите демо из `README.md` и убедитесь, что `http://127.0.0.1:8775/health` отвечает.

Установите bridge из локальной копии репозитория в отдельное виртуальное окружение:

```sh
cd /absolute/path/agent-memory-kit
python3 -m venv .venv
.venv/bin/pip install .
```

Это установка для рабочей станции пользователя. Для проверки самого репозитория используется контейнерный путь из `ci/verify.sh`.

Создайте `client.json` рядом с локальным `.env`:

```json
{
  "url": "http://127.0.0.1:8775/mcp",
  "token": "demo-writer-token"
}
```

Значение примера совпадает с `DEMO_WRITER_TOKEN` из `.env.example`. Файл `client.json` игнорируется Git. Ограничьте доступ:

```sh
chmod 600 client.json
```

Bridge принимает только обычный файл, доступный только владельцу, адреса HTTP на 127.0.0.1 или HTTPS и путь /mcp.

## Claude Code

Команда соответствует разделу «Add a local stdio server» [официальной документации Claude Code](https://code.claude.com/docs/en/mcp):

```sh
claude mcp add --transport stdio agent-memory -- \
  /absolute/path/agent-memory-kit/.venv/bin/agent-memory-bridge \
  --config /absolute/path/agent-memory-kit/client.json
```

Проверка конфигурации:

```sh
claude mcp list
claude mcp get agent-memory
```

Внутри Claude Code также доступна команда `/mcp` для просмотра состояния серверов.

## Codex

Команда соответствует разделу «Configure with the CLI» [официальной документации Codex](https://developers.openai.com/codex/mcp):

```sh
codex mcp add agent-memory -- \
  /absolute/path/agent-memory-kit/.venv/bin/agent-memory-bridge \
  --config /absolute/path/agent-memory-kit/client.json
```

Проверка конфигурации:

```sh
codex mcp list
```

Если клиент запущен в другом окружении, используйте абсолютный путь к тому же окружению, а не к локальному файлу на сервере MCP.

## Два агента в одном проекте

1. Скопируйте `examples/AGENTS.md` в канонический вход инструкций проекта.
2. Настройте обоих агентов на один MCP endpoint и одну область `project 1 / demo-app`.
3. Первый агент вызывает `get_work_context` и `get_project_brief`, затем записывает решение с UUID.
4. Второй агент получает brief, находит решение через `search_project_memory` и читает исходное событие по UUID.
5. Передача завершается новым событием `handoff` или `correction`, а не перезаписью прежней записи.

Пример события — `examples/synthetic-event.json`.

## Проверка и границы совместимости

`ci/verify.sh` проверяет настоящий MCP-клиент FastMCP по HTTP и STDIO-мост отдельными сценариями. Это проверка протокола и транспорта, а не запуск полноценной LLM-сессии Claude Code или Codex.

После настройки конкретного клиента проверьте:

- сервер отображается в списке;
- видны ровно восемь инструментов для writer и семь для reader;
- `get_work_context` возвращает `demo-app`;
- запись writer проходит, запись reader отклоняется;
- проект 2 не имеет доступа к проекту 1.
