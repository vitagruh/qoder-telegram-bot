from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from safe_commands import SAFE_BASH_MODES
from tool_policy import NON_AUTO_TOOLS

log = logging.getLogger("qoder-bot")

# Инструменты с сетью: по умолчанию требуют подтверждения, но могут быть
# ослаблены через APPROVAL_MODE=trusted или AUTO_APPROVE_NETWORK=1.
NETWORK_TOOLS = frozenset({"WebFetch", "WebSearch"})
# Совместимый алиас для внешних импортов; фактическое поведение сетевых
# инструментов зависит от approval_mode/auto_approve_network.
MANDATORY_CONFIRM_TOOLS = NETWORK_TOOLS

APPROVAL_MODES = {"strict", "balanced", "trusted"}

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


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"{name}: ожидается 0/1, true/false, yes/no или on/off")


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


def _tools(name: str, default: str = "") -> frozenset[str]:
    return frozenset(
        part.strip()
        for part in re.split(r"[,\s]+", os.getenv(name, default))
        if part.strip()
    )


def _folded(values: frozenset[str]) -> frozenset[str]:
    return frozenset(value.casefold() for value in values)


@dataclass(frozen=True)
class Config:
    bot_token: str
    allowed_user_ids: frozenset[int]
    admin_user_ids: frozenset[int]
    workspace_root: Path
    permission_mode: str
    approval_mode: str
    auto_approve_network: bool
    allowed_tools: tuple[str, ...]
    always_confirm_tools: frozenset[str]
    safe_bash_mode: str
    max_upload_mb: int
    task_timeout_sec: int
    approval_timeout_sec: int
    max_concurrent_tasks: int
    qoder_config_dir: str | None

    @property
    def confirm_tools(self) -> frozenset[str]:
        """Все инструменты с логикой подтверждения (сеть + ALWAYS)."""

        return self.always_confirm_tools | NETWORK_TOOLS

    def requires_confirmation(self, tool_name: str) -> bool:
        folded = tool_name.casefold()
        if folded in _folded(self.always_confirm_tools):
            return True
        if folded not in _folded(NETWORK_TOOLS):
            return False
        if self.approval_mode == "trusted":
            return False
        return not self.auto_approve_network


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

    admin = _ids("ADMIN_USER_IDS")
    if admin - allowed:
        raise SystemExit(
            "ADMIN_USER_IDS должен быть подмножеством ALLOWED_USER_IDS: "
            + ", ".join(str(item) for item in sorted(admin - allowed))
        )

    mode = os.getenv("PERMISSION_MODE", "default").strip() or "default"
    if mode not in PERMISSION_MODES:
        raise SystemExit(
            f"PERMISSION_MODE: неизвестный режим '{mode}'. "
            f"Допустимо: {', '.join(sorted(PERMISSION_MODES))}"
        )

    approval_mode = os.getenv("APPROVAL_MODE", "balanced").strip().lower() or "balanced"
    if approval_mode not in APPROVAL_MODES:
        raise SystemExit(
            f"APPROVAL_MODE: неизвестный режим '{approval_mode}'. "
            f"Допустимо: {', '.join(sorted(APPROVAL_MODES))}"
        )
    auto_approve_network = _bool("AUTO_APPROVE_NETWORK", False)
    if approval_mode == "trusted" and len(allowed) > 1:
        log.warning(
            "APPROVAL_MODE=trusted при нескольких ALLOWED_USER_IDS небезопасен: "
            "Bash без подтверждения может читать чужие каталоги и секреты. "
            "Для мультипользовательского режима используйте balanced/strict."
        )
    if auto_approve_network and approval_mode != "trusted":
        log.warning(
            "AUTO_APPROVE_NETWORK=1: WebFetch/WebSearch выполняются без "
            "подтверждения (если не добавлены в ALWAYS_CONFIRM_TOOLS)."
        )

    tools = tuple(
        item.strip()
        for item in os.getenv("ALLOWED_TOOLS", "").split(",")
        if item.strip()
    )
    always_confirm_tools = _tools("ALWAYS_CONFIRM_TOOLS")
    confirm_tools = always_confirm_tools | NETWORK_TOOLS
    confirm_folded = _folded(confirm_tools)
    # Файловые инструменты, Bash и AskUserQuestion не должны обходить policy.
    auto_tools = tuple(
        tool
        for tool in tools
        if tool.casefold() not in confirm_folded
        and tool.casefold() not in _folded(NON_AUTO_TOOLS)
    )
    removed = sorted(set(tools) - set(auto_tools))
    if removed:
        log.warning(
            "ALLOWED_TOOLS: %s исключены из автозапуска "
            "(файловые инструменты проходят sandbox/подтверждение)",
            ", ".join(removed),
        )

    safe_bash_mode = os.getenv("SAFE_BASH_MODE", "readonly").strip().lower() or "readonly"
    if safe_bash_mode not in SAFE_BASH_MODES:
        raise SystemExit(
            f"SAFE_BASH_MODE: неизвестный режим '{safe_bash_mode}'. "
            f"Допустимо: {', '.join(sorted(SAFE_BASH_MODES))}"
        )
    if safe_bash_mode == "dev":
        log.warning(
            "SAFE_BASH_MODE=dev: без подтверждения выполняются тесты и сборка, "
            "то есть код проекта. Включайте только для доверенных репозиториев."
        )

    root = Path(os.getenv("WORKSPACE_ROOT", "~/qoder-bot/workspace")).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    return Config(
        bot_token=token,
        allowed_user_ids=allowed,
        admin_user_ids=admin,
        workspace_root=root,
        permission_mode=mode,
        approval_mode=approval_mode,
        auto_approve_network=auto_approve_network,
        allowed_tools=auto_tools,
        always_confirm_tools=always_confirm_tools,
        safe_bash_mode=safe_bash_mode,
        max_upload_mb=_int("MAX_UPLOAD_MB", 20),
        task_timeout_sec=_int("TASK_TIMEOUT_SEC", 1200),
        approval_timeout_sec=_int("APPROVAL_TIMEOUT_SEC", 120),
        max_concurrent_tasks=_int("MAX_CONCURRENT_TASKS", 2),
        qoder_config_dir=os.getenv("QODER_CONFIG_DIR") or None,
    )
