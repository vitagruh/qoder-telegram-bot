"""Данные для админ-команд: пользователи, задачи, форматирование.

Модуль не зависит от aiogram/SDK. Бот вызывает эти функции и отправляет
готовый текст; доступ к чужим файлам не предоставляется — только метаданные.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from workspace import human_size, list_user_tasks, list_workspace_users, task_stats


def _fmt_time(value: Any) -> str:
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return "—"
    if ts <= 0:
        return "—"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except (OSError, OverflowError, ValueError):
        return "—"


def short_session(session_id: str | None) -> str:
    if not session_id:
        return "—"
    return session_id if len(session_id) <= 12 else session_id[:8] + "…"


def _store_users(store: Any) -> list[dict[str, Any]]:
    if store is None:
        return []
    try:
        return list(store.users())
    except Exception:  # noqa: BLE001 - админ-вид не должен падать
        return []


def collect_users(workspace_root: Path, store: Any = None) -> list[dict[str, Any]]:
    """Объединить state store и фактический workspace."""

    root = Path(workspace_root)
    records = {row["user_id"]: row for row in _store_users(store)}
    ids = set(list_workspace_users(root)) | set(records)
    rows: list[dict[str, Any]] = []
    for user_id in sorted(ids):
        record = records.get(user_id) or {}
        chats = record.get("chats") or []
        last_active = max(
            (float(chat.get("last_active") or 0) for chat in chats), default=0.0
        )
        rows.append(
            {
                "user_id": user_id,
                "tasks": len(list_user_tasks(root, user_id, limit=1000)),
                "chats": len(chats),
                "last_active": last_active,
            }
        )
    return rows


def user_tasks(
    workspace_root: Path,
    user_id: int,
    store: Any = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in list_user_tasks(Path(workspace_root), user_id, limit=limit):
        stats = task_stats(path)
        session_id: str | None = None
        chat_id: int | None = None
        if store is not None:
            try:
                found = store.find_by_task_dir(user_id, path)
            except Exception:  # noqa: BLE001
                found = None
            if found:
                session_id = found.get("session_id")
                chat_id = found.get("chat_id")
        rows.append({**stats, "session_id": session_id, "chat_id": chat_id})
    return rows


def format_users(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "👥 Пользователей пока нет."
    lines = ["👥 Пользователи", ""]
    for row in rows:
        lines.append(
            f"• {row['user_id']} — задач {row['tasks']}, "
            f"чатов {row['chats']}, активность {_fmt_time(row.get('last_active'))}"
        )
    return "\n".join(lines)


def format_tasks(user_id: int, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return f"📋 У пользователя {user_id} нет задач."
    lines = [f"📋 Задачи пользователя {user_id}", ""]
    for row in rows:
        session = short_session(row.get("session_id"))
        chat = row.get("chat_id")
        suffix = f", chat {chat}" if chat is not None else ""
        lines.append(
            f"• {row['name']} — {human_size(row['size'])}, "
            f"файлов {row['files']}, {_fmt_time(row['mtime'])}, сессия {session}{suffix}"
        )
        lines.append(f"  {row['path']}")
    return "\n".join(lines)
