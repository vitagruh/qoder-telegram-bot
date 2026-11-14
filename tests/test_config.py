from __future__ import annotations

from pathlib import Path

import pytest

from config import MANDATORY_CONFIRM_TOOLS, load_config


def test_web_tools_are_never_auto_allowed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret")
    monkeypatch.setenv("ALLOWED_USER_IDS", "100,200")
    monkeypatch.setenv("ALLOWED_TOOLS", "Read,WebFetch,WebSearch,Bash")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))

    cfg = load_config()

    assert cfg.allowed_tools == ("Read", "Bash")
    assert cfg.requires_confirmation("WebFetch") is True
    assert cfg.requires_confirmation("webfetch") is True
    assert cfg.requires_confirmation("WebSearch") is True
    assert cfg.requires_confirmation("Read") is False
    assert MANDATORY_CONFIRM_TOOLS <= cfg.confirm_tools


def test_always_confirm_tools_extend_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret")
    monkeypatch.setenv("ALLOWED_USER_IDS", "100")
    monkeypatch.setenv("ALLOWED_TOOLS", "Read,Write")
    monkeypatch.setenv("ALWAYS_CONFIRM_TOOLS", "Write")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))

    cfg = load_config()

    assert cfg.allowed_tools == ("Read",)
    assert cfg.requires_confirmation("Write") is True
