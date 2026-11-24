from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from approvals import Decision
from config import Config
from qoder_runner import QoderTask


def _config(**overrides: Any) -> Config:
    values: dict[str, Any] = {
        "bot_token": "123:secret",
        "allowed_user_ids": frozenset({1}),
        "admin_user_ids": frozenset(),
        "workspace_root": Path(".").resolve(),
        "permission_mode": "default",
        "approval_mode": "balanced",
        "auto_approve_network": False,
        "allowed_tools": (),
        "always_confirm_tools": frozenset(),
        "safe_bash_mode": "off",
        "max_upload_mb": 20,
        "task_timeout_sec": 1200,
        "approval_timeout_sec": 120,
        "max_concurrent_tasks": 1,
        "qoder_config_dir": None,
    }
    values.update(overrides)
    return Config(**values)


class Recorder:
    def __init__(self, decision: Decision = Decision.ALLOW) -> None:
        self.decision = decision
        self.calls: list[tuple[str, bool]] = []

    async def approve(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        context: Any,
        allow_session: bool,
    ) -> Decision:
        self.calls.append((tool_name, allow_session))
        return self.decision


def _task(tmp_path: Path, config: Config, recorder: Recorder) -> QoderTask:
    task_dir = tmp_path / "task"
    task_dir.mkdir(exist_ok=True)

    async def ask(questions: list[dict[str, Any]]) -> dict[str, str] | None:
        return None

    async def on_tool(name: str) -> None:
        return None

    return QoderTask(
        config,
        task_dir,
        "prompt",
        None,
        recorder.approve,
        ask,
        on_tool,
    )


def _run(task: QoderTask, tool: str, data: dict[str, Any]) -> Any:
    context = SimpleNamespace(signal=None, suggestions=None)
    return asyncio.run(task._can_use_tool(tool, data, context))


def test_balanced_write_inside_allowed_without_approval(tmp_path: Path) -> None:
    recorder = Recorder()
    task = _task(tmp_path, _config(), recorder)

    result = _run(task, "Write", {"file_path": "out.txt", "content": "x"})

    assert result.behavior == "allow"
    assert recorder.calls == []


def test_balanced_write_outside_denied(tmp_path: Path) -> None:
    recorder = Recorder()
    task = _task(tmp_path, _config(), recorder)

    result = _run(task, "Write", {"file_path": "../escape.txt", "content": "x"})

    assert result.behavior == "deny"
    assert recorder.calls == []


def test_strict_write_asks(tmp_path: Path) -> None:
    recorder = Recorder()
    task = _task(tmp_path, _config(approval_mode="strict"), recorder)

    result = _run(task, "Write", {"file_path": "out.txt", "content": "x"})

    assert result.behavior == "allow"
    assert recorder.calls == [("Write", True)]


def test_balanced_network_asks(tmp_path: Path) -> None:
    recorder = Recorder()
    task = _task(tmp_path, _config(), recorder)

    result = _run(task, "WebFetch", {"url": "https://example.com"})

    assert result.behavior == "allow"
    assert recorder.calls == [("WebFetch", False)]


def test_auto_approve_network_skips_approval(tmp_path: Path) -> None:
    recorder = Recorder()
    task = _task(tmp_path, _config(auto_approve_network=True), recorder)

    result = _run(task, "WebFetch", {"url": "https://example.com"})

    assert result.behavior == "allow"
    assert recorder.calls == []


def test_trusted_bash_allowed_without_approval(tmp_path: Path) -> None:
    recorder = Recorder()
    task = _task(tmp_path, _config(approval_mode="trusted"), recorder)

    result = _run(task, "Bash", {"command": "rm -rf build"})

    assert result.behavior == "allow"
    assert recorder.calls == []


def test_always_confirm_still_asks_in_trusted(tmp_path: Path) -> None:
    recorder = Recorder()
    config = _config(approval_mode="trusted", always_confirm_tools=frozenset({"Bash"}))
    task = _task(tmp_path, config, recorder)

    result = _run(task, "Bash", {"command": "rm -rf build"})

    assert result.behavior == "allow"
    assert recorder.calls == [("Bash", False)]


def test_trusted_unknown_tool_allowed(tmp_path: Path) -> None:
    recorder = Recorder()
    task = _task(tmp_path, _config(approval_mode="trusted"), recorder)

    result = _run(task, "Mystery", {})

    assert result.behavior == "allow"
    assert recorder.calls == []


def test_balanced_unknown_tool_asks(tmp_path: Path) -> None:
    recorder = Recorder()
    task = _task(tmp_path, _config(), recorder)

    result = _run(task, "Mystery", {})

    assert result.behavior == "allow"
    assert recorder.calls == [("Mystery", True)]


def test_outside_path_denied_even_in_trusted(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("secret", encoding="utf-8")
    recorder = Recorder()
    task = _task(tmp_path, _config(approval_mode="trusted"), recorder)

    result = _run(task, "Read", {"file_path": str(outside / "secret")})

    assert result.behavior == "deny"
    assert recorder.calls == []


def test_pre_tool_use_honours_approval_mode(tmp_path: Path) -> None:
    task = _task(tmp_path, _config(), Recorder())
    hook_input = {
        "tool_name": "Write",
        "tool_input": {"file_path": "out.txt", "content": "x"},
    }

    output = asyncio.run(task._pre_tool_use(hook_input, None, None))

    assert output["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_pre_tool_use_strict_leaves_decision_to_approval(tmp_path: Path) -> None:
    task = _task(tmp_path, _config(approval_mode="strict"), Recorder())
    hook_input = {
        "tool_name": "Write",
        "tool_input": {"file_path": "out.txt", "content": "x"},
    }

    output = asyncio.run(task._pre_tool_use(hook_input, None, None))

    assert output == {}
