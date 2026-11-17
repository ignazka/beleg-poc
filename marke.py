"""Name und Kopfzeile der App: „PraktikantAI“ (AI fett, in Marken-Orange), darunter „powered by“ + Logo."""

import base64
from functools import lru_cache
from pathlib import Path

import streamlit as st

APP_NAME = "PraktikantAI"
# Marken-Orange leicht abgedunkelt: 4,0:1 auf Weiß – reicht für große Schrift (WCAG AA verlangt dort 3:1)
_AI_FARBE = "#D65900"
ASSETS = Path(__file__).parent / "assets"


@lru_cache(maxsize=1)
def _logo_data_uri() -> str:
    """Logo direkt in die Seite einbetten (kein zusätzlicher Dateiabruf nötig)."""
    svg = (ASSETS / "realis-logo.svg").read_bytes()
    return "data:image/svg+xml;base64," + base64.b64encode(svg).decode("ascii")


def kopfzeile() -> None:
    """Hauptüberschrift als echte h1 (für Screenreader), darunter „powered by RE-ALIS“."""
    try:
        logo = (f'<img src="{_logo_data_uri()}" alt="RE-ALIS" '
                'style="height:1.6rem;width:auto;vertical-align:middle">')
    except OSError:  # Logo-Datei fehlt -> Text statt Bild
        logo = "<strong>RE-ALIS</strong>"
    st.markdown(
        '<h1 style="margin-bottom:0;padding-bottom:0;font-size:3.2rem;line-height:1.1;letter-spacing:-.02em">'
        '<span style="font-weight:400">Praktikant</span>'
        f'<span style="font-weight:800;color:{_AI_FARBE}">AI</span></h1>'
        '<div style="display:flex;align-items:center;gap:.5rem;margin:.1rem 0 1.25rem;'
        f'font-size:.85rem;opacity:.75">powered by {logo}</div>',
        unsafe_allow_html=True,
    )
