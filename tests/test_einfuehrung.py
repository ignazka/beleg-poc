"""Einführung (Tour): Skript wird korrekt eingebettet, und alle Tour-Ziele gibt es in der App noch.

Der zweite Test schlägt an, wenn ein Bereich umbenannt wurde, auf den die Tour zeigt – dann
assets/einfuehrung.js nachziehen (siehe CLAUDE.md, „Einführung (Tour) immer mitdenken“).
"""
import re
from pathlib import Path

import attrappe as A
import einfuehrung

WURZEL = Path(__file__).resolve().parents[1]
JS = (WURZEL / "assets" / "einfuehrung.js").read_text(encoding="utf-8")


def _skript(html: str) -> str:
    return html.split("<script>", 1)[1].rsplit("</script>", 1)[0]


def test_skript_uebersteht_streamlits_html_filter():
    html = einfuehrung._tour_html(True, "auto")
    assert "window.TOUR_AUTO = true;" in html
    assert not re.findall(r"<[/\w!]", _skript(html))  # sonst entfernt DOMPurify das ganze <script>
    assert "driver.js@1.3.1" in html and "driver.css" in html
    assert "window.TOUR_AUTO = false;" in einfuehrung._tour_html(False, "123")


def test_tour_ziele_gibt_es_in_der_app():
    quelltext = "".join((WURZEL / d).read_text(encoding="utf-8") for d in ("App.py", "einfuehrung.py"))
    for key in set(re.findall(r"\.st-key-([a-z_]+)", JS)):
        assert re.search(rf'key=f?"{key}', quelltext), f"Tour zeigt auf .st-key-{key}, den es nicht mehr gibt"
    for text in re.findall(r'knopfMitText\("([^"]+)"\)', JS):
        assert f'"{text}' in quelltext, f"Tour sucht den Knopf „{text}“, den es nicht mehr gibt"
    assert JS.count("tab: ") >= 10 and max(int(t) for t in re.findall(r"tab: (\d)", JS)) <= 3


def test_app_bettet_tour_ein():
    at = A.app()
    htmls = [h.proto for h in at.get("html")]
    auto = [h for h in htmls if "window.TOUR_AUTO = true;" in h.body]
    assert len(auto) == 1 and auto[0].unsafe_allow_javascript  # Autostart-Prüfung bei jedem Durchlauf
    A.knopf(at, "Einführung starten").click().run()
    htmls = [h.proto.body for h in at.get("html")]
    assert any("window.TOUR_AUTO = false;" in b for b in htmls)  # Knopf: Tour startet immer
