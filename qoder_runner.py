from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from qoder_agent_sdk import (
    AssistantMessage,
    PermissionResultAllow,
    PermissionResultDeny,
    QoderAgentOptions,
    QoderSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    access_token_from_env,
    qodercli_auth,
)

from approvals import Decision
from config import Config

log = logging.getLogger("qoder-bot")

# SDK по умолчанию читает не больше 1 МиБ на сообщение: один Read большого файла
# или изображения обрывает задачу с "maximum buffer size".
MAX_BUFFER_SIZE = 32 * 1024 * 1024

Approver = Callable[[str, dict[str, Any], Any], Awaitable[Decision]]
ToolObserver = Callable[[str], Awaitable[None]]
Questioner = Callable[[list[dict[str, Any]]], Awaitable[dict[str, str] | None]]


def _build_auth() -> Any:
    if os.getenv("QODER_PERSONAL_ACCESS_TOKEN", "").strip():
        return access_token_from_env()
    return qodercli_auth()


class QoderTask:
    def __init__(
        self,
        config: Config,
        cwd: Path,
        prompt: str,
        session_id: str | None,
        approve: Approver,
        ask: Questioner,
        on_tool: ToolObserver,
    ) -> None:
        self._config = config
        self._cwd = cwd
        self._prompt = prompt
        self._approve = approve
        self._ask = ask
        self._on_tool = on_tool
        self._client: QoderSDKClient | None = None
        self._texts: list[str] = []

        self.session_id = session_id
        self.tool_calls: list[str] = []
        self.result_subtype: str | None = None
        self.request_credits = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.session_credits: float | None = None
        self.model_usage: dict[str, Any] = {}
        self.usage_info: dict[str, Any] | None = None
        self.context_usage: dict[str, Any] | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(part for part in self._texts if part.strip()).strip()

    async def run(self) -> None:
        options_kwargs: dict[str, Any] = {
            "auth": _build_auth(),
            "cwd": str(self._cwd),
            "permission_mode": self._config.permission_mode,
            "allowed_tools": list(self._config.allowed_tools),
            "can_use_tool": self._can_use_tool,
        }
        if "max_buffer_size" in getattr(QoderAgentOptions, "__dataclass_fields__", {}):
            options_kwargs["max_buffer_size"] = MAX_BUFFER_SIZE
        else:
            log.warning(
                "qoder-agent-sdk без max_buffer_size: крупные сообщения могут обрывать задачу"
            )
        if self.session_id:
            options_kwargs["resume"] = self.session_id
        if self._config.permission_mode in {"bypassPermissions", "yolo"}:
            options_kwargs["allow_dangerously_skip_permissions"] = True
        if self._config.qoder_config_dir:
            options_kwargs["env"] = {
                **os.environ,
                "QODER_CONFIG_DIR": self._config.qoder_config_dir,
            }

        options = QoderAgentOptions(**options_kwargs)
        async with QoderSDKClient(options=options) as client:
            self._client = client
            try:
                await client.query(self._prompt)
                async for message in client.receive_response():
                    await self._handle(message)
                await self._collect_usage(client)
            finally:
                self._client = None

    async def _handle(self, message: Any) -> None:
        if isinstance(message, SystemMessage):
            if message.subtype == "init":
                session_id = (message.data or {}).get("session_id")
                if session_id:
                    self.session_id = session_id
            return

        if isinstance(message, AssistantMessage):
            self._collect_request_usage(message)
            chunks: list[str] = []
            for block in message.content:
                if isinstance(block, TextBlock):
                    chunks.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    self.tool_calls.append(block.name)
                    await self._on_tool(block.name)
            text = "".join(chunks)
            if text.strip():
                self._texts.append(text)
            return

        if isinstance(message, ResultMessage):
            if getattr(message, "session_id", None):
                self.session_id = message.session_id
            self.result_subtype = getattr(message, "subtype", None)
            total = getattr(message, "total_credits", None)
            if isinstance(total, (int, float)):
                self.session_credits = float(total)
            model_usage = getattr(message, "model_usage", None)
            if isinstance(model_usage, dict):
                self.model_usage = model_usage

    def _collect_request_usage(self, message: Any) -> None:
        usage = getattr(message, "usage", None)
        if not isinstance(usage, dict):
            return
        credits = usage.get("credits")
        if isinstance(credits, (int, float)):
            self.request_credits += float(credits)
        for key, attribute in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens")):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                setattr(self, attribute, getattr(self, attribute) + int(value))

    async def _collect_usage(self, client: QoderSDKClient) -> None:
        try:
            info = await asyncio.wait_for(client.get_usage_info(), timeout=5)
        except Exception:
            info = None
        if isinstance(info, dict):
            self.usage_info = info

        try:
            context = await asyncio.wait_for(client.get_context_usage(), timeout=5)
        except Exception:
            context = None
        if isinstance(context, dict):
            self.context_usage = context

    async def _can_use_tool(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        context: Any,
    ) -> Any:
        if tool_name == "AskUserQuestion":
            questions = list(input_data.get("questions") or [])
            try:
                answers = await self._ask(questions)
            except Exception:
                answers = None
            if not answers:
                return PermissionResultDeny(message="Пользователь не ответил на вопросы.")
            return PermissionResultAllow(
                updated_input={"questions": questions, "answers": answers}
            )

        try:
            decision = await self._approve(tool_name, input_data, context)
        except Exception:
            return PermissionResultDeny(message="Не удалось получить подтверждение пользователя.")

        if decision is Decision.ALLOW:
            return PermissionResultAllow(updated_input=input_data)
        if decision is Decision.ALWAYS:
            return PermissionResultAllow(
                updated_input=input_data,
                updated_permissions=getattr(context, "suggestions", None),
            )
        return PermissionResultDeny(message="Пользователь отклонил операцию.")

    async def interrupt(self) -> None:
        client = self._client
        if client is None:
            return
        try:
            await client.interrupt()
        except Exception:
            pass
