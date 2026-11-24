from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, cast

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    ErrorEvent,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
    User,
)

import admin_view
from approvals import ApprovalManager, Decision
from config import Config, load_config
from qoder_runner import QoderTask
from state_store import StateStore
from task_log import TaskLog, format_entry
from workspace import (
    SKIP_DIRS,
    create_task_dir,
    human_size,
    is_task_dir,
    iter_files,
    recent_files,
    resolve_in,
    sanitize_filename,
    task_ref,
    unique_path,
)

log = logging.getLogger("qoder-bot")

MAX_TEXT = 4000
MAX_TELEGRAM_DOWNLOAD = 20 * 1024 * 1024
MAX_TELEGRAM_UPLOAD = 50 * 1024 * 1024
MAX_RESULT_FILES = 10
RESULT_SKIP_PARTS = SKIP_DIRS
ADMIN_TASKS_LIMIT = 20
ADMIN_LOGS_DEFAULT = 20
ADMIN_LOGS_MAX = 100

BTN_NEW = "🆕 Новая задача"
BTN_STATUS = "📊 Статус"
BTN_FILES = "📁 Файлы"
BTN_USAGE = "📈 Расход"
BTN_CANCEL = "⏹ Отмена"
BTN_HELP = "❓ Помощь"

MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_NEW), KeyboardButton(text=BTN_STATUS)],
        [KeyboardButton(text=BTN_FILES), KeyboardButton(text=BTN_USAGE)],
        [KeyboardButton(text=BTN_CANCEL), KeyboardButton(text=BTN_HELP)],
    ],
    resize_keyboard=True,
    input_field_placeholder="Задача для Qoder…",
)

BOT_COMMANDS = [
    BotCommand(command="new", description="Новая задача и сессия"),
    BotCommand(command="status", description="Текущая задача"),
    BotCommand(command="files", description="Файлы задачи"),
    BotCommand(command="get", description="Скачать файл из задачи"),
    BotCommand(command="usage", description="Кредиты, квота, контекст"),
    BotCommand(command="cancel", description="Остановить выполнение"),
    BotCommand(command="whoami", description="Мой id и текущая задача"),
    BotCommand(command="help", description="Справка"),
]

ADMIN_COMMANDS = [
    BotCommand(command="users", description="Админ: пользователи"),
    BotCommand(command="tasks", description="Админ: задачи пользователя"),
    BotCommand(command="logs", description="Админ: журнал пользователя"),
]

HELP_TEXT = """🤖 Qoder Telegram Bot

Отправьте сообщение — Qoder выполнит задачу в вашей изолированной директории.
Документы и фото (до MAX_UPLOAD_MB, но не более 20 МБ Bot API) сохраняются
в uploads/ текущей задачи.

Каждый пользователь работает в своей задаче и сессии. В группе у каждого
участника отдельный каталог и счётчики, но итоговые файлы видны всей группе.

Кнопки внизу: новая задача, статус, файлы, расход, отмена, справка.

Команды:
/new [название] — новая задача и новая сессия
/status — директория, сессия и активность
/files — файлы задачи
/get <путь> — скачать файл из задачи
/usage — кредиты, квота, контекст
/cancel — остановить свою задачу
/whoami — мой id, чат и текущая задача

Файловые инструменты Qoder работают только внутри каталога задачи:
абсолютные пути и симлинки наружу отклоняются в любом режиме.

Подтверждения зависят от APPROVAL_MODE:
• strict — запись и сеть спрашивают, read-only чтение — нет;
• balanced (по умолчанию) — запись внутри задачи и безопасный
  read-only Bash выполняются без подтверждения, сеть и прочие
  инструменты — с подтверждением;
• trusted — Bash и сеть без подтверждения, запись внутри задачи
  без подтверждения; файлы вне задачи всё равно запрещены.
Инструменты из ALWAYS_CONFIRM_TOOLS спрашиваются всегда.
Если агент задаёт вопрос, варианты приходят кнопками; свой ответ можно
отправить текстом."""

ADMIN_HELP = """

Админ-команды (только в личном чате):
/users — пользователи, число задач и чатов
/tasks <user_id> — до 20 последних задач пользователя
/logs <user_id> [N] — журнал пользователя, N от 1 до 100
Админ видит метаданные и журнал, но не получает доступ к файлам чужих задач."""

APPROVAL_MODE_HINTS = {
    "strict": "спрашивать запись и сеть",
    "balanced": "запись в задаче и read-only Bash — без подтверждения",
    "trusted": "Bash и сеть без подтверждения; вне задачи — запрет",
}


