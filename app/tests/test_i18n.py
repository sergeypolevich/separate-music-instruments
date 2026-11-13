from audio_stems import i18n


def test_the_stored_catalog_has_every_text_of_the_page():
    # A text added to app.js or index.html without running `python -m audio_stems.i18n` would stay untranslated.
    assert i18n.CATALOG.read_text(encoding="utf-8") == i18n.render(i18n.build_catalog())


def test_page_texts_take_their_block_from_the_nearest_dialog_and_skip_what_is_not_translated():
    html = """<html><head><title>Audio Stems</title><script>const x = "Not this";</script></head><body>
      <header><a aria-label="Home page"><span translate="no">audiostems</span></a></header>
      <div id="help-overlay" hidden><section id="help-dialog" role="dialog">
        <h1>Keyboard shortcuts</h1><button title="Close"><svg><title>Icon</title></svg></button>
        <input placeholder="Artist — Song"><p>  Two   words </p><p>12:00</p>
      </section></div></body></html>"""
    assert i18n.page_texts(html) == [
        ("page", "a[aria-label]", "Home page"),
        ("help-dialog", "h1", "Keyboard shortcuts"),
        ("help-dialog", "button[title]", "Close"),
        ("help-dialog", "input[placeholder]", "Artist — Song"),
        ("help-dialog", "p", "Two words"),
    ]


def test_script_texts_take_their_block_from_the_top_level_function_and_keep_plural_forms():
    js = '''const states = { queued: N_("Queued") };
function renderQueue(items) {
  // t("Not a comment")
  const label = tn(items.length, "Queue: {count} task", "Queue: {count} tasks");
  cancel.title = t("Cancel");  cancel.ariaLabel = t("Cancel: {title}", { title });
  return t("Say \\"hi\\"");
}
'''
    assert i18n.script_texts(js) == [
        ("states", "Text", "Queued", None),
        ("renderQueue", "Text", "Queue: {count} tasks", "Queue: {count} task"),
        ("renderQueue", "Text", "Cancel", None),
        ("renderQueue", "Text", "Cancel: {title}", None),
        ("renderQueue", "Text", 'Say "hi"', None),
    ]


def test_catalog_blocks_have_the_shape_the_translation_service_takes():
    catalog = i18n.build_catalog()
    blocks = {block["block"]: block["elements"] for block in catalog["blocks"]}
    assert {"screen-settings", "search-overlay", "HOTKEYS", "renderQueue"} <= blocks.keys()
    for elements in blocks.values():
        assert [element["index"] for element in elements] == list(range(len(elements)))
        assert len({element["text"] for element in elements}) == len(elements)  # no repeats inside a block
        assert all(set(element) <= {"id", "widget", "text", "index", "one"} for element in elements)
    assert {"id": "e0", "widget": "Text", "text": "Queue: {count} tasks", "index": 0, "one": "Queue: {count} task"} in blocks["renderQueue"]
    assert not any("audiostems" in element["text"] for elements in blocks.values() for element in elements)


def test_the_language_is_the_users_choice_then_the_browsers_first_offered_one_then_english():
    offered = ["de", "en", "nb", "uk"]
    assert i18n.choose("uk", "de-DE,de;q=0.9", offered) == "uk"  # the choice in Settings wins
    assert i18n.choose("fr", "de-DE,de;q=0.9", offered) == "de"  # a cookie for a language no longer shipped
    assert i18n.choose(None, "fr-FR;q=0.9, de;q=0.8, en;q=0.5", offered) == "de"
    assert i18n.choose(None, "en;q=0.2, de;q=0.8", offered) == "de"  # by quality, not by position
    assert i18n.choose(None, "no", offered) == "nb"  # Norwegian is offered as Bokmål
    assert i18n.choose(None, "fr-FR, ja;q=bad", offered) == "en"
    assert i18n.choose(None, "", offered) == "en"


def test_the_page_carries_its_language_and_dictionary_before_app_js(tmp_path):
    (tmp_path / "i18n").mkdir()
    (tmp_path / "index.html").write_text(
        '<!doctype html>\n<html lang="en">\n  <head>\n    <link rel="stylesheet" href="app.css">\n'
        '    <script src="app.js" defer></script>\n  </head>\n</html>\n', encoding="utf-8")
    (tmp_path / "i18n" / "he.json").write_text('{"Settings": "x</script><b>"}', encoding="utf-8")
    (tmp_path / "i18n" / "catalog.json").write_text("{}", encoding="utf-8")  # not a language

    assert i18n.offered(tmp_path) == ["en", "he"]
    english = i18n.page("en", tmp_path)
    assert '<html lang="en">' in english and "i18n-pending" not in english
    assert 'window.AUDIO_STEMS_LANGUAGES = [["en", "English", "English", false], ["he", ' in english
    hebrew = i18n.page("he", tmp_path)
    assert '<html lang="he" data-rtl class="i18n-pending">' in hebrew
    assert "x<\\/script><b>" in hebrew and "x</script>" not in hebrew  # a text never closes the script
    assert hebrew.index("AUDIO_STEMS_I18N") < hebrew.index('<link rel="stylesheet"') < hebrew.index('src="app.js"')
    assert 'classList.remove("i18n-pending"), 1500' in hebrew  # shown anyway should app.js not run


def test_the_api_serves_the_page_in_the_chosen_language(tmp_path):
    from tests.test_web import get_app_client

    request, _repository = get_app_client(tmp_path)
    page = request("GET", "/")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-cache"
    assert "window.AUDIO_STEMS_LANGUAGES" in page.text and '<html lang="en">' in page.text
    assert request("GET", "/index.html").text == page.text
