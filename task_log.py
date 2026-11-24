"""Журнал действий пользователей (JSONL) для админ-команд.

Файлы: ``WORKSPACE_ROOT/logs/users/user_<uid>.jsonl`` (0600, каталоги 0700).
Пишутся только метаданные задач и усечённые prompt/ошибки; env, токены и
содержимое tool_input в журнал не попадают. Известные токен-паттерны
редактируются.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

MAX_BYTES = 2 * 1024 * 1024
PROMPT_LIMIT = 300
ERROR_LIMIT = 500

_SECRET_PATTERNS = (
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{25,}\b"),  # Telegram bot token
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
)

_MASK = "***"


def redact(text: str) -> str:
    result = str(text)
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(_MASK, result)
    return result


def _truncate(text: Any, limit: int) -> str:
    value = redact(str(text))
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _clean(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return _MASK
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {str(key): _clean(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item, depth + 1) for item in value[:50]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact(str(value))


def _normalize(uid: int, event: dict[str, Any]) -> dict[str, Any]:
    data = _clean(dict(event or {}))
    data.setdefault("ts", round(time.time(), 3))
    data.setdefault("user", uid)
    if "prompt" in data:
        data["prompt"] = _truncate(data["prompt"], PROMPT_LIMIT)
    if "error" in data and data["error"]:
        data["error"] = _truncate(data["error"], ERROR_LIMIT)
    if "command" in data and data["command"]:
        data["command"] = _truncate(data["command"], PROMPT_LIMIT)
    return data


class TaskLog:
    def __init__(self, workspace_root: Path) -> None:
        self.root = Path(workspace_root) / "logs" / "users"

    def _path(self, uid: int) -> Path:
        return self.root / f"user_{int(uid)}.jsonl"

    def _ensure_dir(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            self.root.chmod(0o700)
        with contextlib.suppress(OSError):
            self.root.parent.chmod(0o700)

    def _rotate(self, path: Path) -> None:
        try:
            if path.is_file() and path.stat().st_size >= MAX_BYTES:
                os.replace(path, path.with_suffix(path.suffix + ".1"))
        except OSError:
            pass

    def append(self, uid: int, event: dict[str, Any]) -> None:
        self._ensure_dir()
        path = self._path(uid)
        self._rotate(path)
        line = json.dumps(_normalize(uid, event), ensure_ascii=False) + "\n"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            # Журнал не должен ломать выполнение задачи.
            pass

    def read_recent(self, uid: int, limit: int = 20) -> list[dict[str, Any]]:
        path = self._path(uid)
        entries: list[dict[str, Any]] = []
        for candidate in (path.with_suffix(path.suffix + ".1"), path):
            try:
                lines = candidate.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    entries.append(parsed)
        if limit <= 0:
            return []
        return entries[-limit:]


def format_entry(entry: dict[str, Any]) -> str:
    ts = entry.get("ts")
    stamp = "—"
    if isinstance(ts, (int, float)):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    event = entry.get("event") or "?"
    parts = [stamp, str(event)]
    for key in ("task", "chat", "tool", "decision", "command", "prompt"):
        value = entry.get(key)
        if value:
            parts.append(f"{key}={value}")
    if "ok" in entry:
        parts.append("ok" if entry.get("ok") else "fail")
    for key in ("credits", "elapsed", "files"):
        value = entry.get(key)
        if value:
            parts.append(f"{key}={value}")
    error = entry.get("error")
    if error:
        parts.append(f"error={error}")
    line = " · ".join(parts)
    return line if len(line) <= 900 else line[:899] + "…"
