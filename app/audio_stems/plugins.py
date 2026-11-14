"""The plugin store (version 22): the plugins that ship with the app, in web/plugins/<id>/.

A plugin is page code only (plugin.mjs, an ES module) with manifest.json and image.svg next to it. Nothing of a
plugin runs on the computer: heavy work such as chord detection is a service of the app that a plugin calls.
Plugins come only from this store, the user's own; there is no install from a file or a link.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PERMISSIONS = {"sound", "playback", "analysis"}  # what a card lists; the page gives a plugin only these API parts
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_FIELDS = ("name", "description", "author", "version")


def catalog(web: Path) -> dict[str, dict[str, object]]:
    """id → manifest of every valid plugin folder, in the order of the manifests' "order" field."""
    found = {}
    for manifest_path in sorted((web / "plugins").glob("*/manifest.json")):
        plugin_id = manifest_path.parent.name
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        # A broken manifest is a bug in this repository, not user input: fail loudly in the tests.
        assert _ID.match(plugin_id) and manifest.get("id") == plugin_id, f"plugin folder {plugin_id}"
        assert all(isinstance(manifest.get(field), str) and manifest[field] for field in _FIELDS), plugin_id
        assert set(manifest.get("permissions", [])) <= PERMISSIONS, plugin_id
        assert (manifest_path.parent / "plugin.mjs").is_file() and (manifest_path.parent / "image.svg").is_file(), plugin_id
        found[plugin_id] = {**manifest, "permissions": manifest.get("permissions", [])}
    return dict(sorted(found.items(), key=lambda item: item[1].get("order", 99)))
