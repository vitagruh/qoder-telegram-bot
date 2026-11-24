from __future__ import annotations

import time
from pathlib import Path

from workspace import (
    create_task_dir,
    is_task_dir,
    list_user_tasks,
    list_workspace_users,
    scan_task_dirs,
    task_ref,
    task_stats,
)


def test_private_and_group_layouts(tmp_path: Path) -> None:
    private = create_task_dir(tmp_path, 5, 5, "private task")
    group = create_task_dir(tmp_path, 5, 100, "group task")

    assert private.parent.parent.name == "chat_5"
    assert group.parent.parent.name == "user_5"
    assert group.parent.parent.parent.name == "chat_100"
    assert private != group


def test_is_task_dir(tmp_path: Path) -> None:
    task = create_task_dir(tmp_path, 5, 100, "demo")
    assert is_task_dir(tmp_path, task) is True
    assert is_task_dir(tmp_path, tmp_path / "chat_5" / "tasks") is False
    assert is_task_dir(tmp_path, tmp_path) is False
    assert is_task_dir(tmp_path, task.parent) is False

    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    assert is_task_dir(tmp_path, outside) is False


def test_task_ref_is_stable_and_short(tmp_path: Path) -> None:
    task = create_task_dir(tmp_path, 1, 1, "demo")
    other = create_task_dir(tmp_path, 1, 1, "other")

    first = task_ref(task)
    assert first == task_ref(task)
    assert len(first) == 10
    assert first != task_ref(other)


def test_list_user_tasks_merges_private_and_group(tmp_path: Path) -> None:
    private = create_task_dir(tmp_path, 7, 7, "private")
    group = create_task_dir(tmp_path, 7, 200, "group")
    foreign = create_task_dir(tmp_path, 8, 8, "foreign")

    found = list_user_tasks(tmp_path, 7)

    assert set(found) == {private, group}
    assert foreign not in found


def test_list_user_tasks_sorted_and_limited(tmp_path: Path) -> None:
    import os

    first = create_task_dir(tmp_path, 7, 7, "one")
    second = create_task_dir(tmp_path, 7, 7, "two")
    old = time.time() - 100
    os.utime(first, (old, old))

    found = list_user_tasks(tmp_path, 7, limit=1)
    assert found == [second]


def test_list_workspace_users(tmp_path: Path) -> None:
    create_task_dir(tmp_path, 1, 1, "private")
    create_task_dir(tmp_path, 2, 300, "group")
    create_task_dir(tmp_path, 3, 300, "group")

    assert list_workspace_users(tmp_path) == [1, 2, 3]


def test_task_stats(tmp_path: Path) -> None:
    task = create_task_dir(tmp_path, 1, 1, "demo")
    (task / "a.txt").write_text("hello", encoding="utf-8")
    (task / "uploads" / "b.txt").write_text("world!", encoding="utf-8")

    stats = task_stats(task)
    assert stats["files"] == 2
    assert stats["size"] == 11
    assert stats["name"] == task.name


def test_scan_task_dirs_recovers_owner(tmp_path: Path) -> None:
    private = create_task_dir(tmp_path, 1, 1, "private")
    group = create_task_dir(tmp_path, 2, 400, "group")

    found = {(item["user_id"], item["chat_id"], item["path"]) for item in scan_task_dirs(tmp_path)}
    assert found == {(1, 1, private), (2, 400, group)}
