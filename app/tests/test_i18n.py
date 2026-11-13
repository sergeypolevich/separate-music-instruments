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
