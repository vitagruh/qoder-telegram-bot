"""CLI для инспекции и восстановления сессий после рестарта.

Работает только со state.json и раскладкой workspace: aiogram и Qoder SDK
не нужны, сеть и Telegram не затрагиваются.

Примеры:
    python restore_sessions.py --list
    python restore_sessions.py --prune
    python restore_sessions.py --scan
    python restore_sessions.py --set-session --user 42 --chat 42 --session abc
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from admin_view import short_session
from state_store import StateStore
from workspace import is_task_dir, scan_task_dirs

DEFAULT_WORKSPACE = Path("~/qoder-bot/workspace")


def _default_workspace() -> Path:
    return DEFAULT_WORKSPACE.expanduser().resolve()


def _fmt_time(value: Any) -> str:
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return "—"
    if ts <= 0:
        return "—"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except (OSError, OverflowError, ValueError):
        return "—"


def _list_rows(store: StateStore) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for user in store.users():
        for chat in user["chats"]:
            rows.append(
                {
                    "user_id": user["user_id"],
                    "chat_id": chat.get("chat_id"),
                    "task_dir": chat.get("task_dir"),
                    "session_id": chat.get("session_id"),
                    "tasks_done": chat.get("tasks_done", 0),
                    "last_active": chat.get("last_active", 0),
                }
            )
    return rows


def _print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("Состояние пусто: сохранённых сессий нет.")
        return
    print(
        f"{'USER':>12} {'CHAT':>12} {'DONE':>5} {'SESSION':<12} "
        f"{'ACTIVE':<16} TASK"
    )
    for row in rows:
        print(
            f"{str(row['user_id']):>12} {str(row['chat_id']):>12} "
            f"{str(row['tasks_done']):>5} {short_session(row['session_id']):<12} "
            f"{_fmt_time(row['last_active']):<16} {row['task_dir'] or '—'}"
        )


def _scan(store: StateStore, workspace: Path) -> int:
    created = 0
    for item in scan_task_dirs(workspace):
        before = store.get(item["user_id"], item["chat_id"])
        task_dir = item["path"]
        if before and before.get("task_dir"):
            try:
                if Path(str(before["task_dir"])).resolve() == task_dir.resolve():
                    continue
            except (OSError, RuntimeError):
                pass
        if before and before.get("task_dir"):
            # У пользователя уже есть другая задача в этом чате — не
            # перезаписываем его актуальную сессию.
            continue
        store.ensure(item["user_id"], item["chat_id"], task_dir)
        created += 1
    return created


def _set_session(store: StateStore, args: argparse.Namespace) -> None:
    existing = store.get(args.user, args.chat) or {}
    task_dir = Path(args.task_dir) if args.task_dir else existing.get("task_dir")
    if not task_dir:
        raise SystemExit(
            "Не найден каталог задачи: передайте --task-dir или сначала выполните --scan."
        )
    if not is_task_dir(args.workspace, task_dir):
        raise SystemExit(f"Каталог вне workspace или не является задачей: {task_dir}")
    store.set(
        args.user,
        args.chat,
        {
            "task_dir": str(Path(str(task_dir))),
            "session_id": args.session,
            "tasks_done": existing.get("tasks_done", 0),
            "credits_total": existing.get("credits_total", 0.0),
            "last_seconds": existing.get("last_seconds", 0.0),
            "last_active": existing.get("last_active", 0.0),
            "last_usage": existing.get("last_usage"),
            "last_context": existing.get("last_context"),
        },
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Просмотр и восстановление сессий Qoder-бота (state.json)."
    )
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--list", action="store_true", help="показать записи (по умолчанию)")
    actions.add_argument("--prune", action="store_true", help="удалить записи без каталога")
    actions.add_argument("--scan", action="store_true", help="найти задачи в workspace")
    actions.add_argument("--set-session", action="store_true", help="записать session_id")
    parser.add_argument("--user", type=int)
    parser.add_argument("--chat", type=int)
    parser.add_argument("--session")
    parser.add_argument("--task-dir", dest="task_dir")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    workspace = (args.workspace or _default_workspace()).expanduser().resolve()
    if not workspace.is_dir():
        print(f"Workspace не найден: {workspace}", file=sys.stderr)
        return 2
    args.workspace = workspace
    state_path = (
        args.state.expanduser().resolve()
        if args.state
        else workspace / "state.json"
    )
    store = StateStore(state_path, workspace)

    if args.set_session:
        if args.user is None or args.chat is None or not args.session:
            parser.error("--set-session требует --user, --chat и --session")
        _set_session(store, args)
        store.save()
        if args.as_json:
            print(json.dumps({"ok": True, "state": str(state_path)}))
        else:
            print(f"Сессия сохранена: user {args.user}, chat {args.chat}.")
        return 0

    if args.prune:
        removed = store.prune()
        store.save()
        if args.as_json:
            print(json.dumps({"removed": removed, "state": str(state_path)}))
        else:
            print(f"Удалено записей без каталога задачи: {removed}.")
        return 0

    if args.scan:
        created = _scan(store, workspace)
        store.save()
        if args.as_json:
            print(json.dumps({"found": created, "state": str(state_path)}))
        else:
            print(f"Добавлено записей по найденным задачам: {created}.")
        return 0

    rows = _list_rows(store)
    if args.as_json:
        print(
            json.dumps(
                {
                    "state": str(state_path),
                    "loaded_from_backup": store.loaded_from_backup,
                    "sessions": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _print_table(rows)
        if store.loaded_from_backup:
            print("Внимание: state.json был повреждён, загружена резервная копия .bak.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
