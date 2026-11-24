from __future__ import annotations

from pathlib import Path

import admin_view
from state_store import StateStore
from workspace import create_task_dir


def _store_with_task(tmp_path: Path) -> tuple[StateStore, Path, Path]:
    workspace = tmp_path / "ws"
    private = create_task_dir(workspace, 1, 1, "private")
    group = create_task_dir(workspace, 1, 500, "group")
    (private / "a.txt").write_text("hello", encoding="utf-8")
    store = StateStore(workspace / "state.json", workspace)
    store.set(
        1,
        1,
        {
            "task_dir": str(private),
            "session_id": "session-private-1",
            "tasks_done": 4,
            "last_active": 1700000000,
        },
    )
    store.set(
        1,
        500,
        {
            "task_dir": str(group),
            "session_id": "session-group",
            "tasks_done": 1,
            "last_active": 1700000100,
        },
    )
    return store, private, group


def test_collect_users_merges_store_and_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    create_task_dir(workspace, 2, 2, "orphan")
    store, _, _ = _store_with_task(tmp_path)

    rows = admin_view.collect_users(workspace, store)
    by_id = {row["user_id"]: row for row in rows}

    assert by_id[1]["tasks"] == 2
    assert by_id[1]["chats"] == 2
    assert by_id[1]["last_active"] == 1700000100
    assert by_id[2]["tasks"] == 1
    assert by_id[2]["chats"] == 0


def test_user_tasks_includes_session(tmp_path: Path) -> None:
    store, private, _ = _store_with_task(tmp_path)

    rows = admin_view.user_tasks(tmp_path / "ws", 1, store)

    by_path = {row["path"]: row for row in rows}
    row = by_path[str(private)]
    assert row["session_id"] == "session-private-1"
    assert row["chat_id"] == 1
    assert row["files"] == 1
    assert row["size"] == 5


def test_format_users_empty_and_rows() -> None:
    assert "Пользователей пока нет" in admin_view.format_users([])
    text = admin_view.format_users(
        [{"user_id": 1, "tasks": 2, "chats": 1, "last_active": 1700000000}]
    )
    assert "1 — задач 2" in text
    assert "чатов 1" in text


def test_format_tasks_empty_and_rows() -> None:
    assert "нет задач" in admin_view.format_tasks(1, [])
    text = admin_view.format_tasks(
        1,
        [
            {
                "path": "/x/task",
                "name": "task",
                "size": 2048,
                "files": 3,
                "mtime": 1700000000,
                "session_id": "abcdefghijklmnop",
                "chat_id": 1,
            }
        ],
    )
    assert "task" in text
    assert "/x/task" in text
    assert "abcdefgh…" in text


def test_short_session() -> None:
    assert admin_view.short_session(None) == "—"
    assert admin_view.short_session("abc") == "abc"
    assert admin_view.short_session("abcdefghijklmnop") == "abcdefgh…"
