from __future__ import annotations

import contextlib
import os
import re
import time
from pathlib import Path

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


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.chmod(0o700)


def create_task_dir(workspace_root: Path, chat_id: int, title: str) -> Path:
    base = chat_root(workspace_root, chat_id) / "tasks"
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
