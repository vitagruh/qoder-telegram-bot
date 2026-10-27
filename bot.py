from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
    User,
)

from approvals import ApprovalManager, Decision
from config import Config, load_config
from qoder_runner import QoderTask
from workspace import (
    create_task_dir,
    human_size,
    iter_files,
    recent_files,
    resolve_in,
    sanitize_filename,
    unique_path,
)

log = logging.getLogger("qoder-bot")

MAX_TEXT = 4000
MAX_TELEGRAM_DOWNLOAD = 20 * 1024 * 1024
MAX_TELEGRAM_UPLOAD = 50 * 1024 * 1024

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
    BotCommand(command="usage", description="Кредиты, квота, контекст"),
    BotCommand(command="cancel", description="Остановить выполнение"),
    BotCommand(command="help", description="Справка"),
]

HELP_TEXT = """🤖 Qoder Telegram Bot

Отправьте сообщение — Qoder выполнит задачу в изолированной директории.
Документы и фото (до 20 МБ) сохраняются в uploads/ текущей задачи.

Кнопки внизу: новая задача, статус, файлы, расход, отмена, справка.

Команды:
/new [название] — новая задача и новая сессия
/status — директория и сессия
/files — файлы задачи
/usage — кредиты, квота, контекст
/cancel — остановить выполнение

Опасные операции (Bash, запись, сеть) требуют подтверждения кнопками.
Если агент задаёт вопрос, варианты приходят кнопками; свой ответ можно отправить текстом."""


@dataclass
class ChatState:
    task_dir: Path | None = None
    session_id: str | None = None
    task: QoderTask | None = None
    busy: bool = False
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


def status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=BTN_CANCEL, callback_data="act:cancel")]]
    )


def result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=BTN_FILES, callback_data="act:files"),
                InlineKeyboardButton(text=BTN_USAGE, callback_data="act:usage"),
                InlineKeyboardButton(text=BTN_NEW, callback_data="act:new"),
            ]
        ]
    )


def make_status_editor(status: Message) -> Callable[[str], Awaitable[None]]:
    last_update = 0.0

    async def update(text: str) -> None:
        nonlocal last_update
        now = time.monotonic()
        if now - last_update < 1.5:
            return
        last_update = now
        with contextlib.suppress(Exception):
            await status.edit_text(text[:MAX_TEXT], reply_markup=status_keyboard())

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


