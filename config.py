from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from safe_commands import SAFE_BASH_MODES

PERMISSION_MODES = {
    "default",
    "acceptEdits",
    "plan",
    "auto",
    "dontAsk",
    "bypassPermissions",
    "yolo",
}


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def _int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} должен быть целым числом") from exc
    if value < minimum:
        raise SystemExit(f"{name} должен быть >= {minimum}")
    return value


def _ids(name: str) -> frozenset[int]:
    ids: set[int] = set()
    for part in re.split(r"[,\s]+", os.getenv(name, "")):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as exc:
            raise SystemExit(f"{name}: '{part}' не является числом") from exc
    return frozenset(ids)


@dataclass(frozen=True)
class Config:
    bot_token: str
    allowed_user_ids: frozenset[int]
    workspace_root: Path
    permission_mode: str
    allowed_tools: tuple[str, ...]
    safe_bash_mode: str
    max_upload_mb: int
    task_timeout_sec: int
    approval_timeout_sec: int
    qoder_config_dir: str | None


def load_config() -> Config:
    _load_dotenv(Path(__file__).resolve().parent / ".env")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN не задан — укажите токен от @BotFather")

    allowed = _ids("ALLOWED_USER_IDS")
    if not allowed:
        raise SystemExit(
            "ALLOWED_USER_IDS пуст — укажите Telegram user id через запятую, "
            "иначе ботом сможет пользоваться кто угодно"
        )

    mode = os.getenv("PERMISSION_MODE", "default").strip() or "default"
    if mode not in PERMISSION_MODES:
        raise SystemExit(
            f"PERMISSION_MODE: неизвестный режим '{mode}'. "
            f"Допустимо: {', '.join(sorted(PERMISSION_MODES))}"
        )

    tools = tuple(
        item.strip()
        for item in os.getenv("ALLOWED_TOOLS", "Read,Grep,Glob").split(",")
        if item.strip()
    )

    safe_bash_mode = os.getenv("SAFE_BASH_MODE", "off").strip().lower() or "off"
    if safe_bash_mode not in SAFE_BASH_MODES:
        raise SystemExit(
            f"SAFE_BASH_MODE: неизвестный режим '{safe_bash_mode}'. "
            f"Допустимо: {', '.join(sorted(SAFE_BASH_MODES))}"
        )

    root = Path(os.getenv("WORKSPACE_ROOT", "~/qoder-bot/workspace")).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    return Config(
        bot_token=token,
        allowed_user_ids=allowed,
        workspace_root=root,
        permission_mode=mode,
        allowed_tools=tools,
        safe_bash_mode=safe_bash_mode,
        max_upload_mb=_int("MAX_UPLOAD_MB", 20),
        task_timeout_sec=_int("TASK_TIMEOUT_SEC", 1200),
        approval_timeout_sec=_int("APPROVAL_TIMEOUT_SEC", 120),
        qoder_config_dir=os.getenv("QODER_CONFIG_DIR") or None,
    )
