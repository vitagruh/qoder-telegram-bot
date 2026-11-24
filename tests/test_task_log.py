from __future__ import annotations

import os
from pathlib import Path

import pytest

import task_log
from task_log import TaskLog, format_entry


def test_append_and_read_recent(tmp_path: Path) -> None:
    log = TaskLog(tmp_path)
    log.append(7, {"event": "start", "task": "abc", "prompt": "hello"})
    log.append(7, {"event": "end", "ok": True, "credits": 1.25})

    entries = log.read_recent(7)

    assert [entry["event"] for entry in entries] == ["start", "end"]
    assert entries[0]["user"] == 7
    assert entries[0]["ts"] > 0


def test_read_recent_limit(tmp_path: Path) -> None:
    log = TaskLog(tmp_path)
    for index in range(5):
        log.append(1, {"event": "tool", "tool": f"T{index}"})

    assert len(log.read_recent(1, limit=2)) == 2
    assert log.read_recent(1, limit=2)[-1]["tool"] == "T4"
    assert log.read_recent(999) == []


def test_redacts_known_tokens(tmp_path: Path) -> None:
    log = TaskLog(tmp_path)
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"
    log.append(1, {"event": "start", "prompt": f"token {token} sk-abcdefghijklmnop1234"})

    raw = (tmp_path / "logs" / "users" / "user_1.jsonl").read_text(encoding="utf-8")
    assert token not in raw
    assert "sk-abcdefghijklmnop1234" not in raw
    assert "***" in raw


def test_prompt_and_error_are_truncated(tmp_path: Path) -> None:
    log = TaskLog(tmp_path)
    log.append(1, {"event": "start", "prompt": "p" * 1000})
    log.append(1, {"event": "end", "error": "e" * 2000})

    entries = log.read_recent(1)
    assert len(entries[0]["prompt"]) <= task_log.PROMPT_LIMIT
    assert len(entries[1]["error"]) <= task_log.ERROR_LIMIT


def test_rotation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_log, "MAX_BYTES", 120)
    log = TaskLog(tmp_path)
    log.append(1, {"event": "start", "prompt": "x" * 100})
    log.append(1, {"event": "end", "ok": True})

    base = tmp_path / "logs" / "users" / "user_1.jsonl"
    assert base.exists()
    assert (tmp_path / "logs" / "users" / "user_1.jsonl.1").exists()
    assert len(log.read_recent(1, limit=10)) == 2


def test_log_files_are_private(tmp_path: Path) -> None:
    if os.name != "posix":
        # Windows не хранит POSIX-биты; проверка выполняется в CI на Linux.
        return
    log = TaskLog(tmp_path)
    log.append(1, {"event": "start"})

    base = tmp_path / "logs" / "users" / "user_1.jsonl"
    assert base.stat().st_mode & 0o077 == 0
    assert (tmp_path / "logs" / "users").stat().st_mode & 0o077 == 0


def test_format_entry() -> None:
    line = format_entry(
        {
            "ts": 1700000000,
            "event": "end",
            "task": "abc",
            "ok": False,
            "credits": 2,
            "elapsed": 12.5,
            "files": 3,
            "error": "boom",
        }
    )
    assert "end" in line
    assert "task=abc" in line
    assert "fail" in line
    assert "credits=2" in line
    assert "error=boom" in line
