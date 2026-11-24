from __future__ import annotations

from pathlib import Path
from typing import Any

from tool_policy import evaluate_tool, is_path_inside, summarize_input


def test_read_inside_cwd_allowed(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    result = evaluate_tool("Read", {"file_path": "a.txt"}, tmp_path)
    assert result.action == "allow"
    assert result.input["file_path"] == "a.txt"


def test_read_absolute_inside_allowed(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("a", encoding="utf-8")
    result = evaluate_tool("Read", {"file_path": str(target)}, tmp_path)
    assert result.action == "allow"


def test_read_outside_denied(tmp_path: Path) -> None:
    assert evaluate_tool("Read", {"file_path": "/etc/passwd"}, tmp_path).action == "deny"
    assert evaluate_tool("Read", {"file_path": "../secret"}, tmp_path).action == "deny"


def test_read_tilde_denied(tmp_path: Path) -> None:
    result = evaluate_tool("Read", {"file_path": "~/.ssh/id_rsa"}, tmp_path)
    assert result.action == "deny"


def test_read_missing_path_denied(tmp_path: Path) -> None:
    assert evaluate_tool("Read", {}, tmp_path).action == "deny"


def test_grep_without_path_defaults_to_cwd(tmp_path: Path) -> None:
    result = evaluate_tool("Grep", {"pattern": "foo"}, tmp_path)
    assert result.action == "allow"
    assert result.input["path"] == str(tmp_path.resolve())


def test_grep_with_path_outside_denied(tmp_path: Path) -> None:
    result = evaluate_tool("Grep", {"pattern": "foo", "path": "/etc"}, tmp_path)
    assert result.action == "deny"


def test_glob_escaping_pattern_denied(tmp_path: Path) -> None:
    assert evaluate_tool("Glob", {"pattern": "../**/*.py"}, tmp_path).action == "deny"
    assert evaluate_tool("Glob", {"pattern": "/etc/**"}, tmp_path).action == "deny"


def test_glob_inside_allowed(tmp_path: Path) -> None:
    result = evaluate_tool("Glob", {"pattern": "**/*.py"}, tmp_path)
    assert result.action == "allow"
    assert result.input["path"] == str(tmp_path.resolve())


def test_ls_without_path_defaults_to_cwd(tmp_path: Path) -> None:
    result = evaluate_tool("LS", {}, tmp_path)
    assert result.action == "allow"
    assert result.input["path"] == str(tmp_path.resolve())


def test_write_inside_requires_confirmation(tmp_path: Path) -> None:
    result = evaluate_tool("Write", {"file_path": "out.txt", "content": "x"}, tmp_path)
    assert result.action == "ask"


def test_approve_writes_allows_write_inside(tmp_path: Path) -> None:
    result = evaluate_tool(
        "Write",
        {"file_path": "out.txt", "content": "x"},
        tmp_path,
        approve_writes=True,
    )
    assert result.action == "allow"

    edit = evaluate_tool(
        "Edit",
        {"file_path": "out.txt", "old_string": "x", "new_string": "y"},
        tmp_path,
        approve_writes=True,
    )
    assert edit.action == "allow"


def test_approve_writes_still_denies_outside(tmp_path: Path) -> None:
    assert (
        evaluate_tool("Write", {"file_path": "../x"}, tmp_path, approve_writes=True).action
        == "deny"
    )
    assert (
        evaluate_tool(
            "Edit", {"file_path": "/etc/passwd"}, tmp_path, approve_writes=True
        ).action
        == "deny"
    )


def test_force_confirm_wins_over_approve_writes(tmp_path: Path) -> None:
    result = evaluate_tool(
        "Write",
        {"file_path": "out.txt", "content": "x"},
        tmp_path,
        approve_writes=True,
        force_confirm=True,
    )
    assert result.action == "ask"


def test_write_outside_denied(tmp_path: Path) -> None:
    assert evaluate_tool("Write", {"file_path": "../x"}, tmp_path).action == "deny"
    assert evaluate_tool("Edit", {}, tmp_path).action == "deny"


def test_bash_and_unknown_require_confirmation(tmp_path: Path) -> None:
    assert evaluate_tool("Bash", {"command": "ls"}, tmp_path).action == "ask"
    assert evaluate_tool("Mystery", {}, tmp_path).action == "ask"


def test_force_confirm_turns_read_into_ask(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    result = evaluate_tool("Read", {"file_path": "a.txt"}, tmp_path, force_confirm=True)
    assert result.action == "ask"


def test_symlink_outside_denied(
    tmp_path: Path,
    make_symlink: Any,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret").write_text("secret", encoding="utf-8")
    make_symlink(tmp_path / "link", outside / "secret")
    result = evaluate_tool("Read", {"file_path": "link"}, tmp_path)
    assert result.action == "deny"


def test_is_path_inside(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    assert is_path_inside(tmp_path / "a", tmp_path) is True
    assert is_path_inside(tmp_path.parent, tmp_path) is False


def test_summarize_input_hides_file_content(tmp_path: Path) -> None:
    summary = summarize_input("Write", {"file_path": "a.txt", "content": "s" * 4000})
    assert "a.txt" in summary
    assert "s" * 100 not in summary
    assert "4000" in summary
