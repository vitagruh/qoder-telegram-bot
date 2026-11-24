"""Персистентное состояние бота: сессии, счётчики и активность.

Файл ``WORKSPACE_ROOT/state.json`` (0600) с атомарной записью и резервной
копией ``.bak``. Хранятся только идентификаторы и метрики: prompt'ы,
секреты и содержимое файлов сюда не попадают.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from workspace import is_task_dir

log = logging.getLogger("qoder-bot")

SCHEMA_VERSION = 1


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _as_optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


class StateStore:
    def __init__(self, path: Path, workspace_root: Path) -> None:
        self.path = Path(path)
        self.workspace_root = Path(workspace_root)
        self._users: dict[str, dict[str, dict[str, Any]]] = {}
        self.loaded_from_backup = False
        self.load()

    # ------------------------------------------------------------------ load
    def load(self) -> None:
        data = self._read(self.path)
        if data is None:
            data = self._read(self._backup_path())
            self.loaded_from_backup = data is not None
        self._users = {}
        if not isinstance(data, dict):
            return
        users = data.get("users")
        if not isinstance(users, dict):
            return
        for user_key, user_value in users.items():
            user_id = _as_int(user_key, -1)
            if user_id < 0 or not isinstance(user_value, dict):
                continue
            chats = user_value.get("chats")
            if not isinstance(chats, dict):
                continue
            normalized: dict[str, dict[str, Any]] = {}
            for chat_key, chat_value in chats.items():
                chat_id = _as_int(chat_key, -1)
                if chat_id < 0 or not isinstance(chat_value, dict):
                    continue
                record = self._normalize(chat_value)
                if record is not None:
                    normalized[str(chat_id)] = record
            if normalized:
                self._users[str(user_id)] = normalized

    def _read(self, path: Path) -> dict[str, Any] | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        if not raw.strip():
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            log.warning("state: файл %s повреждён, пробую резервную копию", path)
            return None
        return data if isinstance(data, dict) else None

    def _normalize(self, value: dict[str, Any]) -> dict[str, Any] | None:
        task_dir = _as_optional_str(value.get("task_dir"))
        session_id = _as_optional_str(value.get("session_id"))
        if task_dir is not None:
            if not is_task_dir(self.workspace_root, task_dir):
                # Каталог удалён/подменён: не восстанавливаем задачу и сессию.
                log.info("state: task_dir %s вне workspace — запись сброшена", task_dir)
                task_dir = None
                session_id = None
            else:
                task_dir = str(Path(task_dir).expanduser().resolve())
        return {
            "task_dir": task_dir,
            "session_id": session_id,
            "tasks_done": _as_int(value.get("tasks_done")),
            "credits_total": _as_float(value.get("credits_total")),
            "last_seconds": _as_float(value.get("last_seconds")),
            "last_active": _as_float(value.get("last_active")),
            "last_usage": _as_dict(value.get("last_usage")),
            "last_context": _as_dict(value.get("last_context")),
        }

    # ------------------------------------------------------------------- api
    def get(self, user_id: int, chat_id: int) -> dict[str, Any] | None:
        chat = self._users.get(str(user_id), {}).get(str(chat_id))
        return dict(chat) if chat is not None else None

    def set(self, user_id: int, chat_id: int, data: dict[str, Any]) -> None:
        task_dir = data.get("task_dir")
        if task_dir:
            if not is_task_dir(self.workspace_root, Path(str(task_dir))):
                raise ValueError("task_dir вне workspace — запись отклонена")
            task_dir = str(Path(str(task_dir)).expanduser().resolve())
        record = {
            "task_dir": task_dir,
            "session_id": _as_optional_str(data.get("session_id")),
            "tasks_done": _as_int(data.get("tasks_done")),
            "credits_total": _as_float(data.get("credits_total")),
            "last_seconds": _as_float(data.get("last_seconds")),
            "last_active": _as_float(data.get("last_active"), time.time()),
            "last_usage": _as_dict(data.get("last_usage")),
            "last_context": _as_dict(data.get("last_context")),
        }
        self._users.setdefault(str(user_id), {})[str(chat_id)] = record

    def remove(self, user_id: int, chat_id: int) -> bool:
        chats = self._users.get(str(user_id))
        if not chats or str(chat_id) not in chats:
            return False
        del chats[str(chat_id)]
        if not chats:
            del self._users[str(user_id)]
        return True

    def users(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for user_key, chats in sorted(self._users.items(), key=lambda item: int(item[0])):
            rows = [
                {"chat_id": int(chat_key), **chat}
                for chat_key, chat in chats.items()
            ]
            result.append({"user_id": int(user_key), "chats": rows})
        return result

    def find_by_task_dir(self, user_id: int, task_dir: Path) -> dict[str, Any] | None:
        try:
            target = Path(task_dir).resolve()
        except (OSError, RuntimeError):
            return None
        for chat_id, record in self._users.get(str(user_id), {}).items():
            task = record.get("task_dir")
            if not task:
                continue
            try:
                if Path(task).resolve() == target:
                    return {"chat_id": int(chat_id), **record}
            except (OSError, RuntimeError):
                continue
        return None

    def ensure(self, user_id: int, chat_id: int, task_dir: Path) -> dict[str, Any]:
        existing = self.get(user_id, chat_id)
        if existing is not None and existing.get("task_dir"):
            return existing
        self.set(
            user_id,
            chat_id,
            {
                "task_dir": str(task_dir),
                "session_id": existing.get("session_id") if existing else None,
                "tasks_done": existing.get("tasks_done", 0) if existing else 0,
                "credits_total": existing.get("credits_total", 0.0) if existing else 0.0,
                "last_seconds": existing.get("last_seconds", 0.0) if existing else 0.0,
                "last_active": existing.get("last_active", 0.0) if existing else 0.0,
                "last_usage": existing.get("last_usage") if existing else None,
                "last_context": existing.get("last_context") if existing else None,
            },
        )
        return self.get(user_id, chat_id) or {}

    def prune(self) -> int:
        removed = 0
        for user_key in list(self._users):
            chats = self._users[user_key]
            for chat_key in list(chats):
                task_dir = chats[chat_key].get("task_dir")
                if task_dir and is_task_dir(self.workspace_root, Path(str(task_dir))):
                    continue
                del chats[chat_key]
                removed += 1
            if not chats:
                del self._users[user_key]
        return removed

    # ------------------------------------------------------------------ save
    def _backup_path(self) -> Path:
        return self.path.with_name(self.path.name + ".bak")

    def to_dict(self) -> dict[str, Any]:
        users = {
            user_key: {"chats": chats}
            for user_key, chats in self._users.items()
        }
        return {"version": SCHEMA_VERSION, "users": users}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            self.path.parent.chmod(0o700)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2, default=str)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            with contextlib.suppress(OSError):
                tmp.chmod(0o600)
            # Не затирать валидный .bak, если основной файл был повреждён
            # и состояние загружено из резервной копии.
            if (
                self.path.is_file()
                and not self.loaded_from_backup
                and self._read(self.path) is not None
            ):
                with contextlib.suppress(OSError):
                    shutil.copyfile(self.path, self._backup_path())
                    self._backup_path().chmod(0o600)
            os.replace(tmp, self.path)
            if not self._backup_path().is_file():
                with contextlib.suppress(OSError):
                    shutil.copyfile(self.path, self._backup_path())
                    self._backup_path().chmod(0o600)
            self.loaded_from_backup = False
        except OSError as exc:
            log.error("state: не удалось сохранить %s: %s", self.path, exc)
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
