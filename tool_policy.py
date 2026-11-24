"""Политика файловых инструментов Qoder.

Модуль не зависит от aiogram и Qoder SDK: только чистые функции, которые
решают, можно ли выполнить инструмент внутри рабочей директории задачи.
Используется дважды:

* PreToolUse-хуком в ``qoder_runner`` (жёсткая граница для абсолютных путей);
* ``can_use_tool`` (defense in depth, если хук не сработал).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

READ_TOOLS = frozenset({"Read", "Grep", "Glob", "LS", "NotebookRead"})
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
FILE_TOOLS = READ_TOOLS | WRITE_TOOLS

# Инструменты, которые нельзя автоподтверждать через ALLOWED_TOOLS:
# файловые должны проходить policy, Bash — подтверждение, AskUserQuestion —
# интерактивный диалог.
NON_AUTO_TOOLS = FILE_TOOLS | frozenset({"Bash", "AskUserQuestion"})

_PATH_KEYS = ("file_path", "path", "notebook_path")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

Action = Literal["allow", "ask", "deny"]


@dataclass(frozen=True)
class PolicyResult:
    action: Action
    input: dict[str, Any]
    reason: str | None = None


def _deny(reason: str, input_data: dict[str, Any]) -> PolicyResult:
    return PolicyResult("deny", dict(input_data), reason)


def _is_absolute(raw: str) -> bool:
    return raw.startswith(("/", "\\")) or bool(_DRIVE_RE.match(raw))


def _glob_escapes(pattern: str) -> bool:
    text = str(pattern)
    if _is_absolute(text):
        return True
    parts = re.split(r"[\\/]+", text)
    return ".." in parts


def _resolve(raw: Any, cwd: Path) -> Path | None:
    text = str(raw).strip()
    if not text:
        return None
    try:
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = cwd / path
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _within(path: Path, cwd: Path) -> bool:
    return path == cwd or cwd in path.parents


def _required_path_key(tool_name: str) -> str:
    if tool_name in {"NotebookRead", "NotebookEdit"}:
        return "notebook_path"
    return "file_path"


def evaluate_tool(
    tool_name: str,
    input_data: dict[str, Any],
    cwd: Path | str,
    force_confirm: bool = False,
    approve_writes: bool = False,
) -> PolicyResult:
    """Проверить вызов инструмента относительно рабочей директории задачи.

    ``approve_writes=True`` разрешает запись внутри cwd без подтверждения
    (режимы balanced/trusted); выход за cwd остаётся запретом. Явный
    ``force_confirm`` (ALWAYS_CONFIRM_TOOLS) всё равно возвращает ask.
    """

    data = dict(input_data or {})
    try:
        base = Path(cwd).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return _deny("Рабочая директория недоступна.", data)

    if tool_name not in FILE_TOOLS:
        # Bash и неизвестные инструменты всегда требуют подтверждения.
        return PolicyResult("ask", data)

    if tool_name in WRITE_TOOLS:
        key = _required_path_key(tool_name)
        if not data.get(key):
            return _deny(f"{tool_name}: не указан путь ({key}).", data)
        kind: Action = "allow" if approve_writes else "ask"
    else:
        # Read требует file_path; Grep/Glob/LS допускают path по умолчанию.
        kind = "allow"
        if tool_name == "Read" and not data.get("file_path"):
            return _deny("Read: не указан file_path.", data)

    if tool_name == "Glob":
        pattern = data.get("pattern")
        if pattern is not None and _glob_escapes(str(pattern)):
            return _deny("Glob: шаблон выходит за пределы рабочей директории.", data)

    # Grep/Glob/LS без path ищут по cwd и trusted directories — сужаем до cwd.
    if tool_name in {"Grep", "Glob", "LS"} and not data.get("path"):
        data["path"] = str(base)

    path_key = next((name for name in _PATH_KEYS if name in data), None)
    if path_key is None:
        if tool_name in {"Grep", "Glob", "LS"}:
            path_key = "path"
        else:
            return _deny(f"{tool_name}: не указан путь.", data)

    resolved = _resolve(data.get(path_key), base)
    if resolved is None:
        return _deny(f"{tool_name}: некорректный путь.", data)
    if not _within(resolved, base):
        return _deny(
            f"{tool_name}: путь вне рабочей директории задачи запрещён.",
            data,
        )

    if force_confirm:
        return PolicyResult("ask", data, "Инструмент требует подтверждения.")
    return PolicyResult(kind, data)


def is_path_inside(path: Path | str, base: Path | str) -> bool:
    """Публичная проверка «строго внутри» для тестов и диагностики."""

    try:
        target = Path(path).expanduser().resolve()
        root = Path(base).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return _within(target, root)


def summarize_input(tool_name: str, input_data: dict[str, Any], limit: int = 800) -> str:
    """Короткая сводка tool_input без содержимого файлов."""

    data = input_data or {}
    lines: list[str] = []
    path = data.get("file_path") or data.get("notebook_path") or data.get("path")
    if path:
        lines.append(f"путь: {path}")
    pattern = data.get("pattern")
    if pattern is not None:
        lines.append(f"шаблон: {pattern}")
    command = data.get("command")
    if isinstance(command, str):
        lines.append(f"команда: {command[:400]}")
    url = data.get("url")
    if url:
        lines.append(f"url: {str(url)[:400]}")
    query = data.get("query")
    if query:
        lines.append(f"запрос: {str(query)[:400]}")
    content = data.get("content")
    if isinstance(content, str):
        lines.append(f"содержимое: {len(content)} символов")
    if not lines:
        import json

        try:
            raw = json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError):
            raw = str(data)
        lines.append(raw[:limit] if len(raw) <= limit else raw[: limit - 1] + "…")
    return "\n".join(lines)[:limit]


__all__ = [
    "FILE_TOOLS",
    "NON_AUTO_TOOLS",
    "READ_TOOLS",
    "WRITE_TOOLS",
    "PolicyResult",
    "evaluate_tool",
    "is_path_inside",
    "summarize_input",
]
