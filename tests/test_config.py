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
    monkeypatch.setenv("ALLOWED_TOOLS", "Read,WebFetch,WebSearch,Bash,TodoWrite")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))

    cfg = load_config()

    # Файловые инструменты, Bash и сеть не обходят policy.
    assert cfg.allowed_tools == ("TodoWrite",)
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

    assert cfg.allowed_tools == ()
    assert cfg.requires_confirmation("Write") is True
    assert cfg.requires_confirmation("Read") is False


def _base_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret")
    monkeypatch.setenv("ALLOWED_USER_IDS", "100")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    for key in (
        "ADMIN_USER_IDS",
        "ALLOWED_TOOLS",
        "ALWAYS_CONFIRM_TOOLS",
        "APPROVAL_MODE",
        "AUTO_APPROVE_NETWORK",
        "SAFE_BASH_MODE",
        "MAX_CONCURRENT_TASKS",
    ):
        monkeypatch.delenv(key, raising=False)


def test_approval_defaults_to_balanced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _base_env(monkeypatch, tmp_path)

    cfg = load_config()

    assert cfg.approval_mode == "balanced"
    assert cfg.auto_approve_network is False
    assert cfg.safe_bash_mode == "readonly"
    # Сеть по умолчанию всё ещё спрашивается, запись — нет (решает runner).
    assert cfg.requires_confirmation("WebFetch") is True
    assert cfg.requires_confirmation("WebSearch") is True
    assert cfg.requires_confirmation("Write") is False


def test_strict_mode_still_requires_network_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("APPROVAL_MODE", "STRICT")

    cfg = load_config()

    assert cfg.approval_mode == "strict"
    assert cfg.requires_confirmation("WebFetch") is True


def test_trusted_mode_skips_network_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("APPROVAL_MODE", "trusted")

    cfg = load_config()

    assert cfg.requires_confirmation("WebFetch") is False
    assert cfg.requires_confirmation("WebSearch") is False


def test_always_confirm_overrides_trusted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("APPROVAL_MODE", "trusted")
    monkeypatch.setenv("ALWAYS_CONFIRM_TOOLS", "WebFetch,Bash")

    cfg = load_config()

    assert cfg.requires_confirmation("WebFetch") is True
    assert cfg.requires_confirmation("webfetch") is True
    assert cfg.requires_confirmation("Bash") is True
    assert cfg.requires_confirmation("WebSearch") is False


def test_auto_approve_network_in_balanced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTO_APPROVE_NETWORK", "1")

    cfg = load_config()

    assert cfg.auto_approve_network is True
    assert cfg.requires_confirmation("WebFetch") is False

    monkeypatch.setenv("ALWAYS_CONFIRM_TOOLS", "WebSearch")
    cfg = load_config()
    assert cfg.requires_confirmation("WebSearch") is True


@pytest.mark.parametrize("value", ["maybe", "2"])
def test_invalid_auto_approve_network_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value: str,
) -> None:
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTO_APPROVE_NETWORK", value)

    with pytest.raises(SystemExit) as excinfo:
        load_config()
    assert "AUTO_APPROVE_NETWORK" in str(excinfo.value)


def test_unknown_approval_mode_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("APPROVAL_MODE", "yolo-ish")

    with pytest.raises(SystemExit) as excinfo:
        load_config()
    message = str(excinfo.value)
    assert "APPROVAL_MODE" in message
    assert "balanced" in message


def test_admin_must_be_subset_of_allowed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret")
    monkeypatch.setenv("ALLOWED_USER_IDS", "100")
    monkeypatch.setenv("ADMIN_USER_IDS", "100,200")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))

    with pytest.raises(SystemExit) as excinfo:
        load_config()
    assert "ADMIN_USER_IDS" in str(excinfo.value)


def test_admin_and_concurrency_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret")
    monkeypatch.setenv("ALLOWED_USER_IDS", "100")
    monkeypatch.setenv("ADMIN_USER_IDS", "100")
    monkeypatch.delenv("ALLOWED_TOOLS", raising=False)
    monkeypatch.delenv("MAX_CONCURRENT_TASKS", raising=False)
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))

    cfg = load_config()

    assert cfg.allowed_tools == ()
    assert cfg.admin_user_ids == frozenset({100})
    assert cfg.max_concurrent_tasks == 2


def test_max_concurrent_tasks_must_be_positive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret")
    monkeypatch.setenv("ALLOWED_USER_IDS", "100")
    monkeypatch.setenv("MAX_CONCURRENT_TASKS", "0")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))

    with pytest.raises(SystemExit) as excinfo:
        load_config()
    assert "MAX_CONCURRENT_TASKS" in str(excinfo.value)
