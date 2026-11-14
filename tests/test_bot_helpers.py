from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiogram.types import CallbackQuery, User

from bot import (
    AllowlistMiddleware,
    ChatState,
    augment_prompt,
    collect_result_files,
    fmt_num,
    split_text,
    usage_details,
    usage_line,
)


def test_split_text_respects_limit_and_keeps_content() -> None:
    text = "\n".join(f"строка {index} " + "x" * 30 for index in range(200))
    chunks = split_text(text, limit=500)

    assert chunks
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert " ".join(chunks).split() == text.split()


def test_split_text_uses_newline_boundary() -> None:
    text = "a" * 40 + "\n" + "b" * 40
    chunks = split_text(text, limit=50)

    assert chunks == ["a" * 40, "b" * 40]


def test_split_text_handles_unbreakable_text() -> None:
    chunks = split_text("x" * 25, limit=10)

    assert chunks == ["x" * 10, "x" * 10, "x" * 5]


def test_split_text_strips_and_returns_empty_for_blank() -> None:
    assert split_text("   ") == []
    assert split_text("  hello  ") == ["hello"]


def test_fmt_num_formats_values() -> None:
    assert fmt_num(None) == "—"
    assert fmt_num(1234) == "1 234"
    assert fmt_num(1234.0) == "1 234"
    assert fmt_num(1234.5) == "1 234.50"
    assert fmt_num("42") == "42"
    assert fmt_num("n/a") == "n/a"


def test_augment_prompt_without_files_is_noop() -> None:
    assert augment_prompt("do it", []) == "do it"


def test_augment_prompt_lists_uploads() -> None:
    prompt = augment_prompt("проверь", ["uploads/a.txt", "uploads/b.txt"])

    assert prompt.startswith("проверь")
    assert "- uploads/a.txt" in prompt
    assert "- uploads/b.txt" in prompt


def test_collect_result_files_skips_hidden_and_symlinks(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    (task_dir / "sub").mkdir(parents=True)
    (task_dir / ".git").mkdir()
    (task_dir / "a.txt").write_text("a")
    (task_dir / "sub" / "b.txt").write_text("bb")
    (task_dir / ".git" / "config").write_text("secret")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (task_dir / "link.txt").symlink_to(outside)

    found = collect_result_files(task_dir, since=time.time() - 60)

    relatives = [relative for _, relative, _ in found]
    assert "a.txt" in relatives
    assert "sub/b.txt" in relatives
    assert all("outside" not in relative for relative in relatives)
    assert all(not relative.endswith("link.txt") for relative in relatives)
    assert all(".git" not in relative for relative in relatives)


def test_collect_result_files_ignores_old_files(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    old = task_dir / "old.txt"
    old.write_text("old")

    assert collect_result_files(task_dir, since=time.time() + 60) == []


def test_usage_line_contains_metrics() -> None:
    task = SimpleNamespace(
        request_credits=12.5,
        session_credits=30,
        input_tokens=1000,
        output_tokens=200,
        usage_info={"userQuota": {"remaining": 5, "unit": "credits"}},
        context_usage={"contextWindow": {"usedPercentage": 42}},
        tool_calls=["Read", "Bash"],
    )

    line = usage_line(task, elapsed=65)

    assert "credits" in line
    assert "1 000" in line
    assert "остаток 5 credits" in line
    assert "контекст 42%" in line
    assert "⏱ 65 с" in line
    assert "2 вызовов" in line


def test_usage_details_without_data() -> None:
    text = usage_details(ChatState())

    assert "Запусков задач: 0" in text
    assert "Данных пока нет" in text


def test_usage_details_with_quota_and_context() -> None:
    state = ChatState(tasks_done=3, credits_total=10.0, last_seconds=12)
    state.last_usage = {
        "userQuota": {"used": 10, "total": 100, "remaining": 90, "unit": "credits", "percentage": 10},
        "isQuotaExceeded": True,
        "session": {"total_credits": 7, "model_usage": {"qoder": {"credits": 7}}},
    }
    state.last_context = {"contextWindow": {"usedPercentage": 55}}

    text = usage_details(state)

    assert "Запусков задач: 3" in text
    assert "Квота: 10/100 credits" in text
    assert "Осталось: 90 credits" in text
    assert "Квота исчерпана" in text
    assert "qoder: 7" in text
    assert "Контекст: 55%" in text


def _user(user_id: int) -> User:
    return User(id=user_id, is_bot=False, first_name="Test")


def test_allowlist_middleware_allows_known_user() -> None:
    async def scenario() -> None:
        calls: list[Any] = []

        async def handler(event: Any, data: dict[str, Any]) -> str:
            calls.append(event)
            return "handled"

        middleware = AllowlistMiddleware(frozenset({1}))
        result = await middleware(handler, object(), {"event_from_user": _user(1)})

        assert result == "handled"
        assert calls

    asyncio.run(scenario())


def test_allowlist_middleware_blocks_unknown_and_missing_user() -> None:
    async def scenario() -> None:
        async def handler(event: Any, data: dict[str, Any]) -> str:
            raise AssertionError("handler must not be called")

        middleware = AllowlistMiddleware(frozenset({1}))

        assert await middleware(handler, object(), {"event_from_user": _user(2)}) is None
        assert await middleware(handler, object(), {"event_from_user": None}) is None

    asyncio.run(scenario())


def test_allowlist_middleware_answers_denied_callback() -> None:
    async def scenario() -> None:
        async def handler(event: Any, data: dict[str, Any]) -> str:
            raise AssertionError("handler must not be called")

        callback = CallbackQuery(
            id="1",
            from_user=_user(2),
            chat_instance="instance",
            data="act:files",
        )
        middleware = AllowlistMiddleware(frozenset({1}))

        assert await middleware(handler, callback, {"event_from_user": _user(2)}) is None

    asyncio.run(scenario())
