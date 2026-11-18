"""Belegansicht neben dem Formular (App._beleg_vorschau + assets/belegansicht.js).

Alle Seiten werden in .st-key-belegbild_<key> gezeichnet; das Skript blendet die gewählte ein und bedient die
Blätterleiste (.belegnav[data-key]) und die Mini-Vorschauen (.st-key-belegdaumen_<key>). Ändern sich diese
Namen in App.py, blättert nichts mehr – dieser Test fällt dann auf. Das Verhalten im Browser prüft
tests/browser/belegansicht.mjs.
"""
import re
from pathlib import Path

import attrappe as A
from browser_skripte import skript_html

WURZEL = Path(__file__).resolve().parents[1]
JS = (WURZEL / "assets" / "belegansicht.js").read_text(encoding="utf-8")
APP = (WURZEL / "App.py").read_text(encoding="utf-8")


def test_skript_uebersteht_streamlits_html_filter():
    html = skript_html("belegansicht.js")
    skript = html.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    assert not re.findall(r"<[/\w!]", skript)


def test_namen_passen_zur_app():
    for praefix in ("belegbild_", "belegdaumen_"):
        assert f"st-key-{praefix}" in JS, praefix
        assert f'key=f"{praefix}{{key}}"' in APP, f"App.py vergibt keinen key {praefix}…"
    assert ".belegnav" in JS and 'class="belegnav" data-key=' in APP
    assert "dataset.schritt" in JS and 'data-schritt="-1"' in APP and 'data-schritt="1"' in APP


def _beleg_mit_seiten(sha, seiten):
    eintrag = A.analyse_eintrag(sha)
    eintrag["beleg_pfade"] = [f"belege/{sha}_{i}.png" for i in range(seiten)]
    return eintrag


def test_mehrere_seiten_mit_blaetterleiste_und_vorschauen():
    at = A.app(analysen=[_beleg_mit_seiten("drei", 3)])
    at.segmented_control[0].set_value("alle").run()
    A.keine_fehler(at)
    navs = [h.proto.body for h in at.get("html") if h.proto.body.startswith('<div class="belegnav"')]
    assert len(navs) == 1 and "Seite 1 von 3" in navs[0] and 'data-key="belege_drei"' in navs[0]
    assert not any(b.label == "Größer ansehen" for b in at.button)  # kein Vollbild-Klick mehr
    assert any("belegansicht.js" in h.proto.body for h in at.get("html"))


def test_eine_seite_ohne_blaetterleiste():
    at = A.app(analysen=[_beleg_mit_seiten("eins", 1)])
    at.segmented_control[0].set_value("alle").run()
    assert not any(h.proto.body.startswith('<div class="belegnav"') for h in at.get("html"))
