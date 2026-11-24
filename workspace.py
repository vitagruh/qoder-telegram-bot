from __future__ import annotations

import contextlib
import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any

SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".qoder",
        ".cache",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".idea",
        ".vscode",
    }
)

_TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, fallback: str = "task") -> str:
    slug = _SLUG_RE.sub("-", text.lower().translate(_TRANSLIT)).strip("-")
    return slug[:40] or fallback


def chat_root(workspace_root: Path, chat_id: int) -> Path:
    return workspace_root / f"chat_{chat_id}"


def user_root(workspace_root: Path, user_id: int, chat_id: int) -> Path:
    """Корень пользователя: личка — chat_<uid>, группа — chat_<cid>/user_<uid>."""

    if chat_id == user_id:
        return chat_root(workspace_root, user_id)
    return chat_root(workspace_root, chat_id) / f"user_{user_id}"


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.chmod(0o700)


def create_task_dir(workspace_root: Path, user_id: int, chat_id: int, title: str) -> Path:
    owner = user_root(workspace_root, user_id, chat_id)
    _mkdir_private(owner)
    if chat_id != user_id:
        _mkdir_private(chat_root(workspace_root, chat_id))
    base = owner / "tasks"
    _mkdir_private(base)
    name = f"{time.strftime('%Y-%m-%d-%H%M%S')}-{slugify(title)}"
    task_dir = base / name
    index = 1
    while task_dir.exists():
        task_dir = base / f"{name}-{index}"
        index += 1
    _mkdir_private(task_dir)
    _mkdir_private(task_dir / "uploads")
    return task_dir


def task_ref(task_dir: Path) -> str:
    """Короткий стабильный идентификатор задачи для callback_data."""

    try:
        raw = str(Path(task_dir).resolve())
    except (OSError, RuntimeError):
        raw = str(task_dir)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def _numeric_suffix(name: str, prefix: str) -> int | None:
    if not name.startswith(prefix):
        return None
    try:
        return int(name[len(prefix) :])
    except ValueError:
        return None


def is_task_dir(workspace_root: Path, path: Path | str) -> bool:
    """Проверить, что путь — каталог задачи внутри workspace (защита state.json)."""

    try:
        root = Path(workspace_root).expanduser().resolve()
        target = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    if target == root or root not in target.parents or not target.is_dir():
        return False
    if target.parent.name != "tasks":
        return False
    parent = target.parent.parent
    if _numeric_suffix(parent.name, "chat_") is not None:
        return parent.parent == root
    if _numeric_suffix(parent.name, "user_") is not None:
        chat_dir = parent.parent
        return (
            chat_dir.parent == root
            and _numeric_suffix(chat_dir.name, "chat_") is not None
        )
    return False


def _task_dirs(root: Path, user_id: int) -> list[Path]:
    bases = [chat_root(root, user_id) / "tasks"]
    try:
        chat_dirs = sorted(root.glob("chat_*"))
    except OSError:
        chat_dirs = []
    for chat_dir in chat_dirs:
        if chat_dir.is_dir():
            bases.append(chat_dir / f"user_{user_id}" / "tasks")
    found: list[Path] = []
    for base in bases:
        if not base.is_dir():
            continue
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir() and not entry.is_symlink() and is_task_dir(root, entry):
                found.append(entry)
    return found


def list_user_tasks(workspace_root: Path, user_id: int, limit: int = 20) -> list[Path]:
    """Каталоги задач пользователя (личные и групповые), новые сверху."""

    root = Path(workspace_root).expanduser().resolve()
    found: list[tuple[float, Path]] = []
    for path in _task_dirs(root, user_id):
        try:
            found.append((path.stat().st_mtime, path))
        except OSError:
            continue
    found.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in found[: max(limit, 0)]]


def list_workspace_users(workspace_root: Path) -> list[int]:
    """Все user id, для которых в workspace есть каталоги задач."""

    root = Path(workspace_root).expanduser().resolve()
    users: set[int] = set()
    try:
        chat_dirs = list(root.glob("chat_*"))
    except OSError:
        return []
    for chat_dir in chat_dirs:
        if not chat_dir.is_dir():
            continue
        chat_id = _numeric_suffix(chat_dir.name, "chat_")
        if chat_id is None:
            continue
        if (chat_dir / "tasks").is_dir():
            users.add(chat_id)
        try:
            user_dirs = list(chat_dir.glob("user_*"))
        except OSError:
            user_dirs = []
        for user_dir in user_dirs:
            user_id = _numeric_suffix(user_dir.name, "user_")
            if user_id is not None and (user_dir / "tasks").is_dir():
                users.add(user_id)
    return sorted(users)


