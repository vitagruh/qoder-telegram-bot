# Qoder Telegram Bot

Telegram-бот, который запускает задачи Qoder на Linux-машине: текст, загрузка файлов, продолжение сессии, подтверждения и вопросы агента кнопками, статистика расхода.

Репозиторий: <https://github.com/vitagruh/qoder-telegram-bot>

## Как устроено

- `bot.py` — aiogram 3, long polling, команды, загрузка файлов и админ-команды
- `qoder_runner.py` — обёртка над Qoder Agent SDK (`qoder-agent-sdk`): сессии, стриминг сообщений, прерывание, PreToolUse-хук sandbox
- `approvals.py` — мост между запросами подтверждения SDK и inline-кнопками Telegram
- `tool_policy.py` — чистая политика файловых инструментов: разрешено только строго внутри `cwd` задачи
- `safe_commands.py` — проверка Bash-команд для автоподтверждения безопасного списка
- `workspace.py` — директории задач: личка `chat_<uid>/tasks/<дата>-<slug>/`, группа `chat_<cid>/user_<uid>/tasks/<дата>-<slug>/`
- `state_store.py` — персистентное состояние (`state.json`): task dir, session id, счётчики расхода
- `task_log.py` — журнал действий пользователей (JSONL) для админ-команд
- `admin_view.py` — сбор пользователей/задач/логов для админ-команд
- `restore_sessions.py` — CLI просмотра, восстановления и очистки сессий без Telegram
- `config.py` — конфигурация из окружения и `.env`

Каждый пользователь получает свою задачу, сессию и каталог. В личном чате каталог — `chat_<user_id>/tasks/…`; в группе у каждого участника отдельный `chat_<chat_id>/user_<user_id>/tasks/…`, поэтому задачи и сессии не смешиваются. Файлы из Telegram сохраняются в `uploads/` задачи, Qoder запускается с `cwd` этой директории.

## Требования

