from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from workspace import (
    SKIP_DIRS,
    create_task_dir,
    iter_files,
    recent_files,
    resolve_in,
    sanitize_filename,
)


def test_resolve_in_blocks_traversal(tmp_path: Path) -> None:
    base = tmp_path / "task"
    base.mkdir()
    (base / "ok.txt").write_text("ok", encoding="utf-8")
    assert resolve_in(base, "ok.txt") == base / "ok.txt"
    assert resolve_in(base, "../outside.txt") is None
    assert resolve_in(base, "/etc/passwd") is None


def test_resolve_in_resolves_symlinked_base(
    tmp_path: Path,
    make_symlink: Callable[..., None],
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "ok.txt").write_text("ok", encoding="utf-8")
    link = tmp_path / "link"
    make_symlink(link, real, target_is_directory=True)
    assert resolve_in(link, "ok.txt") == real / "ok.txt"


def test_iter_files_skips_service_dirs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print()", encoding="utf-8")
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "data.txt").write_text("data", encoding="utf-8")
    for name in (".git", "node_modules", "__pycache__"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "junk").write_text("junk", encoding="utf-8")

    names = {name for name, _ in iter_files(tmp_path)}
    assert "src/app.py" in names
    assert "uploads/data.txt" in names
    assert not any(part in name for name in names for part in SKIP_DIRS)


def test_recent_files_skips_uploads_and_service_dirs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "new.py").write_text("new", encoding="utf-8")
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "upload.txt").write_text("upload", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("dep", encoding="utf-8")

    names = [name for name, _ in recent_files(tmp_path, since=0)]
    assert names == ["src/new.py"]


def test_create_task_dir_is_private(tmp_path: Path) -> None:
    task_dir = create_task_dir(tmp_path, 42, 42, "My task")
    if os.name == "posix":
        assert task_dir.stat().st_mode & 0o077 == 0
        assert (task_dir / "uploads").stat().st_mode & 0o077 == 0


def test_sanitize_filename_strips_traversal() -> None:
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("..") == "file"
    assert sanitize_filename("a" * 300).__len__() == 120
