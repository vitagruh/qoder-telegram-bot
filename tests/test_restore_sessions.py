from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import restore_sessions
from state_store import StateStore
from workspace import create_task_dir


def _main(args: list[str]) -> int:
    return restore_sessions.main(args)


def test_list_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()

    assert _main(["--workspace", str(workspace)]) == 0
    out = capsys.readouterr().out
    assert "Состояние пусто" in out


def test_scan_creates_records(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    workspace = tmp_path / "ws"
    private = create_task_dir(workspace, 1, 1, "private")
    group = create_task_dir(workspace, 2, 400, "group")

    assert _main(["--workspace", str(workspace), "--scan", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["found"] == 2

    store = StateStore(workspace / "state.json", workspace)
    assert store.get(1, 1) is not None
    assert store.get(1, 1)["task_dir"] == str(private)  # type: ignore[index]
    assert store.get(2, 400) is not None
    assert store.get(2, 400)["task_dir"] == str(group)  # type: ignore[index]


def test_scan_is_idempotent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    workspace = tmp_path / "ws"
    create_task_dir(workspace, 1, 1, "private")

    _main(["--workspace", str(workspace), "--scan", "--json"])
    capsys.readouterr()
    assert _main(["--workspace", str(workspace), "--scan", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["found"] == 0


def test_set_session_and_list(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    workspace = tmp_path / "ws"
    task = create_task_dir(workspace, 1, 1, "private")
    _main(["--workspace", str(workspace), "--scan"])
    capsys.readouterr()

    code = _main(
        [
            "--workspace",
            str(workspace),
            "--set-session",
            "--user",
            "1",
            "--chat",
            "1",
            "--session",
            "new-session-id",
            "--task-dir",
            str(task),
        ]
    )
    assert code == 0
    capsys.readouterr()

    assert _main(["--workspace", str(workspace), "--list"]) == 0
    out = capsys.readouterr().out
    assert "new-sess…" in out
    assert str(task) in out


def test_set_session_rejects_outside_task(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(SystemExit):
        _main(
            [
                "--workspace",
                str(workspace),
                "--set-session",
                "--user",
                "1",
                "--chat",
                "1",
                "--session",
                "s",
                "--task-dir",
                str(outside),
            ]
        )


def test_prune_removes_missing_task(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "ws"
    task = create_task_dir(workspace, 1, 1, "private")
    _main(["--workspace", str(workspace), "--scan"])
    capsys.readouterr()

    shutil.rmtree(task)
    assert _main(["--workspace", str(workspace), "--prune", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["removed"] == 1

    store = StateStore(workspace / "state.json", workspace)
    assert store.get(1, 1) is None


def test_requires_workspace(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "missing"
    assert _main(["--workspace", str(missing)]) == 2
    assert "не найден" in capsys.readouterr().err
