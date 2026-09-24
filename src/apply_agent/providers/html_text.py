"""Minimal HTML-to-text conversion for HTML-only emails, using only the stdlib.

It does not aim for faithful rendering. It aims for text an LLM can classify:
visible words, paragraph breaks, and no markup, scripts or styles.
"""

import re
from html.parser import HTMLParser
from typing import Final

_BLOCK_TAGS: Final = frozenset(
    {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"}
)
_INVISIBLE_TAGS: Final = frozenset({"script", "style", "head", "title"})
_SPACES: Final = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES: Final = re.compile(r"\n\s*\n+")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _INVISIBLE_TAGS:
            self._hidden_depth += 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _INVISIBLE_TAGS:
            self._hidden_depth = max(0, self._hidden_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self._chunks.append(data)

    def text(self) -> str:
        joined = _SPACES.sub(" ", "".join(self._chunks))
        lines = "\n".join(line.strip() for line in joined.split("\n"))
        return _BLANK_LINES.sub("\n\n", lines).strip()


def html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    return extractor.text()