class TaskLimiter:
    """Глобальный лимит одновременно выполняемых задач (без ожидания в очереди)."""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self.active = 0

    def try_acquire(self) -> bool:
        if self.active >= self.limit:
            return False
        self.active += 1
        return True

    def release(self) -> None:
        self.active = max(0, self.active - 1)


@dataclass
class ChatState:
    user_id: int = 0
    chat_id: int = 0
    task_dir: Path | None = None
    session_id: str | None = None
    task: QoderTask | None = None
    run_task: asyncio.Task[None] | None = None
    busy: bool = False
    cancel_requested: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tasks_done: int = 0
    credits_total: float = 0.0
    last_usage: dict[str, Any] | None = None
    last_context: dict[str, Any] | None = None
    last_seconds: float = 0.0


class AllowlistMiddleware(BaseMiddleware):
    def __init__(self, allowed: frozenset[int]) -> None:
        self._allowed = allowed

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None or user.id not in self._allowed:
            if isinstance(event, CallbackQuery):
                with contextlib.suppress(Exception):
                    await event.answer("Нет доступа", show_alert=True)
            return None
        return await handler(event, data)


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    chunks: list[str] = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = remaining.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


def fmt_num(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value):,}".replace(",", " ")
        return f"{value:,.2f}".replace(",", " ")
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def status_keyboard(user_id: int, ref: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data=f"act:{user_id}:{ref}:cancel")]
        ]
    )


def result_keyboard(user_id: int, ref: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=BTN_FILES, callback_data=f"act:{user_id}:{ref}:files"),
                InlineKeyboardButton(text=BTN_USAGE, callback_data=f"act:{user_id}:{ref}:usage"),
                InlineKeyboardButton(text=BTN_NEW, callback_data=f"act:{user_id}:{ref}:new"),
            ]
        ]
    )


def make_status_editor(
    status: Message,
    user_id: int,
    ref: str,
) -> Callable[[str], Awaitable[None]]:
    last_update = 0.0

    async def update(text: str) -> None:
        nonlocal last_update
        now = time.monotonic()
        if now - last_update < 1.5:
            return
        last_update = now
        with contextlib.suppress(Exception):
            await status.edit_text(
                text[:MAX_TEXT],
                reply_markup=status_keyboard(user_id, ref),
            )

    return update


async def keep_typing(bot: Bot, chat_id: int) -> None:
    while True:
        with contextlib.suppress(Exception):
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
        await asyncio.sleep(4)


def augment_prompt(prompt: str, files: list[str]) -> str:
    if not files:
        return prompt
    listing = "\n".join(f"- {path}" for path in files)
    return (
        f"{prompt}\n\n"
        f"[Файлы, загруженные пользователем в рабочую директорию:\n{listing}\n"
        f"Прочитай их при необходимости.]"
    )


def collect_result_files(
    task_dir: Path,
    since: float,
    limit: int = MAX_RESULT_FILES,
) -> list[tuple[Path, str, int]]:
    root = task_dir.resolve()
    found: list[tuple[float, Path, str, int]] = []
    for relative, _ in recent_files(task_dir, since, limit=max(limit * 5, 50)):
        rel_path = Path(relative)
        if any(part in RESULT_SKIP_PARTS for part in rel_path.parts[:-1]):
            continue
        path = task_dir / rel_path
        if path.is_symlink():
            continue
        try:
            resolved = path.resolve()
            stat = resolved.stat()
        except OSError:
            continue
        if not resolved.is_file() or root not in resolved.parents:
            continue
        found.append((stat.st_mtime, resolved, relative, stat.st_size))
    found.sort(key=lambda item: item[0], reverse=True)
    return [(path, relative, size) for _, path, relative, size in found[:limit]]


