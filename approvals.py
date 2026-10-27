from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class Decision(str, Enum):
    ALLOW = "allow"
    ALWAYS = "always"
    DENY = "deny"


@dataclass
class _Pending:
    future: asyncio.Future[Decision]
    chat_id: int
    header: str
    message_id: int | None = None


@dataclass
class _AskFlow:
    chat_id: int
    queue: asyncio.Queue[tuple[str, Any]]


class ApprovalManager:
    """Bridges Qoder approval callbacks and AskUserQuestion to Telegram UI."""

    def __init__(self, bot: Bot, timeout: float) -> None:
        self._bot = bot
        self._timeout = timeout
        self._pending: dict[str, _Pending] = {}
        self._ask_flows: dict[str, _AskFlow] = {}
        self._text_targets: dict[int, str] = {}

    async def request(
        self,
        chat_id: int,
        tool_name: str,
        input_data: dict[str, Any],
        context: Any,
    ) -> Decision:
        signal = getattr(context, "signal", None)
        if signal is not None and getattr(signal, "is_set", lambda: False)():
            return Decision.DENY

        request_id = uuid.uuid4().hex
        pending = _Pending(
            future=asyncio.get_running_loop().create_future(),
            chat_id=chat_id,
            header=self._header(tool_name, input_data, context),
        )
        self._pending[request_id] = pending
        try:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Разрешить",
                            callback_data=f"apv:{request_id}:allow",
                        ),
                        InlineKeyboardButton(
                            text="♾ В сессии",
                            callback_data=f"apv:{request_id}:always",
                        ),
                        InlineKeyboardButton(
                            text="⛔ Отклонить",
                            callback_data=f"apv:{request_id}:deny",
                        ),
                    ]
                ]
            )
            sent = await self._bot.send_message(chat_id, pending.header, reply_markup=keyboard)
            pending.message_id = sent.message_id

            waiters: list[asyncio.Future[Any]] = [pending.future]
            signal_task: asyncio.Task[Any] | None = None
            wait_signal = getattr(signal, "wait", None)
            if wait_signal is not None:
                signal_task = asyncio.ensure_future(wait_signal())
                waiters.append(signal_task)

            try:
                done, _ = await asyncio.wait(
                    waiters,
                    timeout=self._timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                if signal_task is not None and not signal_task.done():
                    signal_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await signal_task

            if signal_task is not None and signal_task in done:
                decision, note = Decision.DENY, "⏹ Запрос отменён — отклонено"
            elif pending.future in done and not pending.future.cancelled():
                decision = pending.future.result()
                note = {
                    Decision.ALLOW: "✅ Разрешено",
                    Decision.ALWAYS: "♾ Разрешено на сессию",
                    Decision.DENY: "⛔ Отклонено",
                }[decision]
            else:
                decision, note = Decision.DENY, "⏱ Время ожидания истекло — отклонено"

            await self._finish(pending, note)
            return decision
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, request_id: str, decision: Decision) -> bool:
        pending = self._pending.get(request_id)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(decision)
        return True

    async def cancel_all(self, chat_id: int | None = None) -> None:
        for pending in list(self._pending.values()):
            if chat_id is not None and pending.chat_id != chat_id:
                continue
            if not pending.future.done():
                pending.future.set_result(Decision.DENY)
        for flow in list(self._ask_flows.values()):
            if chat_id is not None and flow.chat_id != chat_id:
                continue
            flow.queue.put_nowait(("cancel", -1))

    async def ask(self, chat_id: int, questions: list[dict[str, Any]]) -> dict[str, str] | None:
        if not questions:
            return None
        request_id = uuid.uuid4().hex[:10]
        flow = _AskFlow(chat_id=chat_id, queue=asyncio.Queue())
        self._ask_flows[request_id] = flow
        self._text_targets[chat_id] = request_id
        answers: dict[str, str] = {}
        try:
            for question in questions:
                answer = await self._ask_one(request_id, flow, question)
                if answer is None:
                    return None
                answers[str(question.get("question", ""))] = answer
            return answers
        finally:
            self._ask_flows.pop(request_id, None)
            if self._text_targets.get(chat_id) == request_id:
                self._text_targets.pop(chat_id, None)

    def consume_text(self, chat_id: int, text: str) -> bool:
        request_id = self._text_targets.get(chat_id)
        flow = self._ask_flows.get(request_id) if request_id else None
        if flow is None:
            return False
        flow.queue.put_nowait(("text", text))
        return True

    def handle_ask_callback(self, callback_data: str) -> bool:
        parts = callback_data.split(":")
        if len(parts) != 4:
            return False
        _, request_id, action, raw = parts
        flow = self._ask_flows.get(request_id)
        if flow is None:
            return False
        try:
            index = int(raw)
        except ValueError:
            index = -1
        flow.queue.put_nowait((action, index))
        return True

    async def _ask_one(self, request_id: str, flow: _AskFlow, question: dict[str, Any]) -> str | None:
        options = question.get("options") or []
        multi = bool(question.get("multiSelect"))
        selected: set[int] = set()
        body = self._question_text(question)
        try:
            sent = await self._bot.send_message(
                flow.chat_id,
                body,
                reply_markup=self._ask_keyboard(request_id, options, selected, multi),
            )
        except Exception:
            return None
        message_id = sent.message_id

        while True:
            try:
                action, index = await asyncio.wait_for(flow.queue.get(), timeout=self._timeout)
            except asyncio.TimeoutError:
                return None

            if action == "cancel":
                return None
            if action == "select" and 0 <= index < len(options):
                answer = str(options[index].get("label", index + 1))
                await self._close_question(flow.chat_id, message_id, body, answer)
                return answer
            if action == "toggle" and 0 <= index < len(options):
                if index in selected:
                    selected.discard(index)
                else:
                    selected.add(index)
                with contextlib.suppress(Exception):
                    await self._bot.edit_message_reply_markup(
                        chat_id=flow.chat_id,
                        message_id=message_id,
                        reply_markup=self._ask_keyboard(request_id, options, selected, multi),
                    )
                continue
            if action == "done" and selected:
                answer = ", ".join(
                    str(options[i].get("label", i + 1)) for i in sorted(selected)
                )
                await self._close_question(flow.chat_id, message_id, body, answer)
                return answer
            if action == "text":
                answer = str(index).strip()
                if answer:
                    await self._close_question(flow.chat_id, message_id, body, answer)
                    return answer

    def _ask_keyboard(
        self,
        request_id: str,
        options: list[dict[str, Any]],
        selected: set[int],
        multi: bool,
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        for index, option in enumerate(options):
            mark = "✅ " if index in selected else ""
            action = "toggle" if multi else "select"
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{mark}{option.get('label', str(index + 1))}"[:60],
                        callback_data=f"ask:{request_id}:{action}:{index}",
                    )
                ]
            )
        if multi:
            rows.append(
                [InlineKeyboardButton(text="Готово", callback_data=f"ask:{request_id}:done:-1")]
            )
        rows.append(
            [InlineKeyboardButton(text="⛔ Отмена", callback_data=f"ask:{request_id}:cancel:-1")]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _question_text(self, question: dict[str, Any]) -> str:
        lines = [f"❓ {question.get('header') or 'Вопрос'}", str(question.get("question", ""))]
        options = question.get("options") or []
        if options:
            lines.append("")
            for index, option in enumerate(options, start=1):
                label = option.get("label", str(index))
                description = option.get("description")
                lines.append(f"{index}. {label} — {description}" if description else f"{index}. {label}")
            lines.append("")
            lines.append("Выберите вариант или отправьте свой ответ текстом.")
        return "\n".join(lines)[:3900]

    async def _close_question(self, chat_id: int, message_id: int, body: str, answer: str) -> None:
        with contextlib.suppress(Exception):
            await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"{body}\n\n✅ {answer}"[:3900],
                reply_markup=None,
            )

    def _header(self, tool_name: str, input_data: dict[str, Any], context: Any) -> str:
        lines = ["🔐 Qoder запрашивает разрешение", f"Инструмент: {tool_name}"]
        description = getattr(context, "description", None)
        if description:
            lines.append(str(description))
        try:
            payload = json.dumps(input_data, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            payload = str(input_data)
        if payload:
            if len(payload) > 2500:
                payload = payload[:2500] + "…"
            lines.extend(["Ввод:", payload])
        return "\n".join(lines)[:3900]

    async def _finish(self, pending: _Pending, note: str) -> None:
        if pending.message_id is None:
            return
        try:
            await self._bot.edit_message_text(
                chat_id=pending.chat_id,
                message_id=pending.message_id,
                text=f"{pending.header}\n\n{note}"[:3900],
                reply_markup=None,
            )
        except Exception:
            pass
