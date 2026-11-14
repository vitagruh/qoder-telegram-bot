from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from approvals import ApprovalManager, Decision


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, Any]] = []
        self.edited: list[tuple[int, int, str, Any]] = []
        self.markups: list[tuple[int, int, Any]] = []
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

    async def edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: Any = None,
    ) -> None:
        self.markups.append((chat_id, message_id, reply_markup))


class FakeSignal:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    def is_set(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    def set(self) -> None:
        self._event.set()


async def _wait_sent(bot: FakeBot, count: int = 1) -> None:
    for _ in range(100):
        if len(bot.sent) >= count:
            return
        await asyncio.sleep(0.001)
    raise AssertionError("bot.send_message was not called")


def _buttons(keyboard: Any) -> list[tuple[str, str]]:
    return [(button.text, button.callback_data) for row in keyboard.inline_keyboard for button in row]


def test_mandatory_tool_hides_session_button() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        manager = ApprovalManager(bot, timeout=5)
        task = asyncio.create_task(
            manager.request(
                10,
                "WebFetch",
                {"url": "https://example.com/page"},
                SimpleNamespace(description="Сетевой запрос"),
                user_id=42,
                allow_session=False,
            )
        )
        await _wait_sent(bot)

        chat_id, text, keyboard = bot.sent[0]
        assert chat_id == 10
        assert "🌐 https://example.com/page" in text
        buttons = _buttons(keyboard)
        assert [label for label, _ in buttons] == ["✅ Разрешить", "⛔ Отклонить"]

        request_id = buttons[0][1].split(":")[1]
        assert manager.resolve(request_id, Decision.ALLOW, chat_id=10, user_id=42) is True
        assert await task is Decision.ALLOW
        assert "✅ Разрешено" in bot.edited[-1][2]
        assert bot.edited[-1][3] is None

    asyncio.run(scenario())


def test_session_button_is_offered_when_allowed() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        manager = ApprovalManager(bot, timeout=5)
        task = asyncio.create_task(manager.request(1, "Bash", {"command": "ls"}, None))
        await _wait_sent(bot)

        labels = [label for label, _ in _buttons(bot.sent[0][2])]
        assert labels == ["✅ Разрешить", "♾ В сессии", "⛔ Отклонить"]

        request_id = _buttons(bot.sent[0][2])[1][1].split(":")[1]
        assert manager.resolve(request_id, Decision.ALWAYS, chat_id=1) is True
        assert await task is Decision.ALWAYS

    asyncio.run(scenario())


def test_resolve_rejects_foreign_chat_and_user() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        manager = ApprovalManager(bot, timeout=5)
        task = asyncio.create_task(manager.request(1, "Read", {}, None, user_id=7))
        await _wait_sent(bot)
        request_id = _buttons(bot.sent[0][2])[0][1].split(":")[1]

        assert manager.resolve(request_id, Decision.ALLOW, chat_id=2) is False
        assert manager.resolve(request_id, Decision.ALLOW, chat_id=1, user_id=8) is False
        assert manager.resolve("unknown", Decision.ALLOW) is False

        assert manager.resolve(request_id, Decision.DENY, chat_id=1, user_id=7) is True
        assert manager.resolve(request_id, Decision.ALLOW, chat_id=1, user_id=7) is False
        assert await task is Decision.DENY

    asyncio.run(scenario())


def test_timeout_denies_and_updates_message() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        manager = ApprovalManager(bot, timeout=0.05)
        decision = await manager.request(1, "WebSearch", {"query": "test"}, None, user_id=1)

        assert decision is Decision.DENY
        assert "⏱ Время ожидания истекло" in bot.edited[-1][2]
        assert bot.edited[-1][3] is None

    asyncio.run(scenario())


def test_cancellation_signal_denies_request() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        manager = ApprovalManager(bot, timeout=5)
        signal = FakeSignal()
        task = asyncio.create_task(
            manager.request(1, "Bash", {}, SimpleNamespace(signal=signal))
        )
        await _wait_sent(bot)
        signal.set()

        assert await task is Decision.DENY
        assert "Запрос отменён" in bot.edited[-1][2]

    asyncio.run(scenario())


def test_cancel_all_denies_pending_requests() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        manager = ApprovalManager(bot, timeout=5)
        task = asyncio.create_task(manager.request(1, "Bash", {}, None))
        await _wait_sent(bot)
        await manager.cancel_all(chat_id=1)

        assert await task is Decision.DENY
        assert "⛔ Отклонено" in bot.edited[-1][2]

    asyncio.run(scenario())


def test_ask_returns_selected_option() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        manager = ApprovalManager(bot, timeout=5)
        questions = [
            {
                "header": "Выбор",
                "question": "Какой вариант?",
                "options": [{"label": "A"}, {"label": "B", "description": "второй"}],
            }
        ]
        task = asyncio.create_task(manager.ask(5, questions))
        await _wait_sent(bot)

        request_id = _buttons(bot.sent[0][2])[0][1].split(":")[1]
        assert manager.handle_ask_callback(f"ask:{request_id}:select:1", chat_id=5) is True
        assert await task == {"Какой вариант?": "B"}
        assert bot.edited[-1][2].rstrip().endswith("✅ B")

    asyncio.run(scenario())


def test_ask_accepts_free_text_answer() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        manager = ApprovalManager(bot, timeout=5)
        task = asyncio.create_task(
            manager.ask(5, [{"question": "Имя?", "options": [{"label": "A"}]}])
        )
        await _wait_sent(bot)

        assert manager.consume_text(5, "  свой вариант  ") is True
        assert await task == {"Имя?": "свой вариант"}
        assert manager.consume_text(5, "лишний") is False

    asyncio.run(scenario())


def test_ask_multiselect_toggles_and_finishes() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        manager = ApprovalManager(bot, timeout=5)
        task = asyncio.create_task(
            manager.ask(
                5,
                [
                    {
                        "question": "Что включить?",
                        "multiSelect": True,
                        "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
                    }
                ],
            )
        )
        await _wait_sent(bot)
        request_id = _buttons(bot.sent[0][2])[0][1].split(":")[1]

        assert manager.handle_ask_callback(f"ask:{request_id}:toggle:0", chat_id=5) is True
        assert manager.handle_ask_callback(f"ask:{request_id}:toggle:2", chat_id=5) is True
        assert manager.handle_ask_callback(f"ask:{request_id}:done:-1", chat_id=5) is True

        assert await task == {"Что включить?": "A, C"}
        assert bot.markups, "multi-select keyboard should be refreshed after toggle"

    asyncio.run(scenario())


def test_ask_rejects_foreign_chat_callback() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        manager = ApprovalManager(bot, timeout=5)
        task = asyncio.create_task(manager.ask(5, [{"question": "Q", "options": [{"label": "A"}]}]))
        await _wait_sent(bot)
        request_id = _buttons(bot.sent[0][2])[0][1].split(":")[1]

        assert manager.handle_ask_callback(f"ask:{request_id}:select:0", chat_id=6) is False
        assert manager.handle_ask_callback("broken", chat_id=5) is False
        assert manager.handle_ask_callback(f"ask:{request_id}:select:0", chat_id=5) is True
        assert await task == {"Q": "A"}

    asyncio.run(scenario())


def test_ask_timeout_marks_question_expired_once() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        manager = ApprovalManager(bot, timeout=0.05)
        result = await manager.ask(5, [{"question": "Q", "options": [{"label": "A"}]}])

        assert result is None
        assert "⏱ Время ожидания истекло" in bot.edited[-1][2]
        assert manager.question_expired(5) is True
        assert manager.question_expired(5) is False

    asyncio.run(scenario())


def test_header_includes_network_target() -> None:
    manager = ApprovalManager(FakeBot(), timeout=1)
    header = manager._header("WebFetch", {"url": "https://example.org/x"}, None)
    assert "🌐 https://example.org/x" in header
    assert "WebFetch" in header

    search = manager._header("WebSearch", {"query": "qoder"}, None)
    assert "🌐 qoder" in search


def test_header_truncates_large_payload() -> None:
    manager = ApprovalManager(FakeBot(), timeout=1)
    header = manager._header("Write", {"content": "x" * 5000}, None)
    assert len(header) <= 3900
    assert "…" in header


def test_request_denied_when_signal_already_set() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        manager = ApprovalManager(bot, timeout=5)
        signal = FakeSignal()
        signal.set()

        decision = await manager.request(1, "Bash", {}, SimpleNamespace(signal=signal))

        assert decision is Decision.DENY
        assert bot.sent == []

    asyncio.run(scenario())


def test_resolve_wrong_action_is_ignored() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        manager = ApprovalManager(bot, timeout=5)
        task = asyncio.create_task(manager.request(1, "Read", {}, None))
        await _wait_sent(bot)
        request_id = _buttons(bot.sent[0][2])[0][1].split(":")[1]

        assert manager.resolve(request_id, Decision.ALLOW) is True
        assert manager.resolve(request_id, Decision.DENY) is False
        assert await task is Decision.ALLOW

    asyncio.run(scenario())
