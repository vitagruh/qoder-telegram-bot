# Qoder Telegram Bot

Telegram-бот, который запускает задачи Qoder на Linux-машине: текст, загрузка файлов, продолжение сессии, подтверждения и вопросы агента кнопками, статистика расхода.

Репозиторий: <https://github.com/vitagruh/qoder-telegram-bot>

## Как устроено

- `bot.py` — aiogram 3, long polling, команды и загрузка файлов
- `qoder_runner.py` — обёртка над Qoder Agent SDK (`qoder-agent-sdk`): сессии, стриминг сообщений, прерывание
- `approvals.py` — мост между запросами подтверждения SDK и inline-кнопками Telegram
- `safe_commands.py` — проверка Bash-команд для автоподтверждения безопасного списка
- `workspace.py` — изолированные директории задач: `WORKSPACE_ROOT/chat_<id>/tasks/<дата>-<slug>/`
- `config.py` — конфигурация из окружения и `.env`

Каждый чат получает свою рабочую директорию. Файлы из Telegram сохраняются в `uploads/` текущей задачи, Qoder запускается с `cwd` этой директории.

## Требования

- Linux с Python 3.10+
- Токен бота от [@BotFather](https://t.me/BotFather)
- Qoder Personal Access Token: [qoder.com/account/integrations](https://qoder.com/account/integrations)
- Свой Telegram user id (например, через [@userinfobot](https://t.me/userinfobot))
- `qoder-agent-sdk` запускает `qodercli` под капотом. Если при старте появляется ошибка о ненайденном бинаре — установите Qoder CLI по официальной инструкции: <https://docs.qoder.com/cli/installation>

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
| `QODER_PERSONAL_ACCESS_TOKEN` | для автозапуска | PAT Qoder; без него SDK попробует локальный вход `qodercli` |
| `WORKSPACE_ROOT` | нет | корень рабочих директорий, по умолчанию `~/qoder-bot/workspace` |
| `PERMISSION_MODE` | нет | `default` (всё опасное — по кнопке), `acceptEdits`, `plan`, `auto`, `dontAsk`, `bypassPermissions`, `yolo` |
| `ALLOWED_TOOLS` | нет | что выполняется без подтверждения, по умолчанию `Read,Grep,Glob` |
| `SAFE_BASH_MODE` | нет | `off` (по умолчанию), `readonly` — безопасные read-only Bash-команды без кнопки, `dev` — дополнительно тесты/сборка проекта |
| `MAX_UPLOAD_MB` | нет | лимит загрузки, фактический максимум — 20 МБ (Bot API) |
| `TASK_TIMEOUT_SEC` | нет | общий таймаут задачи, по умолчанию 1200 |
| `APPROVAL_TIMEOUT_SEC` | нет | сколько ждать нажатия кнопки, по умолчанию 120; истечение = отказ |
| `QODER_CONFIG_DIR` | нет | каталог состояния Qoder CLI, по умолчанию `~/.qoder` |

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

Перед копированием поправьте в unit-файле пользователя (`User=qoderbot`), путь и `WorkingDirectory`. Если `qodercli` установлен в `~/.local/bin`, добавьте в `[Service]`:

```ini
Environment=PATH=/usr/local/bin:/usr/bin:/bin:/home/qoderbot/.local/bin
```

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
- `/status` — рабочая директория, session id, занятость
- `/files` — файлы задачи с размерами
- `/get <путь>` — отправить файл из директории задачи (до 50 МБ)
- `/usage` — кредиты, квота аккаунта, контекст
- `/cancel` — прервать текущую задачу
- `/help` — справка и меню

Обычное сообщение — задача для Qoder в текущей директории; следующее сообщение продолжает ту же сессию. Документ или фото (до 20 МБ) сохраняется в `uploads/`; подпись к файлу становится запросом.

После задачи файлы, созданные или изменённые в рабочей директории, автоматически отправляются в чат документами (до 10 файлов, каждый до 50 МБ; служебные каталоги вроде `.git`, `node_modules`, `__pycache__` пропускаются). Если файлов больше или они слишком большие, используйте `/files` и `/get`.

## Кнопки и расход

- Постоянное меню под полем ввода: «Новая задача», «Статус», «Файлы», «Расход», «Отмена», «Помощь» — дублирует команды.
- Во время работы у статус-сообщения есть кнопка «Отмена».
- После ответа — inline-кнопки «Файлы», «Расход», «Новая задача».
- Опасные операции приходят с кнопками «Разрешить / В сессии / Отклонить».
- Когда агент задаёт уточняющий вопрос (`AskUserQuestion`), варианты приходят кнопками (с поддержкой мультивыбора через «Готово»); свой ответ можно отправить текстом.

Под каждым ответом печатается строка расхода: `💳 credits за задачу · Σ сессия · остаток квоты · контекст % · ⏱ время · 🔧 вызовы инструментов`. `/usage` показывает подробнее: квоту аккаунта (`userQuota`), общую загрузку, сессии по моделям.

Qoder Agent SDK отдаёт **Credits** (`total_credits`, `usage.credits`), а не токены: это внутренняя единица расхода Qoder. Если SDK дополнительно вернёт `input_tokens`/`output_tokens` в `usage`, бот покажет и их; отсутствие этих полей не считается нулём.

## Безопасность

- `ALLOWED_USER_IDS` обязателен: без списка бот не стартует.
- Все операции, не входящие в `ALLOWED_TOOLS`, приходят в Telegram с кнопками «Разрешить / В сессии / Отклонить». Нет ответа за `APPROVAL_TIMEOUT_SEC` — отказ.
- Qoder работает с `cwd` внутри `WORKSPACE_ROOT`; `/get` не выходит за пределы директории задачи.
- Не запускайте бота от root. Отдельный пользователь + `NoNewPrivileges` в unit-файле.
- `bypassPermissions`/`yolo` отключают подтверждения — только в контейнере или одноразовой VM.
- Бот читает загруженные файлы через агента: содержимое файла может содержать инструкции для модели. Подтверждения остаются последним барьером, не отключайте их без изоляции.
- PAT храните только в `.env` с правами `600`, не коммитьте его.

### Безопасные Bash-команды

При `SAFE_BASH_MODE=readonly` без кнопки выполняются только read-only команды, и только внутри директории задачи:

- просмотр: `pwd`, `ls`, `cat`, `head`, `tail`, `wc`, `file`, `stat`, `du`, `df`, `tree`, `date`, `uname`, `whoami`, `id`, `which`, `hostname`, `echo`, `printf`, `tr`;
- поиск и сравнение: `grep`, `rg`, `find` (без `-exec`, `-delete`, `-fprint`, `-L`), `diff`, `cmp`, `cut`, `sort`, `uniq`;
- контрольные суммы: `sha256sum`, `sha1sum`, `md5sum`, `b2sum`;
- git только чтение: `status`, `diff`, `log`, `show`, `branch --list`, `rev-parse`, `ls-files`, `blame`, `describe`, `cat-file`, `grep`, `shortlog`, `reflog show`;
- проверка версий: `python --version`, `node --version`, `npm --version`, `pip --version`, `git --version`, `go version`, `cargo --version`, `rustc --version`, `java -version`, `gcc --version`, `make --version`, `cmake --version`.

Ограничения: запрещены `;`, `&&`, `|`, перенаправления, подстановки `$()`/backticks и неэкранированные glob-символы; пути за пределы директории задачи отклоняются; у `git` проверяется локальный `.git/config` на `fsmonitor`, `textconv`, внешний diff и filter-хуки, а флаги `--output`, `--ext-diff`, `--textconv` и подобные не разрешены. Всё, что не прошло проверку, по-прежнему приходит с кнопками подтверждения.

`SAFE_BASH_MODE=dev` дополнительно разрешает тесты и сборку: `pytest` / `python -m pytest`, `npm|yarn|pnpm test` и `run test|build|lint|check|typecheck`, `cargo test|build|check|clippy`, `go test|build|vet`, `make test|check|build|lint`, `mvn test|package|verify`, `gradle test|build|check`, `dotnet test|build`, `ruff check` / `ruff format --check`, `mypy`, `eslint` (без `--fix`), `tsc --noEmit`. Эти команды выполняют код проекта — включайте `dev` только для доверенных репозиториев.

## Ограничения текущей версии

- Прогресс показывается по вызовам инструментов, без посимвольного стриминга.
- Один активный запрос на чат; состояние (директория, session id, счётчики расхода) живёт в памяти и сбрасывается при перезапуске — после рестарта используйте `/new`.
- Поддерживаются документы и фото; видео, аудио и голосовые не сохраняются.
- Автоматически прикрепляются до 10 изменённых файлов; файлы больше 50 МБ и остальные доступны через `/files` и `/get`.
- Расход считается по Credits из SDK; квота запрашивается один раз в конце задачи.
