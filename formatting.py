"""Безопасный рендеринг Markdown-ответов агента в Telegram HTML.

Telegram не понимает CommonMark целиком, поэтому используется узкое
подмножество: жирный, курсив, зачёркивание, inline/fenced code, ссылки,
заголовки и цитаты. Весь пользовательский текст экранируется; HTML-теги
создаёт только этот модуль.
"""

from __future__ import annotations

import html
import re

TELEGRAM_LIMIT = 4000

_FENCE_RE = re.compile(
    r"(?ms)^[ \t]*(```|~~~)[^\n]*\n(.*?)(?:^[ \t]*\1[ \t]*$|\Z)"
)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_BOLD_RE = re.compile(r"(?<![\w*])\*\*(?=\S)([^\n]*?\S)\*\*(?![\w*])")
_STRIKE_RE = re.compile(r"(?<!\w)~~(?=\S)([^\n]*?\S)~~(?!\w)")
_ITALIC_RE = re.compile(r"(?<![*\w])\*(?=\S)([^\n]*?\S)\*(?![*\w])")
_HEADING_RE = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*$")
_QUOTE_RE = re.compile(r"(?m)^[ \t]{0,3}&gt;[ \t]?(.+)$")
_HR_RE = re.compile(r"(?m)^[ \t]{0,3}(?:-{3,}|\*{3,}|_{3,})[ \t]*$")
_TAG_RE = re.compile(r"<[^>]+>")


def _stash(blocks: list[str], value: str) -> str:
    blocks.append(value)
    return f"\x00{len(blocks) - 1}\x00"


def render_telegram_html(text: str) -> str:
    """Сконвертировать Markdown в безопасный HTML для parse_mode=HTML."""

    if not text:
        return ""
    text = text.replace("\x00", " ")
    blocks: list[str] = []

    def fence(match: re.Match[str]) -> str:
        body = match.group(2).rstrip("\n")
        return _stash(blocks, "<pre><code>" + html.escape(body, quote=False) + "</code></pre>")

    text = _FENCE_RE.sub(fence, text)

    def inline_code(match: re.Match[str]) -> str:
        body = html.escape(match.group(1), quote=False)
        return _stash(blocks, f"<code>{body}</code>")

    text = _INLINE_CODE_RE.sub(inline_code, text)

    def link(match: re.Match[str]) -> str:
        label = html.escape(match.group(1), quote=False)
        url = html.escape(match.group(2), quote=True)
        return _stash(blocks, f'<a href="{url}">{label}</a>')

    text = _LINK_RE.sub(link, text)

    text = html.escape(text, quote=False)

    text = _HR_RE.sub("────────", text)
    text = _HEADING_RE.sub(lambda match: f"<b>{match.group(1)}</b>", text)
    text = _QUOTE_RE.sub(lambda match: f"<blockquote>{match.group(1)}</blockquote>", text)
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _STRIKE_RE.sub(r"<s>\1</s>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)

    for index, block in enumerate(blocks):
        text = text.replace(f"\x00{index}\x00", block)
    return text


def html_to_plain(text: str) -> str:
    """Резервный plain-text вариант, если Telegram не принял HTML."""

    return html.unescape(_TAG_RE.sub("", text))


def _split_raw(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = remaining.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


def _iter_fenced(text: str):
    """Разделить текст на обычные участки и тела fenced-блоков."""

    position = 0
    for match in _FENCE_RE.finditer(text):
        if match.start() > position:
            yield "text", text[position : match.start()]
        yield "code", match.group(2).rstrip("\n").replace("\x00", " ")
        position = match.end()
    if position < len(text):
        yield "text", text[position:]


def _escaped_len(text: str) -> int:
    """Длина text после html.escape(quote=False) без построения строки."""

    return sum(5 if char == "&" else 4 if char in "<>" else 1 for char in text)


def _chunk_code_body(body: str, budget: int) -> list[str]:
    """Разбить тело code-блока так, чтобы escaped-версия влезла в budget."""

    if not body:
        return []
    pieces: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in body.splitlines(keepends=True):
        line_len = _escaped_len(line)
        if line_len > budget:
            if current:
                pieces.append("".join(current))
                current = []
                current_len = 0
            start = 0
            used = 0
            for index, char in enumerate(line):
                char_len = 5 if char == "&" else 4 if char in "<>" else 1
                if used + char_len > budget and index > start:
                    pieces.append(line[start:index])
                    start = index
                    used = 0
                used += char_len
            if start < len(line):
                current = [line[start:]]
                current_len = used
            continue
        if current_len + line_len > budget and current:
            pieces.append("".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len

    if current:
        pieces.append("".join(current))
    return pieces


def render_html_chunks(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Разбить ответ на HTML-чанки, не превышающие лимит Telegram.

    Обычный текст режется до рендеринга, поэтому HTML-теги не разрываются.
    Длинные code-блоки делятся по строкам и рендерятся отдельными
    самостоятельными тегами <pre><code>...</code></pre>.
    """

    result: list[str] = []
    code_overhead = len("<pre><code>") + len("</code></pre>")
    code_budget = max(1, limit - code_overhead)

    def add(raw: str) -> None:
        raw = raw.strip()
        if not raw:
            return
        rendered = render_telegram_html(raw)
        if len(rendered) <= limit:
            result.append(rendered)
            return
        ratio = limit / max(1, len(rendered))
        target = max(1, min(len(raw) - 1, int(len(raw) * ratio * 0.95)))
        pieces = _split_raw(raw, target)
        if len(pieces) == 1:
            pieces = [raw[index : index + target] for index in range(0, len(raw), target)]
        for piece in pieces:
            add(piece)

    for kind, payload in _iter_fenced(text):
        if kind == "code":
            for piece in _chunk_code_body(payload, code_budget):
                result.append(
                    "<pre><code>" + html.escape(piece, quote=False) + "</code></pre>"
                )
        else:
            for chunk in _split_raw(payload, limit):
                add(chunk)
    return result
