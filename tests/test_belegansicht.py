"""Große Belegansicht im Browser (assets/belegansicht.js): eingebettet, filterfest und passend zur App.

Das Skript findet Bilder und Knöpfe über die key-Präfixe belegbild_/belegmehr_/bildklick_/gross_. Ändert sich
einer davon in App.py, öffnet die schnelle Ansicht nicht mehr (nur noch der langsame Rückfall) – dieser Test
fällt dann auf. Das Verhalten im Browser prüft tests/browser/belegansicht.mjs.
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


def test_key_praefixe_passen_zur_app():
    for praefix in ("belegbild_", "belegmehr_", "bildklick_", "gross_"):
        assert f"st-key-{praefix}" in JS, praefix
        assert f'key=f"{praefix}{{key}}"' in APP, f"App.py vergibt keinen key {praefix}…"


def test_app_bettet_skript_ein():
    at = A.app(analysen=[A.analyse_eintrag("mehr")])
    assert any("belegansicht.js" in h.proto.body and h.proto.unsafe_allow_javascript for h in at.get("html"))


def test_rueckfall_dialog_ohne_skript():
    """Ohne Browser-Skript (AppTest führt kein JS aus) öffnet der Knopf weiterhin das Streamlit-Fenster."""
    at = A.app(analysen=[A.analyse_eintrag("x")])
    at.segmented_control[0].set_value("alle").run()
    A.knopf(at, "Größer ansehen").click().run()
    A.keine_fehler(at)
    assert any("Seite 1 von 1" in c.value for c in at.caption) or at.get("dialog")
