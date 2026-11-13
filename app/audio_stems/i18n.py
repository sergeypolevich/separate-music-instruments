"""The texts of the page, collected for translation (version 21).

app.js marks its texts with t("…"), tn(count, "one", "other") and N_("…"); index.html is read as it is (element
texts and the aria-label, title and placeholder attributes; translate="no" skips an element). Every text belongs to
a UI block, which the translation service gets as context: the nearest section, header or dialog with an id in the
page, the top-level function or constant in app.js. `python -m audio_stems.i18n` writes web/i18n/catalog.json.
"""
from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
CATALOG = WEB / "i18n" / "catalog.json"

_LETTER = re.compile(r"[^\W\d_]")
_STRING = r'"((?:[^"\\]|\\.)*)"'
_CALL = re.compile(r"\b(?:t|N_)\(\s*" + _STRING + r"|\btn\([^,]+,\s*" + _STRING + r"\s*,\s*" + _STRING)
_TOP = re.compile(r"^(?:async\s+)?function\s+(\w+)|^(?:const|let|class)\s+(\w+)")
_ATTRIBUTES = ("aria-label", "title", "placeholder")
_BLOCK_TAGS = {"header", "section", "dialog", "nav"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str | None, bool]] = []  # tag, block id, skipped
        self.found: list[tuple[str, str, str]] = []  # block, widget, text

    def _block(self) -> str:
        return next((block for _tag, block, _skip in reversed(self.stack) if block), "page")

    def _skipped(self) -> bool:
        return any(skip for _tag, _block, skip in self.stack)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        block = values.get("id") if values.get("id") and (tag in _BLOCK_TAGS or values.get("role") == "dialog"
                                                          or values["id"].endswith("-overlay")) else None
        skip = tag in ("head", "script", "style", "svg") or values.get("translate") == "no"
        if not skip and not self._skipped():
            for name in _ATTRIBUTES:
                if values.get(name) and _LETTER.search(values[name]):
                    self.found.append((block or self._block(), f"{tag}[{name}]", values[name]))
        if tag not in _VOID:
            self.stack.append((tag, block, skip))

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop()[0] == tag:
                break

    def handle_data(self, data):
        text = " ".join(data.split())
        if text and _LETTER.search(text) and self.stack and not self._skipped():
            self.found.append((self._block(), self.stack[-1][0], text))


def page_texts(html: str) -> list[tuple[str, str, str]]:
    parser = _Page()
    parser.feed(html)
    return parser.found


def script_texts(js: str) -> list[tuple[str, str, str, str | None]]:
    """(block, widget, text, the singular of a plural or None) for every marked text, in the order of the file."""
    found, block = [], "script"
    for line in js.splitlines():
        top = _TOP.match(line)
        if top:
            block = top.group(1) or top.group(2)
        if line.lstrip().startswith("//"):
            continue
        for single, one, other in _CALL.findall(line):
            if single:
                found.append((block, "Text", json.loads(f'"{single}"'), None))
            else:
                found.append((block, "Text", json.loads(f'"{other}"'), json.loads(f'"{one}"')))
    return found


def build_catalog(web: Path = WEB) -> dict:
    """The blocks in the shape the translation service takes (block, elements with id, widget, text, index);
    a plural element also carries its singular as "one"."""
    blocks: dict[str, list[dict]] = {}
    entries = [(block, widget, text, None) for block, widget, text in page_texts((web / "index.html").read_text(encoding="utf-8"))]
    entries += script_texts((web / "app.js").read_text(encoding="utf-8"))
    for block, widget, text, one in entries:
        elements = blocks.setdefault(block, [])
        if any(element["text"] == text for element in elements):
            continue
        element = {"id": f"e{len(elements)}", "widget": widget, "text": text, "index": len(elements)}
        if one is not None:
            element["one"] = one
        elements.append(element)
    return {"blocks": [{"block": name, "elements": elements} for name, elements in blocks.items()]}


def render(catalog: dict) -> str:
    return json.dumps(catalog, ensure_ascii=False, indent=1) + "\n"


if __name__ == "__main__":
    CATALOG.parent.mkdir(exist_ok=True)
    CATALOG.write_text(render(build_catalog()), encoding="utf-8")
    texts = sum(len(block["elements"]) for block in build_catalog()["blocks"])
    sys.stdout.write(f"{CATALOG}: {texts} texts\n")