def task_stats(task_dir: Path, max_files: int = 5000) -> dict[str, Any]:
    """Метаданные задачи: размер, число файлов, mtime."""

    total = 0
    files = 0
    for root, dirnames, filenames in os.walk(task_dir, topdown=True, followlinks=False):
        dirnames[:] = [name for name in dirnames if not (Path(root) / name).is_symlink()]
        for name in filenames:
            path = Path(root) / name
            if path.is_symlink():
                continue
            try:
                total += path.stat().st_size
                files += 1
            except OSError:
                continue
            if files >= max_files:
                break
        if files >= max_files:
            break
    try:
        mtime = task_dir.stat().st_mtime
    except OSError:
        mtime = 0.0
    return {
        "path": str(task_dir),
        "name": task_dir.name,
        "files": files,
        "size": total,
        "mtime": mtime,
    }


def scan_task_dirs(workspace_root: Path) -> list[dict[str, Any]]:
    """Найти все каталоги задач и восстановить владельца по раскладке."""

    root = Path(workspace_root).expanduser().resolve()
    results: list[dict[str, Any]] = []
    try:
        chat_dirs = sorted(root.glob("chat_*"))
    except OSError:
        return results
    for chat_dir in chat_dirs:
        if not chat_dir.is_dir():
            continue
        chat_id = _numeric_suffix(chat_dir.name, "chat_")
        if chat_id is None:
            continue
        direct = chat_dir / "tasks"
        if direct.is_dir():
            for task in sorted(direct.iterdir()):
                if task.is_dir() and is_task_dir(root, task):
                    results.append(
                        {"user_id": chat_id, "chat_id": chat_id, "path": task}
                    )
        try:
            user_dirs = sorted(chat_dir.glob("user_*"))
        except OSError:
            user_dirs = []
        for user_dir in user_dirs:
            user_id = _numeric_suffix(user_dir.name, "user_")
            if user_id is None or not (user_dir / "tasks").is_dir():
                continue
            for task in sorted((user_dir / "tasks").iterdir()):
                if task.is_dir() and is_task_dir(root, task):
                    results.append(
                        {"user_id": user_id, "chat_id": chat_id, "path": task}
                    )
    return results


def sanitize_filename(name: str, fallback: str = "file") -> str:
    name = os.path.basename(name.replace("\\", "/")).strip()
    name = re.sub(r"[^\w.\- ]+", "_", name, flags=re.UNICODE).strip(" .")
    return (name or fallback)[:120]


def unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    index = 1
    while True:
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def resolve_in(base: Path, relative: str) -> Path | None:
    try:
        base = base.resolve()
        target = (base / relative).resolve()
    except (OSError, RuntimeError):
        return None
    if target != base and base not in target.parents:
        return None
    return target


def _walk_files(
    base: Path,
    skip_top: tuple[str, ...] = (),
    skip_dirs: frozenset[str] = SKIP_DIRS,
):
    for root, dirnames, filenames in os.walk(base, topdown=True, followlinks=False):
        root_path = Path(root)
        relative_root = root_path.relative_to(base)
        if not relative_root.parts and skip_top:
            dirnames[:] = [name for name in dirnames if name not in skip_top]
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in skip_dirs and not (root_path / name).is_symlink()
        )
        for name in sorted(filenames):
            path = root_path / name
            if not path.is_symlink():
                yield path


def iter_files(
    base: Path,
    limit: int = 200,
    skip_dirs: frozenset[str] = SKIP_DIRS,
) -> list[tuple[str, int]]:
    files: list[tuple[str, int]] = []
    if not base.is_dir():
        return files
    for path in _walk_files(base, skip_dirs=skip_dirs):
        if len(files) >= limit:
            break
        try:
            files.append((path.relative_to(base).as_posix(), path.stat().st_size))
        except OSError:
            continue
    return files


def recent_files(
    base: Path,
    since: float,
    limit: int = 20,
    max_scan: int = 5000,
    skip_top: tuple[str, ...] = ("uploads",),
    skip_dirs: frozenset[str] = SKIP_DIRS,
) -> list[tuple[str, int]]:
    found: list[tuple[float, str, int]] = []
    if not base.is_dir():
        return []
    scanned = 0
    for path in _walk_files(base, skip_top=skip_top, skip_dirs=skip_dirs):
        if scanned >= max_scan:
            break
        scanned += 1
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime >= since:
            found.append((stat.st_mtime, path.relative_to(base).as_posix(), stat.st_size))
    found.sort(key=lambda item: item[0], reverse=True)
    return [(relative, size) for _, relative, size in found[:limit]]


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{value:.0f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
