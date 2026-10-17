"""문서 속 HTML 표(<table>)를 행·칸 목록으로 읽는 시험용 도우미."""

from __future__ import annotations

from html.parser import HTMLParser


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[dict]]] = []
        self._row: list[dict] | None = None
        self._cell: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self.tables.append([])
        elif tag == "tr":
            self._row = []
            self.tables[-1].append(self._row)
        elif tag in ("td", "th"):
            self._cell = {"tag": tag, "text": "", "colspan": int(a.get("colspan") or 1), "rowspan": int(a.get("rowspan") or 1), "cls": a.get("class") or ""}
            self._row.append(self._cell)

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._cell["text"] = self._cell["text"].strip()
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"] += data


def html_tables(markdown: str) -> list[list[list[dict]]]:
    parser = _Parser()
    parser.feed(markdown)
    return parser.tables


def texts(row: list[dict]) -> list[str]:
    return [c["text"] for c in row]
