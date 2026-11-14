from __future__ import annotations

import os

from env_policy import build_child_env


def test_child_env_drops_bot_token() -> None:
    base = {
        "PATH": "/usr/bin",
        "HOME": "/home/bot",
        "LANG": "C.UTF-8",
        "TELEGRAM_BOT_TOKEN": "123:secret",
        "QODER_PERSONAL_ACCESS_TOKEN": "pat-secret",
        "UNRELATED_SECRET": "nope",
    }
    env = build_child_env("/home/bot/.qoder", base=base)

    assert "TELEGRAM_BOT_TOKEN" not in env
    assert "UNRELATED_SECRET" not in env
    assert env["QODER_PERSONAL_ACCESS_TOKEN"] == "pat-secret"
    assert env["QODER_CONFIG_DIR"] == "/home/bot/.qoder"
    assert env["PATH"] == "/usr/bin"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_CONFIG_SYSTEM"] == os.devnull
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_child_env_keeps_config_dir_from_base() -> None:
    env = build_child_env(None, base={"QODER_CONFIG_DIR": "/tmp/qoder"})
    assert env["QODER_CONFIG_DIR"] == "/tmp/qoder"
    assert env["PATH"] == os.defpath
