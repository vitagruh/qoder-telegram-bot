from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from approvals import ApprovalManager, Decision


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, Any]] = []
        self.edited: list[tuple[int, int, str, Any]] = []
        self._message_id = 100

    async def send_message(self, chat_id: int, text: str, reply_markup: Any = None) -> Any:
        self.sent.append((chat_id, text, reply_markup))
        self._message_id += 1
        return SimpleNamespace(message_id=self._message_id)

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Any = None,
    ) -> None:
        self.edited.append((chat_id, message_id, text, reply_markup))


async def _wait_sent(bot: FakeBot, count: int = 1) -> None:
    for _ in range(100):
        if len(bot.sent) >= count:
            return
        await asyncio.sleep(0.001)
    raise AssertionError("bot.send_message was not called")


def _request_id(bot: FakeBot) -> str:
    buttons = [
        button
        for row in bot.sent[0][2].inline_keyboard
        for button in row
    ]
    return buttons[0].callback_data.split(":")[1]


def test_ask_accepts_text_only_from_owner() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        manager = ApprovalManager(bot, timeout=5)
        task = asyncio.create_task(
            manager.ask(10, [{"question": "Q", "options": [{"label": "A"}]}], user_id=42)
        )
        await _wait_sent(bot)

        assert manager.consume_text(10, "чужой", user_id=99) is False
        assert manager.consume_text(11, "чужой чат", user_id=42) is False
        assert manager.consume_text(10, "свой", user_id=42) is True
        assert await task == {"Q": "свой"}

    asyncio.run(scenario())


def test_ask_callback_rejects_foreign_user() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        manager = ApprovalManager(bot, timeout=5)
        task = asyncio.create_task(
            manager.ask(10, [{"question": "Q", "options": [{"label": "A"}]}], user_id=42)
        )
        await _wait_sent(bot)
        request_id = _request_id(bot)

        assert manager.handle_ask_callback(
            f"ask:{request_id}:select:0", chat_id=10, user_id=99
        ) is False
        assert manager.handle_ask_callback(
            f"ask:{request_id}:select:0", chat_id=11, user_id=42
        ) is False
        assert manager.handle_ask_callback(
            f"ask:{request_id}:select:0", chat_id=10, user_id=42
        ) is True
        assert await task == {"Q": "A"}

    asyncio.run(scenario())


def test_expired_question_is_per_user() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        manager = ApprovalManager(bot, timeout=0.03)
        result = await manager.ask(
            10,
            [{"question": "Q", "options": [{"label": "A"}]}],
            user_id=42,
        )
        assert result is None
        assert manager.question_expired(10, user_id=99) is False
        assert manager.question_expired(10, user_id=42) is True
        assert manager.question_expired(10, user_id=42) is False

    asyncio.run(scenario())


def test_cancel_all_can_target_one_user() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        manager = ApprovalManager(bot, timeout=5)
        first = asyncio.create_task(
            manager.request(10, "Bash", {}, None, user_id=1)
        )
        await _wait_sent(bot, 1)
        second = asyncio.create_task(
            manager.request(10, "Bash", {}, None, user_id=2)
        )
        await _wait_sent(bot, 2)

        await manager.cancel_all(10, user_id=1)
        assert await first is Decision.DENY
        assert not second.done()

        await manager.cancel_all(10, user_id=2)
        assert await second is Decision.DENY

    asyncio.run(scenario())