async def send_result_files(
    bot: Bot,
    chat_id: int,
    files: list[tuple[Path, str, int]],
) -> None:
    for path, relative, size in files:
        if size > MAX_TELEGRAM_UPLOAD:
            await bot.send_message(
                chat_id,
                f"📎 {relative} — {human_size(size)}, больше 50 МБ. "
                "Скачайте через /get, уменьшив файл.",
            )
            continue
        caption = f"📎 {relative} — {human_size(size)}"[:1000]
        try:
            await bot.send_document(
                chat_id,
                FSInputFile(path, filename=path.name),
                caption=caption,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("send document failed for %s: %s", path, exc)
            with contextlib.suppress(Exception):
                await bot.send_message(
                    chat_id,
                    f"Не удалось отправить {relative}; детали в логах.",
                )


def usage_line(task: QoderTask, elapsed: float) -> str:
    parts: list[str] = []
    if task.request_credits > 0:
        parts.append(f"💳 {fmt_num(task.request_credits)} credits")
    if task.session_credits is not None:
        parts.append(f"Σ сессия {fmt_num(task.session_credits)}")
    if task.input_tokens:
        parts.append(f"⬆️ {fmt_num(task.input_tokens)} ток.")
    if task.output_tokens:
        parts.append(f"⬇️ {fmt_num(task.output_tokens)} ток.")
    quota = (task.usage_info or {}).get("userQuota") or {}
    if quota.get("remaining") is not None:
        unit = quota.get("unit") or ""
        parts.append(f"остаток {fmt_num(quota['remaining'])}{(' ' + unit) if unit else ''}")
    if (task.usage_info or {}).get("isQuotaExceeded"):
        parts.append("⚠️ квота исчерпана")
    ctx = ((task.context_usage or {}).get("contextWindow") or {}).get("usedPercentage")
    if ctx is not None:
        parts.append(f"контекст {fmt_num(ctx)}%")
    parts.append(f"⏱ {elapsed:.0f} с")
    parts.append(f"🔧 {len(task.tool_calls)} вызовов")
    return " · ".join(parts)


def usage_details(state: ChatState) -> str:
    lines = ["📈 Расход и квота", f"Запусков задач: {state.tasks_done}"]
    if state.credits_total > 0:
        lines.append(f"Суммарно по задачам: {fmt_num(state.credits_total)} credits")
    if state.last_seconds:
        lines.append(f"Последняя задача: {state.last_seconds:.0f} с")

    info = state.last_usage or {}
    quota = info.get("userQuota") or {}
    if quota:
        used = fmt_num(quota.get("used"))
        total = fmt_num(quota.get("total"))
        unit = quota.get("unit") or ""
        suffix = f" {unit}" if unit else ""
        lines.append(f"Квота: {used}/{total}{suffix}")
        if quota.get("remaining") is not None:
            lines.append(f"Осталось: {fmt_num(quota['remaining'])}{suffix}")
        if quota.get("percentage") is not None:
            lines.append(f"Израсходовано: {fmt_num(quota['percentage'])}%")
    if info.get("totalUsagePercentage") is not None:
        lines.append(f"Общая загрузка аккаунта: {fmt_num(info['totalUsagePercentage'])}%")
    if info.get("isQuotaExceeded"):
        lines.append("⚠️ Квота исчерпана")

    session = info.get("session") or {}
    if session.get("total_credits") is not None:
        lines.append(f"Сессия Qoder: {fmt_num(session['total_credits'])} credits")
    for model, data in list((session.get("model_usage") or {}).items())[:5]:
        if isinstance(data, dict) and data.get("credits") is not None:
            lines.append(f"• {model}: {fmt_num(data['credits'])}")

    ctx = ((state.last_context or {}).get("contextWindow") or {}).get("usedPercentage")
    if ctx is not None:
        lines.append(f"Контекст: {fmt_num(ctx)}%")

    if len(lines) == 2:
        lines.append("Данных пока нет — выполните задачу.")
    return "\n".join(lines)


def friendly_task_error(exc: BaseException) -> str:
    text = str(exc).lower()
    if any(marker in text for marker in ("session", "resume", "expired")):
        return (
            "❌ Не удалось продолжить сессию Qoder — возможно, она истекла. "
            "Отправьте /new и повторите запрос. Детали в логах."
        )
    return "❌ Не удалось выполнить задачу. Детали в логах; попробуйте /new."


def user_id_of(message: Message) -> int:
    return message.from_user.id if message.from_user else 0


def chat_type_of(message: Message) -> str:
    return str(getattr(message.chat, "type", "private"))


def build_router(
    cfg: Config,
    approvals: ApprovalManager,
    states: dict[tuple[int, int], ChatState],
    store: StateStore | None = None,
    task_log: TaskLog | None = None,
    limiter: TaskLimiter | None = None,
) -> Router:
    router = Router()
    slots = limiter or TaskLimiter(cfg.max_concurrent_tasks)

    def state_for(chat_id: int, user_id: int) -> ChatState:
        key = (chat_id, user_id)
        state = states.get(key)
        if state is not None:
            return state
        state = ChatState(user_id=user_id, chat_id=chat_id)
        record = store.get(user_id, chat_id) if store is not None else None
        if record:
            task_dir = record.get("task_dir")
            state.task_dir = Path(str(task_dir)) if task_dir else None
            state.session_id = record.get("session_id")
            state.tasks_done = int(record.get("tasks_done") or 0)
            state.credits_total = float(record.get("credits_total") or 0.0)
            state.last_seconds = float(record.get("last_seconds") or 0.0)
            state.last_usage = record.get("last_usage")
            state.last_context = record.get("last_context")
        states[key] = state
        return state

    def persist(state: ChatState) -> None:
        if store is None:
            return
        try:
            store.set(
                state.user_id,
                state.chat_id,
                {
                    "task_dir": str(state.task_dir) if state.task_dir else None,
                    "session_id": state.session_id,
                    "tasks_done": state.tasks_done,
                    "credits_total": state.credits_total,
                    "last_seconds": state.last_seconds,
                    "last_active": time.time(),
                    "last_usage": state.last_usage,
                    "last_context": state.last_context,
                },
            )
            store.save()
        except Exception as exc:  # noqa: BLE001 - состояние не должно ломать задачу
            log.warning("state save failed: %s", exc)

    def log_event(user_id: int, event: dict[str, Any]) -> None:
        if task_log is not None:
            task_log.append(user_id, event)

    def ensure_task_dir(user_id: int, chat_id: int, title: str) -> Path:
        state = state_for(chat_id, user_id)
        if state.task_dir is None or not is_task_dir(cfg.workspace_root, state.task_dir):
            state.task_dir = create_task_dir(cfg.workspace_root, user_id, chat_id, title)
            state.session_id = None
            persist(state)
        return state.task_dir

    def is_admin(user_id: int) -> bool:
        return user_id in cfg.admin_user_ids

    async def require_admin(message: Message, user_id: int) -> bool:
        if not is_admin(user_id):
            await message.answer("Команда доступна только администратору.")
            return False
        if chat_type_of(message) != "private":
            await message.answer("Админ-команды работают только в личном чате.")
            return False
        return True

    async def do_new(message: Message, user_id: int, title: str) -> None:
        state = state_for(message.chat.id, user_id)
        if state.busy:
            await message.answer("Сейчас идёт задача — сначала /cancel, затем новая.")
            return
        state.task_dir = create_task_dir(cfg.workspace_root, user_id, message.chat.id, title or "task")
        state.session_id = None
        persist(state)
        await message.answer(f"🆕 Новая задача: {state.task_dir}")

    async def do_status(message: Message, user_id: int) -> None:
        state = state_for(message.chat.id, user_id)
        lines = [
            f"👤 user {user_id} · chat {message.chat.id} ({chat_type_of(message)})",
        ]
        if state.task_dir is None:
            lines.append("Задача ещё не создана — напишите запрос или нажмите «Новая задача».")
        else:
            lines.append(f"📁 {state.task_dir}")
        lines.extend(
            [
                f"Session: {state.session_id or '—'}",
                f"Статус: {'занят' if state.busy else 'свободен'}",
                f"Активных задач: {slots.active}/{slots.limit}",
                f"Запусков задач: {state.tasks_done}",
                f"Подтверждения: {cfg.approval_mode} — "
                f"{APPROVAL_MODE_HINTS.get(cfg.approval_mode, '')}",
            ]
        )
        if cfg.auto_approve_network:
            lines.append("Сеть: без подтверждения (AUTO_APPROVE_NETWORK=1)")
        await message.answer("\n".join(lines))

    async def do_files(message: Message, user_id: int) -> None:
        state = state_for(message.chat.id, user_id)
        if state.task_dir is None:
            await message.answer("Файлов пока нет.")
            return
        files = iter_files(state.task_dir, limit=100)
        if not files:
            await message.answer("В рабочей директории нет файлов.")
            return
        lines = [f"📁 {state.task_dir}"] + [
            f"{path} — {human_size(size)}" for path, size in files
        ]
        for chunk in split_text("\n".join(lines)):
            await message.answer(chunk)

    async def do_usage(message: Message, user_id: int) -> None:
        await message.answer(usage_details(state_for(message.chat.id, user_id)))

    async def do_cancel(message: Message, user_id: int) -> None:
        state = state_for(message.chat.id, user_id)
        if state.task is None and state.run_task is None:
            await message.answer("Нечего отменять.")
            return
        state.cancel_requested = True
        if state.task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(state.task.interrupt(), timeout=3)
        await approvals.cancel_all(message.chat.id, user_id)
        run_task = state.run_task
        if run_task is not None and not run_task.done():
            run_task.cancel()
        await message.answer("⏹ Останавливаю задачу.")

    async def do_help(message: Message, user_id: int) -> None:
        text = HELP_TEXT
        if is_admin(user_id) and chat_type_of(message) == "private":
            text += ADMIN_HELP
        await message.answer(text, reply_markup=MENU)

    async def finish_status(status: Message, text: str, user_id: int, ref: str) -> None:
        bot = cast(Bot, status.bot)
        chunks = split_text(text) or ["✅ Готово."]
        keyboard = result_keyboard(user_id, ref)
        try:
            await status.edit_text(chunks[0], reply_markup=keyboard)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                await bot.send_message(status.chat.id, chunks[0], reply_markup=keyboard)
        except Exception:
            await bot.send_message(status.chat.id, chunks[0], reply_markup=keyboard)
        for chunk in chunks[1:]:
            await bot.send_message(status.chat.id, chunk)

    async def execute(
        message: Message,
        state: ChatState,
        prompt: str,
        files: list[str],
    ) -> None:
        chat_id = message.chat.id
        user_id = state.user_id
        bot = cast(Bot, message.bot)
        task_dir = ensure_task_dir(user_id, chat_id, prompt)
        ref = task_ref(task_dir)
        started_wall = time.time()
        started_mono = time.monotonic()
        log_event(
            user_id,
            {"event": "start", "task": ref, "chat": chat_id, "prompt": prompt},
        )

        status = await message.answer(
            "⏳ Запускаю Qoder…",
            reply_markup=status_keyboard(user_id, ref),
        )
        update_status = make_status_editor(status, user_id, ref)
        typing = asyncio.create_task(keep_typing(bot, chat_id))

        async def approve(
            tool: str,
            data: dict[str, Any],
            context: Any,
            allow_session: bool,
        ) -> Decision:
            decision = await approvals.request(
                chat_id,
                tool,
                data,
                context,
                user_id=user_id,
                allow_session=allow_session,
            )
            log_event(
                user_id,
                {
                    "event": "approval",
                    "task": ref,
                    "tool": tool,
                    "decision": decision.value,
                },
            )
            return decision

        async def on_tool(tool: str) -> None:
            log_event(user_id, {"event": "tool", "task": ref, "tool": tool})
            await update_status(f"⚙️ {tool}\n⏳ Работаю…")

        task = QoderTask(
            config=cfg,
            cwd=task_dir,
            prompt=augment_prompt(prompt, files),
            session_id=state.session_id,
            approve=approve,
            ask=lambda questions: approvals.ask(chat_id, questions, user_id=user_id),
            on_tool=on_tool,
        )
        state.task = task
        state.cancel_requested = False
        run_task = asyncio.create_task(task.run())
        state.run_task = run_task

        error: str | None = None
        try:
            await asyncio.wait_for(run_task, timeout=cfg.task_timeout_sec)
        except asyncio.TimeoutError:
            error = f"⏱ Превышен лимит {cfg.task_timeout_sec} с — задача остановлена."
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task.interrupt(), timeout=3)
            await approvals.cancel_all(chat_id, user_id)
        except asyncio.CancelledError:
            if not state.cancel_requested:
                raise
            error = "⏹ Задача остановлена."
            await approvals.cancel_all(chat_id, user_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("Qoder task failed for user %s", user_id)
            error = friendly_task_error(exc)
        finally:
            if not run_task.done():
                run_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run_task
            state.run_task = None
            state.task = None
            state.cancel_requested = False
            typing.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing
            if task.session_id:
                state.session_id = task.session_id

        elapsed = time.monotonic() - started_mono
        state.tasks_done += 1
        state.credits_total += task.request_credits
        state.last_seconds = elapsed
        if task.usage_info:
            state.last_usage = task.usage_info
        if task.context_usage:
            state.last_context = task.context_usage
        persist(state)

        text = task.text
        if error:
            text = f"{error}\n\n{text}" if text else error
        elif not text:
            text = "✅ Готово, но Qoder не вернул текст."

        changed = collect_result_files(task_dir, started_wall)
        if changed:
            listing = "\n".join(f"• {relative} — {human_size(size)}" for _, relative, size in changed)
            text = f"{text}\n\n📎 Прикрепляю файлы:\n{listing}"

        log_event(
            user_id,
            {
                "event": "end",
                "task": ref,
                "ok": error is None,
                "error": error or "",
                "credits": round(task.request_credits, 3),
                "elapsed": round(elapsed, 1),
                "files": len(changed),
            },
        )

        text = f"{text}\n\n─────\n{usage_line(task, elapsed)}"
        await finish_status(status, text, user_id, ref)
        if changed:
            await send_result_files(bot, chat_id, changed)

    async def run_prompt(message: Message, prompt: str, files: list[str]) -> None:
        user_id = user_id_of(message)
        state = state_for(message.chat.id, user_id)
        async with state.lock:
            if state.busy:
                await message.answer("Предыдущая задача ещё выполняется — /cancel, чтобы остановить.")
                return
            if not slots.try_acquire():
                await message.answer(
                    f"Сейчас уже выполняется {slots.limit} задач(и) — общий лимит. "
                    "Попробуйте позже."
                )
                return
            state.busy = True
            try:
                await execute(message, state, prompt, files)
            finally:
                state.busy = False
                state.task = None
                slots.release()
                persist(state)

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        await do_help(message, user_id_of(message))

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await do_help(message, user_id_of(message))

    @router.message(Command("new"))
    async def cmd_new(message: Message) -> None:
        title = (message.text or "").partition(" ")[2].strip()
        await do_new(message, user_id_of(message), title)

    @router.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        await do_status(message, user_id_of(message))

    @router.message(Command("files"))
    async def cmd_files(message: Message) -> None:
        await do_files(message, user_id_of(message))

    @router.message(Command("usage"))
    async def cmd_usage(message: Message) -> None:
        await do_usage(message, user_id_of(message))

    @router.message(Command("cancel"))
    async def cmd_cancel(message: Message) -> None:
        await do_cancel(message, user_id_of(message))

    @router.message(Command("whoami"))
    async def cmd_whoami(message: Message) -> None:
        user_id = user_id_of(message)
        state = state_for(message.chat.id, user_id)
        lines = [
            f"👤 user_id: {user_id}",
            f"💬 chat_id: {message.chat.id} ({chat_type_of(message)})",
            f"🛡 администратор: {'да' if is_admin(user_id) else 'нет'}",
            f"📁 задача: {state.task_dir or '—'}",
            f"🔗 сессия: {admin_view.short_session(state.session_id)}",
        ]
        await message.answer("\n".join(lines))

    @router.message(Command("users"))
    async def cmd_users(message: Message) -> None:
        user_id = user_id_of(message)
        if not await require_admin(message, user_id):
            return
        rows = admin_view.collect_users(cfg.workspace_root, store)
        for chunk in split_text(admin_view.format_users(rows)):
            await message.answer(chunk)

    @router.message(Command("tasks"))
    async def cmd_tasks(message: Message) -> None:
        user_id = user_id_of(message)
        if not await require_admin(message, user_id):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            await message.answer("Использование: /tasks <user_id>")
            return
        target = int(parts[1].strip())
        rows = admin_view.user_tasks(
            cfg.workspace_root, target, store, limit=ADMIN_TASKS_LIMIT
        )
        for chunk in split_text(admin_view.format_tasks(target, rows)):
            await message.answer(chunk)

    @router.message(Command("logs"))
    async def cmd_logs(message: Message) -> None:
        user_id = user_id_of(message)
        if not await require_admin(message, user_id):
            return
        parts = (message.text or "").split()
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("Использование: /logs <user_id> [N]")
            return
        target = int(parts[1])
        limit = ADMIN_LOGS_DEFAULT
        if len(parts) >= 3:
            if not parts[2].isdigit():
                await message.answer("N должно быть числом от 1 до 100.")
                return
            limit = max(1, min(int(parts[2]), ADMIN_LOGS_MAX))
        if task_log is None:
            await message.answer("Журнал недоступен.")
            return
        entries = task_log.read_recent(target, limit)
        if not entries:
            await message.answer(f"Журнал пользователя {target} пуст.")
            return
        lines = [f"🗒 Журнал пользователя {target} (последние {len(entries)})"]
        lines.extend(format_entry(entry) for entry in entries)
        for chunk in split_text("\n".join(lines)):
            await message.answer(chunk)

    @router.message(Command("get"))
    async def cmd_get(message: Message) -> None:
        state = state_for(message.chat.id, user_id_of(message))
        if state.task_dir is None:
            await message.answer("Рабочая директория ещё не создана.")
            return
        relative = (message.text or "").partition(" ")[2].strip()
        if not relative:
            await message.answer("Использование: /get <путь>")
            return
        target = resolve_in(state.task_dir, relative)
        if target is None or not target.is_file():
            await message.answer("Файл не найден внутри рабочей директории.")
            return
        if target.stat().st_size > MAX_TELEGRAM_UPLOAD:
            await message.answer("Файл больше 50 МБ — Telegram не даст его отправить.")
            return
        await message.answer_document(FSInputFile(target, filename=target.name))

    @router.message(F.text == BTN_NEW)
    async def btn_new(message: Message) -> None:
        await do_new(message, user_id_of(message), "")

    @router.message(F.text == BTN_STATUS)
    async def btn_status(message: Message) -> None:
        await do_status(message, user_id_of(message))

    @router.message(F.text == BTN_FILES)
    async def btn_files(message: Message) -> None:
        await do_files(message, user_id_of(message))

    @router.message(F.text == BTN_USAGE)
    async def btn_usage(message: Message) -> None:
        await do_usage(message, user_id_of(message))

    @router.message(F.text == BTN_CANCEL)
    async def btn_cancel(message: Message) -> None:
        await do_cancel(message, user_id_of(message))

    @router.message(F.text == BTN_HELP)
    async def btn_help(message: Message) -> None:
        await do_help(message, user_id_of(message))

    @router.message(F.document | F.photo)
    async def on_file(message: Message) -> None:
        user_id = user_id_of(message)
        state = state_for(message.chat.id, user_id)
        if state.busy:
            await message.answer("Сейчас идёт задача — дождитесь ответа или /cancel.")
            return

        file = message.document or (message.photo[-1] if message.photo else None)
        if file is None:
            return

        size = getattr(file, "file_size", None) or 0
        limit = min(cfg.max_upload_mb * 1024 * 1024, MAX_TELEGRAM_DOWNLOAD)
        if size > limit:
            await message.answer(
                f"Файл больше {human_size(limit)} — Telegram Bot API не отдаст его боту."
            )
            return

        task_dir = ensure_task_dir(
            user_id,
            message.chat.id,
            (message.caption or "upload").strip(),
        )
        uploads = task_dir / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            uploads.chmod(0o700)

        original = getattr(file, "file_name", None) or f"photo_{int(time.time())}.jpg"
        destination = unique_path(uploads, sanitize_filename(original))
        try:
            # Потоковая загрузка: файл не буферизуется целиком в памяти.
            await cast(Bot, message.bot).download(file, destination=destination)
            with contextlib.suppress(OSError):
                destination.chmod(0o600)
        except Exception as exc:  # noqa: BLE001
            log.warning("download failed: %s", exc)
            with contextlib.suppress(OSError):
                destination.unlink(missing_ok=True)
            await message.answer("Не удалось скачать файл из Telegram.")
            return

        try:
            actual_size = destination.stat().st_size
        except OSError:
            actual_size = 0
        if actual_size > limit:
            with contextlib.suppress(OSError):
                destination.unlink(missing_ok=True)
            await message.answer(f"Файл больше {human_size(limit)} — загрузка отменена.")
            return

        relative = destination.relative_to(task_dir).as_posix()

        caption = (message.caption or "").strip()
        if caption:
            await run_prompt(message, caption, [relative])
        else:
            await message.answer(
                f"📎 Сохранён: {relative}\n"
                "Напишите запрос — файл уже лежит в рабочей директории задачи."
            )

    @router.message(F.text)
    async def on_text(message: Message) -> None:
        prompt = (message.text or "").strip()
        if not prompt:
            return
        if prompt.startswith("/"):
            await message.answer("Неизвестная команда. /help — список команд.")
            return
        user_id = user_id_of(message)
        if approvals.consume_text(message.chat.id, prompt, user_id):
            await message.answer("✍️ Ответ отправлен агенту.")
            return
        if approvals.question_expired(message.chat.id, user_id):
            await message.answer(
                "⏱ Вопрос уже истёк — ответ не отправлен. "
                "Повторите запрос, если он ещё актуален."
            )
            return
        await run_prompt(message, prompt, [])

    @router.callback_query(F.data.startswith("apv:"))
    async def on_approval(cb: CallbackQuery) -> None:
        parts = (cb.data or "").split(":")
        if len(parts) != 3:
            await cb.answer()
            return
        _, request_id, raw = parts
        try:
            decision = Decision(raw)
        except ValueError:
            await cb.answer()
            return
        chat = getattr(cb.message, "chat", None)
        chat_id = chat.id if chat is not None else None
        user_id = cb.from_user.id if cb.from_user else None
        if chat_id is None:
            await cb.answer("Сообщение устарело — откройте задачу заново.", show_alert=True)
            return
        if approvals.resolve(request_id, decision, chat_id=chat_id, user_id=user_id):
            await cb.answer("Принято")
        else:
            await cb.answer("Запрос уже неактуален или недоступен", show_alert=True)

    @router.callback_query(F.data.startswith("ask:"))
    async def on_ask(cb: CallbackQuery) -> None:
        chat = getattr(cb.message, "chat", None)
        chat_id = chat.id if chat is not None else None
        user_id = cb.from_user.id if cb.from_user else None
        if chat_id is None:
            await cb.answer("Сообщение устарело.", show_alert=True)
            return
        if approvals.handle_ask_callback(
            cb.data or "",
            chat_id=chat_id,
            user_id=user_id,
        ):
            await cb.answer()
        else:
            await cb.answer("Вопрос уже неактуален", show_alert=True)

    @router.callback_query(F.data.startswith("act:"))
    async def on_action(cb: CallbackQuery) -> None:
        message = cb.message
        chat = getattr(message, "chat", None)
        if not isinstance(message, Message) or chat is None:
            await cb.answer("Сообщение устарело — отправьте команду.", show_alert=True)
            return
        parts = (cb.data or "").split(":")
        if len(parts) != 4:
            await cb.answer("Кнопка устарела — отправьте команду.", show_alert=True)
            return
        _, raw_user, ref, action = parts
        if not raw_user.isdigit():
            await cb.answer("Кнопка устарела — отправьте команду.", show_alert=True)
            return
        user_id = int(raw_user)
        if cb.from_user is None or cb.from_user.id != user_id:
            await cb.answer("Кнопка принадлежит другому пользователю.", show_alert=True)
            return
        state = state_for(chat.id, user_id)
        if state.task_dir is None or task_ref(state.task_dir) != ref:
            await cb.answer("Кнопка относится к другой задаче.", show_alert=True)
            return
        await cb.answer()
        if action == "new":
            await do_new(message, user_id, "")
        elif action == "files":
            await do_files(message, user_id)
        elif action == "usage":
            await do_usage(message, user_id)
        elif action == "cancel":
            await do_cancel(message, user_id)

    return router


async def main() -> None:
    cfg = load_config()
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=None))
    dp = Dispatcher()
    approvals = ApprovalManager(bot, cfg.approval_timeout_sec)
    middleware = AllowlistMiddleware(cfg.allowed_user_ids)
    dp.message.middleware(middleware)
    dp.callback_query.middleware(middleware)

    store = StateStore(cfg.workspace_root / "state.json", cfg.workspace_root)
    task_log = TaskLog(cfg.workspace_root)
    limiter = TaskLimiter(cfg.max_concurrent_tasks)
    dp.include_router(build_router(cfg, approvals, {}, store, task_log, limiter))

    async def on_error(event: ErrorEvent) -> bool:
        exc = event.exception
        log.error(
            "Unhandled update error: %s",
            exc,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        update = event.update
        message = update.message
        if message is None and update.callback_query is not None:
            candidate = update.callback_query.message
            if isinstance(candidate, Message):
                message = candidate
        if isinstance(message, Message):
            with contextlib.suppress(Exception):
                await message.answer("❌ Внутренняя ошибка. Попробуйте ещё раз, детали в логах.")
        return True

    dp.errors.register(on_error)

    try:
        await bot.set_my_commands(BOT_COMMANDS)
        for admin_id in cfg.admin_user_ids:
            with contextlib.suppress(Exception):
                await bot.set_my_commands(
                    BOT_COMMANDS + ADMIN_COMMANDS,
                    scope=BotCommandScopeChat(chat_id=admin_id),
                )
    except Exception as exc:  # noqa: BLE001
        log.warning("set_my_commands failed: %s", exc)

    log.info("workspace: %s", cfg.workspace_root)
    log.info("permission mode: %s", cfg.permission_mode)
    log.info("approval mode: %s", cfg.approval_mode)
    log.info("auto approve network: %s", cfg.auto_approve_network)
    log.info("safe bash mode: %s", cfg.safe_bash_mode)
    log.info("always confirm: %s", ", ".join(sorted(cfg.always_confirm_tools)) or "—")
    log.info("max concurrent tasks: %s", cfg.max_concurrent_tasks)
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