- Linux с Python 3.10+
- Токен бота от [@BotFather](https://t.me/BotFather)
- Qoder Personal Access Token: [qoder.com/account/integrations](https://qoder.com/account/integrations)
- Свой Telegram user id (например, через [@userinfobot](https://t.me/userinfobot))
- `qoder-agent-sdk==1.0.17` содержит `qodercli` внутри wheel; отдельная установка CLI не требуется

## Установка на Linux

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip

sudo mkdir -p /opt/qoder-telegram-bot
sudo chown "$USER" /opt/qoder-telegram-bot
# скопируйте файлы проекта в /opt/qoder-telegram-bot
cd /opt/qoder-telegram-bot

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
chmod 600 .env
```

Вместо копирования файлов можно сразу клонировать репозиторий — см. «Обновление через GitHub».

Заполните `.env`:

| Переменная | Обязательна | Назначение |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | да | токен от @BotFather |
| `ALLOWED_USER_IDS` | да | id пользователей через запятую; остальным бот не отвечает |
| `ADMIN_USER_IDS` | нет | админы для `/users`, `/tasks`, `/logs`; обязаны быть подмножеством `ALLOWED_USER_IDS`, пусто — команды выключены |
| `QODER_PERSONAL_ACCESS_TOKEN` | для автозапуска | PAT Qoder; без него SDK попробует локальный вход `qodercli` |
| `WORKSPACE_ROOT` | нет | корень рабочих директорий, по умолчанию `~/qoder-bot/workspace` |
| `PERMISSION_MODE` | нет | `default` (всё опасное — по кнопке), `acceptEdits`, `plan`, `auto`, `dontAsk`, `bypassPermissions`, `yolo` |
| `APPROVAL_MODE` | нет | `strict` — подтверждать запись, Bash и сеть; `balanced` (по умолчанию) — запись в задаче и безопасный Bash без кнопки; `trusted` — Bash и сеть без кнопки. Файлы вне задачи запрещены в любом режиме |
| `AUTO_APPROVE_NETWORK` | нет | `1` — `WebFetch`/`WebSearch` без кнопки вне режима `trusted`; по умолчанию `0` (спрашивать) |
| `ALLOWED_TOOLS` | нет | инструменты без подтверждения; по умолчанию пусто. Файловые тулы, Bash и `AskUserQuestion` сюда не добавляются — они проходят sandbox/подтверждение |
| `ALWAYS_CONFIRM_TOOLS` | нет | инструменты, которые требуют подтверждения в любом `APPROVAL_MODE` (например `Write,Edit`); имеют приоритет над `trusted` |
| `SAFE_BASH_MODE` | нет | `readonly` (по умолчанию) — безопасные read-only Bash-команды без кнопки, `dev` — дополнительно тесты/сборка проекта, `off` — подтверждать любой Bash |
| `MAX_UPLOAD_MB` | нет | лимит загрузки, фактический максимум — 20 МБ (Bot API) |
| `MAX_CONCURRENT_TASKS` | нет | глобальный лимит одновременно выполняемых задач, по умолчанию 2 |
| `TASK_TIMEOUT_SEC` | нет | общий таймаут задачи, по умолчанию 1200 |
| `APPROVAL_TIMEOUT_SEC` | нет | сколько ждать нажатия кнопки, по умолчанию 120; истечение = отказ |
| `QODER_CONFIG_DIR` | нет | каталог состояния Qoder CLI, по умолчанию `~/.qoder` |
| `LOG_LEVEL` | нет | уровень логов, по умолчанию `INFO` |

## Первый запуск

```bash
cd /opt/qoder-telegram-bot
source .venv/bin/activate
python bot.py
```

В логах должно появиться `workspace: ...` и `permission mode: ...`. Напишите боту `/start`.

## Автозапуск (systemd)

Unit-файл запускает бота от пользователя `qoderbot`, поэтому сначала создайте его (иначе systemd завершится с `status=217/USER`):

```bash
sudo useradd --system --create-home --home-dir /home/qoderbot --shell /usr/sbin/nologin qoderbot
sudo chown -R qoderbot:qoderbot /opt/qoder-telegram-bot
sudo install -d -o qoderbot -g qoderbot -m 700 /home/qoderbot/qoder-workspace
sudo chown qoderbot:qoderbot /opt/qoder-telegram-bot/.env && sudo chmod 600 /opt/qoder-telegram-bot/.env
```

В `.env` при этом укажите `WORKSPACE_ROOT=/home/qoderbot/qoder-workspace`. Затем:

```bash
sudo cp deploy/qoder-bot.service /etc/systemd/system/qoder-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now qoder-bot
journalctl -u qoder-bot -f
```

Перед копированием поправьте в unit-файле пользователя (`User=qoderbot`), путь и `WorkingDirectory`.

Unit-файл включает `ProtectHome=read-only`: запись разрешена в рабочую директорию, `~/.qoder` и типовые кэши (`.cache`, `.config`, `.local`, `.npm`) через `ReadWritePaths`. Если вы изменили `WORKSPACE_ROOT`, `QODER_CONFIG_DIR` или используете другой пакетный менеджер, обновите `ReadWritePaths` (пути с префиксом `-` игнорируются, если каталога нет). `state.json` и журналы лежат внутри `WORKSPACE_ROOT`, поэтому отдельные пути для них не нужны.

### Без root

Боту привилегии не нужны: только исходящие HTTPS-запросы к Telegram и Qoder. Root/sudo требуется лишь для `apt install` и системного unit-файла — оба шага можно обойти:

```bash
mkdir -p ~/qoder-telegram-bot                     # проект в домашней директории
cd ~/qoder-telegram-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && chmod 600 .env
python bot.py
```

Пользовательский сервис (стартует от вашего пользователя):

```bash
mkdir -p ~/.config/systemd/user
cp deploy/qoder-bot.user.service ~/.config/systemd/user/qoder-bot.service
systemctl --user daemon-reload
systemctl --user enable --now qoder-bot
systemctl --user status qoder-bot
loginctl enable-linger "$USER"    # автозапуск после перезагрузки без входа в систему
```

`enable-linger` может потребовать прав администратора один раз; без него сервис поднимется при вашем входе. В `.env` при таком варианте используйте `WORKSPACE_ROOT=/home/<user>/qoder-workspace`.

Агенту не выдавайте sudo: в headless-режиме пароль ввести некому, а подтверждения в Telegram не заменяют привилегированный доступ. Задачи должны выполняться от того же непривилегированного пользователя.

## Обновление через GitHub

Код живёт в публичном репозитории: <https://github.com/vitagruh/qoder-telegram-bot>. Публичный репозиторий тянется по HTTPS без ключей и токенов. `.env` в репозиторий не попадает (он в `.gitignore`), поэтому токены и настройки при обновлении не затрагиваются.

Если бот ещё не установлен, клонируйте проект вместо копирования файлов:

```bash
sudo git clone https://github.com/vitagruh/qoder-telegram-bot.git /opt/qoder-telegram-bot
sudo chown -R "$USER" /opt/qoder-telegram-bot
cd /opt/qoder-telegram-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

### Если каталог уже существует

В `/opt/qoder-telegram-bot` уже лежат файлы, но это ещё не git-репозиторий. Подключите его к GitHub один раз:

```bash
cd /opt/qoder-telegram-bot
sudo -u qoderbot git init -b main
sudo -u qoderbot git remote add origin https://github.com/vitagruh/qoder-telegram-bot.git
sudo -u qoderbot git fetch origin
sudo -u qoderbot git reset --hard origin/main
sudo -u qoderbot git branch --set-upstream-to=origin/main main
```

`git reset --hard` заменит локальные версии отслеживаемых файлов версией из репозитория. `.env`, `.venv` и рабочая директория git не отслеживаются и останутся на месте. Если в файлах проекта были ручные правки — сделайте их копию заранее.

### Обновление

```bash
cd /opt/qoder-telegram-bot
sudo -u qoderbot git pull --ff-only
sudo -u qoderbot /opt/qoder-telegram-bot/.venv/bin/pip install -r requirements.txt
sudo systemctl restart qoder-bot
journalctl -u qoder-bot -n 50 --no-pager
```

Для пользовательского сервиса:

```bash
cd ~/qoder-telegram-bot
git pull --ff-only
.venv/bin/pip install -r requirements.txt
systemctl --user restart qoder-bot
journalctl --user -u qoder-bot -n 50 --no-pager
```

Шаг с `pip install` нужен, когда менялись зависимости; на остальных обновлениях он просто быстро проверяет окружение.

## Команды бота

- `/new [название]` — новая задача и новая сессия Qoder
- `/status` — пользователь, чат, рабочая директория, session id, занятость и `активных задач n/MAX_CONCURRENT_TASKS`
- `/files` — файлы задачи с размерами
- `/get <путь>` — отправить файл из директории задачи (до 50 МБ)
- `/usage` — кредиты, квота аккаунта, контекст
- `/cancel` — прервать **свою** текущую задачу
- `/whoami` — user id, chat id/тип, признак админа, текущая задача и сессия
- `/help` — справка и меню

Обычное сообщение — задача для Qoder в текущей директории пользователя; следующее сообщение продолжает ту же сессию. Документ или фото (до 20 МБ) сохраняется в `uploads/`; подпись к файлу становится запросом.

После задачи файлы, созданные или изменённые в рабочей директории, автоматически отправляются в чат документами (до 10 файлов, каждый до 50 МБ; служебные каталоги вроде `.git`, `node_modules`, `__pycache__` пропускаются). Если файлов больше или они слишком большие, используйте `/files` и `/get`.

### Админ-команды

`ADMIN_USER_IDS` включает команды (только в личном чате администратора):

- `/users` — пользователи, число задач и чатов, последняя активность (state store + фактический workspace);
- `/tasks <user_id>` — до 20 последних задач пользователя: имя, путь, размер, файлы, mtime, session id;
- `/logs <user_id> [N]` — последние N записей журнала (по умолчанию 20, максимум 100).

Админ видит только метаданные и журнал; доступа к содержимому файлов чужих задач команды не дают. Без `ADMIN_USER_IDS` команды выключены, а при попытке указать админа вне `ALLOWED_USER_IDS` бот не стартует.

## Несколько пользователей

- Состояние ключуется парой `(chat_id, user_id)`: у каждого пользователя своя задача, сессия, счётчики и каталог — даже в одной группе.
- В личном чате каталог `chat_<uid>/tasks/…` (совместим с прежней раскладкой), в группе `chat_<cid>/user_<uid>/tasks/…`.
- Один пользователь выполняет одну задачу за раз; глобально одновременно работают не более `MAX_CONCURRENT_TASKS` задач. При превышении бот вежливо отказывает, а не ставит в очередь.
- Кнопки «Отмена / Файлы / Расход / Новая задача» содержат user id и короткий `task_ref`; нажатия из чужого аккаунта, другого чата и от пересланных копий отклоняются.
- Подтверждения и вопросы агента привязаны к `(chat_id, user_id)`: чужой участник группы не может нажать «Разрешить» или ответить за владельца. Ответ текстом принимается только от владельца вопроса.
- В группе итоговый ответ и вложения видны всем участникам — это свойство Telegram, а не утечка между задачами. Не запускайте в группе задачи с приватными данными.

## Состояние, журнал и восстановление сессий

- `WORKSPACE_ROOT/state.json` (0600) хранит по паре user/chat: `task_dir`, `session_id`, число запусков, суммарные credits, последнее время и метрики. Prompt'ы и секреты не сохраняются. Запись атомарная (temp + `os.replace` + fsync), есть резервная копия `state.json.bak`; при повреждении основного файла читается `.bak`.
- После `/new` и по завершении каждой задачи состояние сохраняется. После рестарта первый запрос пользователя продолжает сохранённый `session_id` через `resume`.
- `WORKSPACE_ROOT/logs/users/user_<uid>.jsonl` (0600, каталоги 0700, ротация ~2 МБ) — журнал событий `start`/`tool`/`approval`/`end`. В нём усечённые prompt (до 300 символов) и ошибки (до 500), без env и содержимого инструментов; известные токен-паттерны редактируются.

CLI (не требует aiogram, SDK, сети и токена):

```bash
python restore_sessions.py --list                 # таблица сохранённых сессий
python restore_sessions.py --prune                # удалить записи без каталога задачи
python restore_sessions.py --scan                 # найти задачи в workspace и создать записи
python restore_sessions.py --set-session --user 42 --chat 42 --session <id> [--task-dir <path>]
python restore_sessions.py --list --json          # машинный вывод
```

`--workspace` и `--state` переопределяют корень и путь `state.json`. `--scan` полезен после ручного копирования задач; `--set-session` — если нужно вручную вернуть известный session id.

## Кнопки и расход

- Постоянное меню под полем ввода: «Новая задача», «Статус», «Файлы», «Расход», «Отмена», «Помощь» — дублирует команды.
- Во время работы у статус-сообщения есть кнопка «Отмена».
- После ответа — inline-кнопки «Файлы», «Расход», «Новая задача».
- Операции, требующие подтверждения по `APPROVAL_MODE` (см. таблицу выше), приходят с кнопками «Разрешить / В сессии / Отклонить». Для `ALWAYS_CONFIRM_TOOLS` и для сетевых инструментов, которые всё ещё обязательны, кнопки «В сессии» нет — подтверждение требуется каждый раз.
- Когда агент задаёт уточняющий вопрос (`AskUserQuestion`), варианты приходят кнопками (с поддержкой мультивыбора через «Готово»); свой ответ можно отправить текстом. Если вопрос истёк, ответ в течение минуты не запустит новую задачу — бот предупредит об этом.

Под каждым ответом печатается строка расхода: `💳 credits за задачу · Σ сессия · остаток квоты · контекст % · ⏱ время · 🔧 вызовы инструментов`. `/usage` показывает подробнее: квоту аккаунта (`userQuota`), общую загрузку, сессии по моделям.

Qoder Agent SDK отдаёт **Credits** (`total_credits`, `usage.credits`), а не токены: это внутренняя единица расхода Qoder. Если SDK дополнительно вернёт `input_tokens`/`output_tokens` в `usage`, бот покажет и их; отсутствие этих полей не считается нулём.

## Подтверждения

`APPROVAL_MODE` задаёт, что бот делает без кнопок. По умолчанию — `balanced`: рутинные правки внутри задачи и просмотр файлов не отвлекают, а сеть и всё незнакомое по-прежнему спрашиваются.

| Действие | `strict` | `balanced` | `trusted` |
| --- | --- | --- | --- |
| Чтение внутри задачи | без кнопки | без кнопки | без кнопки |
| Запись/Edit внутри задачи | кнопка | без кнопки | без кнопки |
| Read-only Bash (`SAFE_BASH_MODE=readonly`) | без кнопки | без кнопки | без кнопки |
| Остальной Bash | кнопка | кнопка | без кнопки |
| `WebFetch`/`WebSearch` | кнопка | кнопка* | без кнопки |
| Незнакомые инструменты | кнопка | кнопка | без кнопки |
| Путь или симлинк вне задачи | отказ | отказ | отказ |
| `ALWAYS_CONFIRM_TOOLS` | кнопка | кнопка | кнопка |

\* без кнопки при `AUTO_APPROVE_NETWORK=1`.

- `strict` — прежнее поведение: подтверждается каждая запись, любой Bash (кроме разрешённых `SAFE_BASH_MODE`) и сеть.
- `balanced` — запись внутри задачи и разрешённые `SAFE_BASH_MODE` команды выполняются сразу; сеть и незнакомые инструменты — по кнопке. `SAFE_BASH_MODE=off` снова спрашивает про любой Bash.
- `trusted` — Bash и сеть без кнопки. Удобно для личного одноразового стенда, но **не для нескольких пользователей**: sandbox ограничивает только файловые инструменты, а Bash может читать чужие каталоги задач и файлы пользователя бота.
- `ALWAYS_CONFIRM_TOOLS` — жёсткое исключение для всех режимов; для таких инструментов кнопки «В сессии» нет.
- Текущий режим и подсказка видны в `/status`, предупреждение о рисках — в логах при старте.

**Несколько пользователей.** Держите `balanced` или `strict` и изолируйте бота на уровне ОС (отдельный пользователь, контейнер или VM). `trusted` с несколькими `ALLOWED_USER_IDS` означает, что один участник через Bash может прочитать задачи другого; бот пишет об этом в лог, но не блокирует запуск. Если `trusted` нужен, добавьте `ALWAYS_CONFIRM_TOOLS=Bash` — тогда Bash снова будет спрашиваться, а запись/чтение внутри задачи останутся без кнопок.

## Безопасность

Модель безопасности многослойная, но **не является полноценной песочницей ОС**. Полную изоляцию даёт только отдельный пользователь/контейнер/VM.

- **Доступ.** `ALLOWED_USER_IDS` обязателен: без списка бот не стартует; middleware проверяет и сообщения, и callback-кнопки. `ADMIN_USER_IDS` — подмножество allowed, иначе `SystemExit`.
- **Sandbox файловых инструментов.** `tool_policy.py` резолвит `file_path`/`path`/`notebook_path` (с `expanduser` и `resolve`) и разрешает только строго внутри `cwd` задачи. Абсолютные пути наружу, `..`, `~`, `/proc`, симлинки наружу и чужие каталоги `chat_*` отклоняются. `Read` без `file_path` и `Write`/`Edit`/`MultiEdit` без пути — отказ. `Grep`/`Glob`/`LS` без `path` принудительно получают `path=cwd` (иначе SDK ищет по cwd и всем trusted directories); `Glob` с `..` или абсолютным шаблоном — отказ. Read-only внутри cwd разрешается, запись внутри — без кнопки в `balanced`/`trusted` и по кнопке в `strict`, запись наружу — отказ в любом режиме.
- **Двойная проверка.** `PreToolUse`-хук SDK применяет ту же политику до выполнения инструмента (fail-closed при исключении), а `can_use_tool` повторяет её как defense in depth. `ALLOWED_TOOLS` по умолчанию пуст; файловые тулы, `Bash` и `AskUserQuestion` из него вычищаются с предупреждением, чтобы не обходить policy.
- **Подтверждения и вопросы.** Каждый запрос привязан к `(chat_id, user_id)`; кнопки в пересланной копии, из другого чата или от другого пользователя отклоняются. Ответ текстом на `AskUserQuestion` принимается только от владельца. Нет ответа за `APPROVAL_TIMEOUT_SEC` — отказ. Набор подтверждений задаёт `APPROVAL_MODE` (таблица выше). `ALWAYS_CONFIRM_TOOLS` всегда требует подтверждения, «В сессии» для таких инструментов нет; `WebFetch`/`WebSearch` без кнопки — только в `trusted` или при `AUTO_APPROVE_NETWORK=1`.
- **Права файлов.** Каталоги задач, `logs/` и `state.json` создаются с `0700`/`0600`; в unit-файлах включены `UMask=0077`, `ProtectHome=read-only`, `ReadWritePaths`. Если бот работает на общем хосте без этих мер, другие локальные пользователи могут прочитать задачи.
- **Окружение.** Дочерний `qodercli` получает только PATH/HOME/локаль/прокси и `QODER_PERSONAL_ACCESS_TOKEN`; токен Telegram и посторонние переменные не передаются (см. `env_policy.py`). Для git выставлены `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM=/dev/null`, чтобы пользовательский конфиг не выполнил `fsmonitor`/`textconv`/filter-хуки. Env и токены не логируются.
- **Журнал и state.** `state.json` содержит только идентификаторы и метрики; журнал — усечённые prompt/ошибки с редактированием известных токен-паттернов. Prompt может содержать пользовательские данные: журнал предназначен только для админов.
- **Группы.** Изоляция задач — per-user, но ответ и вложения видит вся группа. Не запускайте в группах задачи с приватными данными.
- **Границы.** `bypassPermissions`/`yolo` отключают все подтверждения — только в контейнере/одноразовой VM. Загруженный файл может содержать prompt injection; подтверждения и sandbox остаются барьером, но не отменяют риск. Политика проверяет запрошенные пути, но рекурсивные `Grep`/`Glob` могут проходить по симлинкам внутри `cwd` в зависимости от реализации инструмента — жёсткую границу даёт только отдельный пользователь/контейнер. Порядок «хук против allow-list/permission_mode» в живом CLI не документирован, поэтому файловые тулы дополнительно исключены из автоподтверждения.
- **Полная защита.** Отдельный непривилегированный пользователь, `ProtectHome`, отсутствие sudo, контейнер или VM с минимальными правами. PAT храните в `.env` с правами `600` и не коммитьте.

### Безопасные Bash-команды

При `SAFE_BASH_MODE=readonly` без кнопки выполняются только read-only команды, и только внутри директории задачи:

- просмотр: `pwd`, `ls`, `cat`, `head`, `tail`, `wc`, `file`, `stat`, `du`, `df`, `tree`, `date`, `uname`, `whoami`, `id`, `which`, `hostname`, `echo`, `printf`, `tr`;
- поиск и сравнение: `grep`, `rg`, `find` (без `-exec`, `-delete`, `-fprint`, `-L`), `diff`, `cmp`, `cut`, `sort`, `uniq`;
- контрольные суммы: `sha256sum`, `sha1sum`, `md5sum`, `b2sum`;
- git только чтение: `status`, `diff`, `log`, `show`, `branch --list`, `rev-parse`, `ls-files`, `blame`, `describe`, `cat-file`, `grep`, `shortlog`, `reflog show`;
- проверка версий: `python --version`, `node --version`, `npm --version`, `pip --version`, `git --version`, `go version`, `cargo --version`, `rustc --version`, `java -version`, `gcc --version`, `make --version`, `cmake --version`.

Работают привычные сокращения: `git log -1`, `git show -3 --stat`, `head -20`, `tail -100`, `make -j`, `make -j4 test`. У `tail` **не** разрешены `-f`/`--follow`/`-F`/`--retry`: такая команда не завершится и заблокирует задачу.

Запрещены динамические списки путей, содержимое которых нельзя проверить статически: `sort --files0-from`, `wc --files0-from`, `find -files0-from`, `file -f/--files-from`. Они позволяют читать файлы за пределами рабочей директории.

Ограничения: запрещены `;`, `&&`, `|`, перенаправления, подстановки `$()`/backticks, brace expansion (`{~}`, `{..}`, `{a,b}`) и неэкранированные glob-символы (шаблоны вроде `--include='*.py'` указывайте в кавычках); пути за пределы директории задачи отклоняются; у `git` проверяется локальный `.git/config` на `fsmonitor`, `textconv`, внешний diff и filter-хуки, а флаги `--output`, `--ext-diff`, `--textconv` и подобные не разрешены. Всё, что не прошло проверку, по-прежнему приходит с кнопками подтверждения.

`SAFE_BASH_MODE=dev` дополнительно разрешает тесты и сборку: `pytest` / `python -m pytest`, `npm|yarn|pnpm test` и `run test|build|lint|check|typecheck`, `cargo test|build|check|clippy`, `go test|build|vet`, `make test|check|build|lint`, `mvn test|package|verify`, `gradle test|build|check`, `dotnet test|build`, `ruff check` / `ruff format --check`, `mypy`, `eslint` (без `--fix`), `tsc --noEmit`. Эти команды выполняют код проекта — включайте `dev` только для доверенных репозиториев.

## Разработка и тесты

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m ruff check .
python -m mypy
python -m pytest -q
```

Тесты проверяют валидатор Bash-команд, sandbox файловых инструментов, мультипользовательскую раскладку, состояние, журнал, админ-вид, restore CLI, подтверждения и вспомогательные функции бота. Тесты с симлинками на Windows без соответствующих прав помечаются `skip` (на Linux выполняются). Для части тестов нужны `aiogram` и `qoder-agent-sdk`, поэтому ставьте зависимости приложения. CI прогоняет линтер, mypy и тесты на Python 3.10 и 3.12.

## Ограничения текущей версии

- Прогресс показывается по вызовам инструментов, без посимвольного стриминга.
- Один активный запрос на пользователя; глобально — не более `MAX_CONCURRENT_TASKS`. Активная задача после рестарта **не продолжается с середины**: сохраняются каталог и `session_id`, поэтому следующий запрос продолжит сессию, но незавершённый ход нужно повторить.
- Сессия Qoder может истечь на стороне сервиса; тогда бот предложит `/new`.
- В группе итоговые файлы и сообщения видны всем участникам.
- Поддерживаются документы и фото; видео, аудио и голосовые не сохраняются.
- Автоматически прикрепляются до 10 изменённых файлов; файлы больше 50 МБ и остальные доступны через `/files` и `/get`.
- Расход считается по Credits из SDK; квота запрашивается один раз в конце задачи.
- Порядок применения `PreToolUse`-хука и `permission_mode` в живом `qodercli` не проверен в этом репозитории; политика продублирована в `can_use_tool`, а файловые инструменты исключены из `ALLOWED_TOOLS`.