def build_router(cfg: Config, approvals: ApprovalManager, states: dict[int, ChatState]) -> Router:
    router = Router()

    def state_for(chat_id: int) -> ChatState:
        return states.setdefault(chat_id, ChatState())

    def ensure_task_dir(chat_id: int, title: str) -> Path:
        state = state_for(chat_id)
        if state.task_dir is None:
            state.task_dir = create_task_dir(cfg.workspace_root, chat_id, title)
            state.session_id = None
        return state.task_dir

    async def do_new(message: Message, title: str) -> None:
        state = state_for(message.chat.id)
        if state.busy:
            await message.answer("Сейчас идёт задача — сначала /cancel, затем новая.")
            return
        state.task_dir = create_task_dir(cfg.workspace_root, message.chat.id, title or "task")
        state.session_id = None
        await message.answer(f"🆕 Новая задача: {state.task_dir}")

    async def do_status(message: Message) -> None:
        state = state_for(message.chat.id)
        if state.task_dir is None:
            await message.answer("Задача ещё не создана — напишите запрос или нажмите «Новая задача».")
            return
        lines = [
            f"📁 {state.task_dir}",
            f"Session: {state.session_id or '—'}",
            f"Статус: {'занят' if state.busy else 'свободен'}",
            f"Запусков задач: {state.tasks_done}",
        ]
        await message.answer("\n".join(lines))

    async def do_files(message: Message) -> None:
        state = state_for(message.chat.id)
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

    async def do_usage(message: Message) -> None:
        await message.answer(usage_details(state_for(message.chat.id)))

    async def do_cancel(message: Message) -> None:
        state = state_for(message.chat.id)
        if state.task is None:
            await message.answer("Нечего отменять.")
            return
        await state.task.interrupt()
        await approvals.cancel_all(message.chat.id)
        await message.answer("⏹ Отправляю сигнал остановки.")

    async def do_help(message: Message) -> None:
        await message.answer(HELP_TEXT, reply_markup=MENU)

    async def finish_status(status: Message, text: str) -> None:
        chunks = split_text(text) or ["✅ Готово."]
        try:
            await status.edit_text(chunks[0], reply_markup=result_keyboard())
        except Exception:
            await status.bot.send_message(status.chat.id, chunks[0], reply_markup=result_keyboard())
        for chunk in chunks[1:]:
            await status.bot.send_message(status.chat.id, chunk)

    async def execute(message: Message, state: ChatState, prompt: str, files: list[str]) -> None:
        chat_id = message.chat.id
        task_dir = ensure_task_dir(chat_id, prompt)
        started = time.time()
        status = await message.answer("⏳ Запускаю Qoder…", reply_markup=status_keyboard())
        update_status = make_status_editor(status)
        typing = asyncio.create_task(keep_typing(message.bot, chat_id))

        task = QoderTask(
            config=cfg,
            cwd=task_dir,
            prompt=augment_prompt(prompt, files),
            session_id=state.session_id,
            approve=lambda tool, data, context: approvals.request(chat_id, tool, data, context),
            ask=lambda questions: approvals.ask(chat_id, questions),
            on_tool=lambda tool: update_status(f"⚙️ {tool}\n⏳ Работаю…"),
        )
        state.task = task

        error: str | None = None
        try:
            await asyncio.wait_for(task.run(), timeout=cfg.task_timeout_sec)
        except asyncio.TimeoutError:
            error = f"⏱ Превышен лимит {cfg.task_timeout_sec} с — задача остановлена."
            await approvals.cancel_all(chat_id)
        except asyncio.CancelledError:
            error = "⏹ Задача остановлена."
            await approvals.cancel_all(chat_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("Qoder task failed")
            error = f"❌ Ошибка Qoder: {exc}"
        finally:
            typing.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing
            state.task = None
            if task.session_id:
                state.session_id = task.session_id

        elapsed = time.time() - started
        state.tasks_done += 1
        state.credits_total += task.request_credits
        state.last_seconds = elapsed
        if task.usage_info:
            state.last_usage = task.usage_info
        if task.context_usage:
            state.last_context = task.context_usage

        text = task.text
        if error:
            text = f"{error}\n\n{text}" if text else error
        elif not text:
            text = "✅ Готово, но Qoder не вернул текст."

        changed = recent_files(task_dir, started)
        if changed:
            listing = "\n".join(f"• {path} — {human_size(size)}" for path, size in changed)
            text = f"{text}\n\n📝 Изменённые файлы:\n{listing}"

        text = f"{text}\n\n─────\n{usage_line(task, elapsed)}"
        await finish_status(status, text)

    async def run_prompt(message: Message, prompt: str, files: list[str]) -> None:
        state = state_for(message.chat.id)
        async with state.lock:
            if state.busy:
                await message.answer("Предыдущая задача ещё выполняется — /cancel, чтобы остановить.")
                return
            state.busy = True
            try:
                await execute(message, state, prompt, files)
            finally:
                state.busy = False
                state.task = None

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        await message.answer(HELP_TEXT, reply_markup=MENU)

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await do_help(message)

    @router.message(Command("new"))
    async def cmd_new(message: Message) -> None:
        title = (message.text or "").partition(" ")[2].strip()
        await do_new(message, title)

    @router.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        await do_status(message)

    @router.message(Command("files"))
    async def cmd_files(message: Message) -> None:
        await do_files(message)

    @router.message(Command("usage"))
    async def cmd_usage(message: Message) -> None:
        await do_usage(message)

    @router.message(Command("cancel"))
    async def cmd_cancel(message: Message) -> None:
        await do_cancel(message)

    @router.message(Command("get"))
    async def cmd_get(message: Message) -> None:
        state = state_for(message.chat.id)
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
        await do_new(message, "")

    @router.message(F.text == BTN_STATUS)
    async def btn_status(message: Message) -> None:
        await do_status(message)

    @router.message(F.text == BTN_FILES)
    async def btn_files(message: Message) -> None:
        await do_files(message)

    @router.message(F.text == BTN_USAGE)
    async def btn_usage(message: Message) -> None:
        await do_usage(message)

    @router.message(F.text == BTN_CANCEL)
    async def btn_cancel(message: Message) -> None:
        await do_cancel(message)

    @router.message(F.text == BTN_HELP)
    async def btn_help(message: Message) -> None:
        await do_help(message)

    @router.message(F.document | F.photo)
    async def on_file(message: Message) -> None:
        state = state_for(message.chat.id)
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
                f"Файл больше {cfg.max_upload_mb} МБ — Telegram Bot API не отдаст его боту."
            )
            return

        task_dir = ensure_task_dir(message.chat.id, (message.caption or "upload").strip())
        uploads = task_dir / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)

        original = getattr(file, "file_name", None) or f"photo_{int(time.time())}.jpg"
        destination = unique_path(uploads, sanitize_filename(original))
        try:
            buffer = await message.bot.download(file)
        except Exception as exc:  # noqa: BLE001
            log.warning("download failed: %s", exc)
            await message.answer("Не удалось скачать файл из Telegram.")
            return
        if buffer is None:
            await message.answer("Не удалось скачать файл из Telegram.")
            return

        destination.write_bytes(buffer.getvalue())
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
        if approvals.consume_text(message.chat.id, prompt):
            await message.answer("✍️ Ответ отправлен агенту.")
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
        if approvals.resolve(request_id, decision):
            await cb.answer("Принято")
        else:
            await cb.answer("Запрос уже неактуален", show_alert=True)

    @router.callback_query(F.data.startswith("ask:"))
    async def on_ask(cb: CallbackQuery) -> None:
        if approvals.handle_ask_callback(cb.data or ""):
            await cb.answer()
        else:
            await cb.answer("Вопрос уже неактуален", show_alert=True)

    @router.callback_query(F.data.startswith("act:"))
    async def on_action(cb: CallbackQuery) -> None:
        message = cb.message
        if not isinstance(message, Message):
            await cb.answer("Сообщение устарело — отправьте команду.", show_alert=True)
            return
        action = (cb.data or "").partition(":")[2]
        await cb.answer()
        if action == "new":
            await do_new(message, "")
        elif action == "files":
            await do_files(message)
        elif action == "usage":
            await do_usage(message)
        elif action == "cancel":
            await do_cancel(message)

    return router


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config()
    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=None))
    dp = Dispatcher()
    approvals = ApprovalManager(bot, cfg.approval_timeout_sec)
    middleware = AllowlistMiddleware(cfg.allowed_user_ids)
    dp.message.middleware(middleware)
    dp.callback_query.middleware(middleware)
    dp.include_router(build_router(cfg, approvals, {}))

    with contextlib.suppress(Exception):
        await bot.set_my_commands(BOT_COMMANDS)

    log.info("workspace: %s", cfg.workspace_root)
    log.info("permission mode: %s", cfg.permission_mode)
    await dp.start_polling(bot)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
