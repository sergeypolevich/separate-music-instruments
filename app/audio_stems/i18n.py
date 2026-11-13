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
COOKIE = "audio-stems-language"  # the user's choice in Settings; the page sets it

# The languages the app offers: code → (English name, own name). Offered once web/i18n/<code>.json exists.
LANGUAGES = {
    "sq": ("Albanian", "Shqip"), "ar": ("Arabic", "العربية"), "be": ("Belarusian", "Беларуская"),
    "bs": ("Bosnian", "Bosanski"), "bg": ("Bulgarian", "Български"), "ca": ("Catalan", "Català"),
    "zh": ("Chinese", "中文（简体）"), "hr": ("Croatian", "Hrvatski"), "cs": ("Czech", "Čeština"),
    "da": ("Danish", "Dansk"), "nl": ("Dutch", "Nederlands"), "en": ("English", "English"), "et": ("Estonian", "Eesti"),
    "fi": ("Finnish", "Suomi"), "fr": ("French", "Français"), "gl": ("Galician", "Galego"), "de": ("German", "Deutsch"),
    "el": ("Greek", "Ελληνικά"), "he": ("Hebrew", "עברית"), "hi": ("Hindi", "हिन्दी"), "hu": ("Hungarian", "Magyar"),
    "it": ("Italian", "Italiano"), "lv": ("Latvian", "Latviešu"), "lt": ("Lithuanian", "Lietuvių"),
    "mk": ("Macedonian", "Македонски"), "nb": ("Norwegian", "Norsk"), "fa": ("Persian", "فارسی"),
    "pl": ("Polish", "Polski"), "pt": ("Portuguese", "Português"), "ro": ("Romanian", "Română"),
    "ru": ("Russian", "Русский"), "sr": ("Serbian", "Српски"), "sk": ("Slovak", "Slovenčina"),
    "sl": ("Slovenian", "Slovenščina"), "es": ("Spanish", "Español"), "sv": ("Swedish", "Svenska"),
    "tr": ("Turkish", "Türkçe"), "uk": ("Ukrainian", "Українська"),
}
# Written right to left: the text runs that way, the layout is not mirrored (the user's choice).
RIGHT_TO_LEFT = {"ar", "he", "fa"}
_ALIASES = {"no": "nb", "nn": "nb", "iw": "he"}  # browser tags that name an offered language differently

_LETTER = re.compile(r"[^\W\d_]")
_STRING = r'"((?:[^"\\]|\\.)*)"'
_CALL = re.compile(r"\b(?:t|N_)\(\s*" + _STRING + r"|\btn\([^,]+,\s*" + _STRING + r"\s*,\s*" + _STRING)
_TOP = re.compile(r"^(?:async\s+)?function\s+(\w+)|^(?:const|let|class)\s+(\w+)")
_ATTRIBUTES = ("aria-label", "title", "placeholder")
# Server texts the page shows: API error messages, errors that become a job's summary or an analysis' error,
# the models' names and descriptions.
_SERVER = re.compile(r'"message":\s*' + _STRING + r'|AdapterError\(\s*' + _STRING
                     + r'|_Model\(\s*' + _STRING + r',\s*' + _STRING
                     + r'|fail_analysis\([^,]+,[^,]+,\s*' + _STRING)
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


def server_texts(python: str, module: str) -> list[tuple[str, str, str, None]]:
    """Literal server texts (a template such as "The file is larger than {limit} MB" keeps its placeholders)."""
    found = []
    for groups in _SERVER.findall(python):
        for text in filter(None, groups):
            found.append((f"server: {module}", "Message", json.loads(f'"{text}"'), None))
    return found


def build_catalog(web: Path = WEB) -> dict:
    """The blocks in the shape the translation service takes (block, elements with id, widget, text, index);
    a plural element also carries its singular as "one"."""
    blocks: dict[str, list[dict]] = {}
    entries = [(block, widget, text, None) for block, widget, text in page_texts((web / "index.html").read_text(encoding="utf-8"))]
    entries += script_texts((web / "app.js").read_text(encoding="utf-8"))
    for module in sorted((web.parent / "audio_stems").glob("*.py")):
        entries += server_texts(module.read_text(encoding="utf-8"), module.stem)
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


def offered(web: Path = WEB) -> list[str]:
    """English and every language whose translation ships in web/i18n, in the order of LANGUAGES."""
    return [code for code in LANGUAGES if code == "en" or (web / "i18n" / f"{code}.json").is_file()]


def choose(cookie: str | None, accept_language: str, languages: list[str]) -> str:
    """The user's choice; on the first start the browser's first language that is offered; else English."""
    if cookie in languages:
        return cookie
    ranked = []
    for position, part in enumerate(accept_language.split(",")):
        tag, _, quality = part.strip().partition(";q=")
        try:
            ranked.append((-float(quality or 1), position, tag.strip().lower()))
        except ValueError:
            continue
    for _quality, _position, tag in sorted(ranked):
        primary = tag.split("-")[0]
        code = _ALIASES.get(primary, primary)
        if code in languages:
            return code
    return "en"


def page(code: str, web: Path = WEB) -> str:
    """index.html in the chosen language: lang on <html>, the dictionary and the offered languages before app.js.
    Until app.js has put the static texts into the language the page stays hidden (no English shows for a moment);
    should app.js not run, it shows anyway after 1.5 s."""
    html = (web / "index.html").read_text(encoding="utf-8")
    dictionary = {} if code == "en" else json.loads((web / "i18n" / f"{code}.json").read_text(encoding="utf-8"))
    languages = [[c, LANGUAGES[c][1], LANGUAGES[c][0], c in RIGHT_TO_LEFT] for c in offered(web)]
    script = (f"window.AUDIO_STEMS_I18N = {json.dumps(dictionary, ensure_ascii=False)};\n"
              f"window.AUDIO_STEMS_LANGUAGES = {json.dumps(languages, ensure_ascii=False)};")
    attributes = f'lang="{code}"' + (" data-rtl" if code in RIGHT_TO_LEFT else "")
    if code != "en":
        attributes += ' class="i18n-pending"'
        script += '\nsetTimeout(() => document.documentElement.classList.remove("i18n-pending"), 1500);'
    script = script.replace("</", "<\\/")  # a text can never close the script element
    assert '<html lang="en">' in html and '<link rel="stylesheet"' in html, "index.html changed its head"
    html = html.replace('<html lang="en">', f"<html {attributes}>", 1)
    return html.replace('<link rel="stylesheet"', f"<script>{script}</script>\n    <link rel=\"stylesheet\"", 1)


if __name__ == "__main__":
    CATALOG.parent.mkdir(exist_ok=True)
    CATALOG.write_text(render(build_catalog()), encoding="utf-8")
    texts = sum(len(block["elements"]) for block in build_catalog()["blocks"])
    sys.stdout.write(f"{CATALOG}: {texts} texts\n")
