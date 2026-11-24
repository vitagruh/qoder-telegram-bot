from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from state_store import StateStore
from workspace import create_task_dir


def _record(task_dir: Path, session: str = "sess-1") -> dict[str, object]:
    return {
        "task_dir": str(task_dir),
        "session_id": session,
        "tasks_done": 2,
        "credits_total": 1.5,
        "last_seconds": 10,
        "last_active": 1700000000.0,
        "last_usage": {"userQuota": {"remaining": 5}},
        "last_context": {"contextWindow": {"usedPercentage": 40}},
    }


def test_set_get_save_load_roundtrip(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    task_dir = create_task_dir(workspace, 1, 1, "demo")
    path = workspace / "state.json"

    store = StateStore(path, workspace)
    store.set(1, 1, _record(task_dir))
    store.save()

    reloaded = StateStore(path, workspace)
    record = reloaded.get(1, 1)
    assert record is not None
    assert record["session_id"] == "sess-1"
    assert record["tasks_done"] == 2
    assert record["task_dir"] == str(task_dir)


def test_state_file_is_private(tmp_path: Path) -> None:
    if os.name != "posix":
        # Windows не хранит POSIX-биты; проверка выполняется в CI на Linux.
        return
    workspace = tmp_path / "ws"
    task_dir = create_task_dir(workspace, 1, 1, "demo")
    store = StateStore(workspace / "state.json", workspace)
    store.set(1, 1, _record(task_dir))
    store.save()

    assert (workspace / "state.json").stat().st_mode & 0o077 == 0


def test_backup_used_when_main_file_corrupt(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    task_dir = create_task_dir(workspace, 1, 1, "demo")
    path = workspace / "state.json"
    store = StateStore(path, workspace)
    store.set(1, 1, _record(task_dir))
    store.save()
    store.set(1, 1, _record(task_dir, session="sess-2"))
    store.save()  # создаёт .bak с первой версией

    path.write_text("{ broken", encoding="utf-8")
    reloaded = StateStore(path, workspace)

    assert reloaded.loaded_from_backup is True
    record = reloaded.get(1, 1)
    assert record is not None
    assert record["session_id"] == "sess-1"


def test_save_after_backup_load_keeps_last_good_backup(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    first = create_task_dir(workspace, 1, 1, "first")
    second = create_task_dir(workspace, 1, 1, "second")
    path = workspace / "state.json"
    store = StateStore(path, workspace)
    store.set(1, 1, _record(first))
    store.save()
    store.set(1, 1, _record(first, session="sess-2"))
    store.save()  # .bak = sess-1

    path.write_text("{ broken", encoding="utf-8")
    reloaded = StateStore(path, workspace)
    assert reloaded.loaded_from_backup is True
    reloaded.set(1, 1, _record(second, session="sess-3"))
    reloaded.save()

    backup = json.loads((workspace / "state.json.bak").read_text(encoding="utf-8"))
    main = json.loads(path.read_text(encoding="utf-8"))
    assert backup["users"]["1"]["chats"]["1"]["session_id"] == "sess-1"
    assert main["users"]["1"]["chats"]["1"]["session_id"] == "sess-3"


def test_invalid_task_dir_is_rejected_on_set(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    store = StateStore(workspace / "state.json", workspace)

    with pytest.raises(ValueError):
        store.set(1, 1, {"task_dir": str(outside)})


def test_invalid_task_dir_dropped_on_load(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    path = workspace / "state.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "users": {
                    "1": {
                        "chats": {
                            "1": {
                                "task_dir": str(outside),
                                "session_id": "bad",
                                "tasks_done": 3,
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    store = StateStore(path, workspace)
    record = store.get(1, 1)
    assert record is not None
    assert record["task_dir"] is None
    assert record["session_id"] is None
    assert record["tasks_done"] == 3


def test_users_include_chat_id(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    task_dir = create_task_dir(workspace, 5, 100, "group")
    store = StateStore(workspace / "state.json", workspace)
    store.set(5, 100, _record(task_dir))

    users = store.users()
    assert users[0]["user_id"] == 5
    assert users[0]["chats"][0]["chat_id"] == 100


def test_prune_removes_records_without_task_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    task_dir = create_task_dir(workspace, 1, 1, "demo")
    store = StateStore(workspace / "state.json", workspace)
    store.set(1, 1, _record(task_dir))
    store.set(2, 2, {"session_id": "orphan"})

    assert store.prune() == 1
    assert store.get(2, 2) is None
    assert store.get(1, 1) is not None


def test_find_by_task_dir_and_ensure(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    task_dir = create_task_dir(workspace, 1, 1, "demo")
    store = StateStore(workspace / "state.json", workspace)

    created = store.ensure(1, 1, task_dir)
    assert created["task_dir"] == str(task_dir)

    found = store.find_by_task_dir(1, task_dir)
    assert found is not None
    assert found["chat_id"] == 1
