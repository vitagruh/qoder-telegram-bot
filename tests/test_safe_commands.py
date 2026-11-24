from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from safe_commands import is_safe_command


@pytest.fixture()
def cwd(tmp_path: Path) -> Path:
    (tmp_path / "file.txt").write_text("hello", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("ls -la", True),
        ("cat file.txt", True),
        ("head -n 5 file.txt", True),
        ("head -20 file.txt", True),
        ("tail -n 5 file.txt", True),
        ("tail -100 file.txt", True),
        ("wc -l file.txt", True),
        ("sort file.txt", True),
        ("printf '%s\\n' hi", True),
        ("rg --files -g '*.py'", True),
        ("grep -r --include='*.py' foo .", True),
        ("find . -name '*.py'", True),
        ("git status -uall", True),
        ("git branch -a", True),
        ("git log -1", True),
        ("git log -5 --oneline", True),
        ("git show -1", True),
        ("git diff --stat", True),
        ("git rev-parse HEAD", True),
        ("git blame file.txt", True),
        ("git grep -n foo", True),
        ("git log --format=%H", True),
        # Отклоняем небезопасное и обходы.
        ("cat /etc/passwd", False),
        ("cat ../secret", False),
        ("ls ..", False),
        ('cat "$(id)"', False),
        ("cat `id`", False),
        # Brace expansion выполняется bash после проверки — запрещаем.
        ("cat {~}/.ssh/id_rsa", False),
        ("cat {..}/x", False),
        ("ls {a,b}", False),
        ("ls *.py", False),
        ("tail -f file.txt", False),
        ("tail --follow file.txt", False),
        ("sort --files0-from=list.txt", False),
        ("wc --files0-from=list.txt", False),
        ("find . -files0-from list.txt", False),
        ("file -f list.txt", False),
        ("find . -delete", False),
        ("find . -exec true {} +", False),
        ("git log --output=/tmp/out", False),
        ("git log --format=%G?", False),
        ("git -c core.pager=cat log", False),
        ("git diff --no-index /etc/passwd file.txt", False),
        ("git show --textconv HEAD:file.txt", False),
        ("git branch -d topic", False),
        ("sha256sum -c /etc/passwd", False),
        ("rg --pre /bin/sh foo", False),
        ("make -j test", False),  # dev-режим выключен
    ],
)
def test_readonly_mode(command: str, expected: bool, cwd: Path) -> None:
    assert is_safe_command(command, cwd) is expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("pytest -k 'x or y'", True),
        ("python3 -m pytest -q", True),
        ("npm test", True),
        ("npm run build", True),
        ("yarn lint", True),
        ("pnpm typecheck", True),
        ("cargo test", True),
        ("cargo fmt --check", True),
        ("go test ./...", True),
        ("make test", True),
        ("make -j test", True),
        ("make -j4 test", True),
        ("make -j 8 test", True),
        ("make --jobs=8 test", True),
        ("mvn test", True),
        ("gradle build", True),
        ("dotnet test", True),
        ("ruff check .", True),
        ("ruff format --check .", True),
        ("mypy .", True),
        ("eslint .", True),
        ("tsc --noEmit", True),
        ("npm run deploy", False),
        ("ruff format .", False),
        ("eslint --fix .", False),
        ("tsc", False),
        ("go test -exec=/bin/sh", False),
        ("make -jfoo test", False),
        ("make deploy", False),
        ("pytest ../../etc/passwd", False),
    ],
)
def test_dev_mode(command: str, expected: bool, cwd: Path) -> None:
    assert is_safe_command(command, cwd, allow_dev=True) is expected


def test_symlink_outside_cwd_rejected(
    tmp_path: Path,
    make_symlink: Any,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret").write_text("secret", encoding="utf-8")
    make_symlink(tmp_path / "link", outside / "secret")
    assert is_safe_command("cat link", tmp_path) is False


def test_absolute_symlink_dir_rejected(
    tmp_path: Path,
    make_symlink: Any,
) -> None:
    inside = tmp_path / "inside"
    inside.mkdir()
    (inside / "ok").write_text("ok", encoding="utf-8")
    make_symlink(tmp_path / "escape", inside, target_is_directory=True)
    assert is_safe_command("cat escape/ok", tmp_path) is True
    assert is_safe_command("cat escape/../../outside", tmp_path) is False
