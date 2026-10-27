from __future__ import annotations

import os
import re
import time
from pathlib import Path

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


def create_task_dir(workspace_root: Path, chat_id: int, title: str) -> Path:
    base = chat_root(workspace_root, chat_id) / "tasks"
    base.mkdir(parents=True, exist_ok=True)
    name = f"{time.strftime('%Y-%m-%d-%H%M%S')}-{slugify(title)}"
    task_dir = base / name
    index = 1
    while task_dir.exists():
        task_dir = base / f"{name}-{index}"
        index += 1
    task_dir.mkdir(parents=True)
    (task_dir / "uploads").mkdir()
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
        target = (base / relative).resolve()
    except OSError:
        return None
    if target != base and base not in target.parents:
        return None
    return target


def iter_files(base: Path, limit: int = 200) -> list[tuple[str, int]]:
    files: list[tuple[str, int]] = []
    if not base.is_dir():
        return files
    for path in sorted(base.rglob("*")):
        if len(files) >= limit:
            break
        if path.is_file():
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
) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    if not base.is_dir():
        return found
    scanned = 0
    for path in base.rglob("*"):
        scanned += 1
        if scanned > max_scan:
            break
        if not path.is_file():
            continue
        relative = path.relative_to(base)
        if relative.parts and relative.parts[0] in skip_top:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime >= since:
            found.append((relative.as_posix(), stat.st_size))
    found.sort()
    return found[:limit]


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{value:.0f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
