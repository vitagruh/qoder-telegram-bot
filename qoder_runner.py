from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from qoder_agent_sdk import (
    AssistantMessage,
    HookMatcher,
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
from config import NETWORK_TOOLS, Config
from env_policy import build_child_env
from safe_commands import is_safe_command
from tool_policy import FILE_TOOLS, evaluate_tool

log = logging.getLogger("qoder-bot")

# Matcher PreToolUse-хука: только файловые инструменты, которые должны
# проверяться на выход за пределы cwd. Для остальных действует can_use_tool.
FILE_TOOL_MATCHER = "|".join(sorted(FILE_TOOLS))


def _hook_output(
    decision: str,
    reason: str | None = None,
    updated_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
    }
    if reason:
        output["permissionDecisionReason"] = reason
    if updated_input is not None:
        output["updatedInput"] = updated_input
    return {"hookSpecificOutput": output}

# SDK по умолчанию читает не больше 1 МиБ на сообщение: один Read большого файла
# или изображения обрывает задачу с "maximum buffer size".
MAX_BUFFER_SIZE = 32 * 1024 * 1024

Approver = Callable[[str, dict[str, Any], Any, bool], Awaitable[Decision]]
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
            log.warning(
                "PERMISSION_MODE=%s отключает все подтверждения, "
                "включая WebFetch/WebSearch. Используйте только в изоляции.",
                self._config.permission_mode,
            )
            options_kwargs["allow_dangerously_skip_permissions"] = True

        # В дочерний процесс не попадают токен бота и прочие секреты
        # родительского окружения; см. env_policy.py.
        options_kwargs["env"] = build_child_env(self._config.qoder_config_dir)

        # Жёсткая граница для файловых инструментов: абсолютные пути,
        # симлинки и выход из cwd отклоняются до выполнения.
        hook_callback: Any = self._pre_tool_use
        options_kwargs["hooks"] = {
            "PreToolUse": [
                HookMatcher(matcher=FILE_TOOL_MATCHER, hooks=[hook_callback])
            ]
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
        async def fetch(coro: Awaitable[Any], label: str) -> Any:
            try:
                return await asyncio.wait_for(coro, timeout=5)
            except Exception as exc:  # noqa: BLE001 - использование не критично
                log.debug("%s недоступен: %s", label, exc)
                return None

        info, context = await asyncio.gather(
            fetch(client.get_usage_info(), "usage info"),
            fetch(client.get_context_usage(), "context usage"),
        )
        if isinstance(info, dict):
            self.usage_info = info
        if isinstance(context, dict):
            self.context_usage = context

    async def _pre_tool_use(
        self,
        input_data: Any,
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        """PreToolUse: не выпускать файловые инструменты за пределы cwd."""

        try:
            tool_name = str(input_data.get("tool_name") or "")
            if tool_name not in FILE_TOOLS:
                return {}
            tool_input = input_data.get("tool_input") or {}
            result = evaluate_tool(
                tool_name,
                tool_input,
                self._cwd,
                force_confirm=self._config.requires_confirmation(tool_name),
                approve_writes=self._config.approval_mode in {"balanced", "trusted"},
            )
            if result.action == "deny":
                log.warning("tool policy denied %s: %s", tool_name, result.reason)
                return _hook_output("deny", result.reason or "Операция запрещена.")
            if result.action == "allow":
                return _hook_output("allow", result.reason, result.input)
            # "ask" оставляем can_use_tool, чтобы не дублировать подтверждение.
            return {}
        except Exception as exc:  # noqa: BLE001 - fail closed
            log.warning("PreToolUse hook failed: %s", exc)
            return _hook_output("deny", "Политика инструментов недоступна.")

    async def _can_use_tool(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        context: Any,
    ) -> Any:
        signal = getattr(context, "signal", None)
        if signal is not None and getattr(signal, "is_set", lambda: False)():
            return PermissionResultDeny(message="Задача остановлена.")

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

        requires_confirm = self._config.requires_confirmation(tool_name)
        trusted = self._config.approval_mode == "trusted"
        is_bash = tool_name.casefold() == "bash"
        is_network = tool_name.casefold() in {
            name.casefold() for name in NETWORK_TOOLS
        }

        # Сеть без подтверждения: trusted или AUTO_APPROVE_NETWORK=1.
        if is_network and not requires_confirm:
            return PermissionResultAllow(updated_input=input_data)

        # trusted: любой Bash без подтверждения, если ALWAYS_CONFIRM_TOOLS
        # не требует обратного. Файловые инструменты всё равно проходят policy.
        if trusted and not requires_confirm and is_bash:
            return PermissionResultAllow(updated_input=input_data)

        # Безопасные Bash-команды могут выполняться без подтверждения
        # (режим SAFE_BASH_MODE); policy их не отменяет.
        if (
            is_bash
            and not requires_confirm
            and self._config.safe_bash_mode != "off"
            and not input_data.get("dangerouslyDisableSandbox")
        ):
            command = input_data.get("command")
            if isinstance(command, str) and is_safe_command(
                command,
                self._cwd,
                allow_dev=self._config.safe_bash_mode == "dev",
            ):
                log.info("auto-approved Bash: %s", command[:300])
                return PermissionResultAllow(updated_input=input_data)

        # Defense in depth: даже если хук не сработал, файловые инструменты
        # проверяются здесь (allow/ask/deny). В balanced/trusted запись внутри
        # cwd разрешена, выход за cwd остаётся deny.
        policy = evaluate_tool(
            tool_name,
            input_data,
            self._cwd,
            force_confirm=requires_confirm,
            approve_writes=self._config.approval_mode in {"balanced", "trusted"},
        )
        if policy.action == "deny":
            log.warning("tool policy denied %s: %s", tool_name, policy.reason)
            return PermissionResultDeny(
                message=policy.reason or "Операция запрещена политикой."
            )
        if policy.action == "allow":
            return PermissionResultAllow(updated_input=policy.input)
        input_data = policy.input

        # trusted: остальные (не файловые) инструменты без подтверждения.
        if trusted and not requires_confirm:
            return PermissionResultAllow(updated_input=input_data)

        # Для ALWAYS_CONFIRM_TOOLS и обязательных сетевых инструментов
        # «разрешить на сессию» не предлагаем.
        allow_session = not requires_confirm

        try:
            decision = await self._approve(tool_name, input_data, context, allow_session)
        except Exception:
            return PermissionResultDeny(message="Не удалось получить подтверждение пользователя.")

        if decision is Decision.ALLOW:
            return PermissionResultAllow(updated_input=input_data)
        if decision is Decision.ALWAYS:
            return PermissionResultAllow(
                updated_input=input_data,
                updated_permissions=(
                    getattr(context, "suggestions", None) if allow_session else None
                ),
            )
        return PermissionResultDeny(message="Пользователь отклонил операцию.")

    async def interrupt(self) -> None:
        client = self._client
        if client is None:
            return
        try:
            await client.interrupt()
        except Exception as exc:  # noqa: BLE001
            log.debug("interrupt failed: %s", exc)
