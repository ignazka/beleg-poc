"""Interaktive Einführung: Knopf in der Seitenleiste startet eine Tour (driver.js) durch die Oberfläche.

Die Schritte stehen in assets/einfuehrung.js. Die Tour läuft komplett im Browser; sie wechselt die Tabs selbst
und löst keine Eingaben aus (also auch keinen Neustart des Skripts).
"""

import re
import time
from pathlib import Path

import streamlit as st

_ASSETS = Path(__file__).with_name("assets")
_DRIVER_CSS = "https://cdn.jsdelivr.net/npm/driver.js@1.3.1/dist/driver.css"

# RE-ALIS-Look für die Popups (Akzentfarbe wie in .streamlit/config.toml)
_STIL = """
<style>
.driver-popover.praktikant-tour {border-radius: 12px; max-width: 360px; padding: 18px 20px; color: #14151A;}
.driver-popover.praktikant-tour, .driver-popover.praktikant-tour * {
  font-family: "Source Sans", "Source Sans 3", sans-serif !important;}
.praktikant-tour .driver-popover-title {font-size: 1.15rem; font-weight: 700;}
.praktikant-tour .driver-popover-description {font-size: .98rem; line-height: 1.45; margin-top: 6px;}
.praktikant-tour .driver-popover-progress-text {color: #5B6170;}
.praktikant-tour .driver-popover-footer button {font-family: inherit; font-size: .92rem; border-radius: 8px;
  padding: 5px 12px; text-shadow: none; border: 1px solid #C25100; color: #C25100; background: #fff;}
.praktikant-tour .driver-popover-footer .driver-popover-next-btn {background: #C25100; color: #fff;}
.praktikant-tour .driver-popover-footer button:hover {filter: brightness(.95);}
.praktikant-tour .driver-popover-close-btn {color: #5B6170;}
.praktikant-tour .tour-fortschritt {margin-top: 10px; font-size: .85rem; color: #5B6170;}
</style>
"""


def einfuehrung_knopf() -> None:
    """Knopf in der Seitenleiste; ein Klick startet die Tour beim nächsten Zeichnen der Seite."""
    if st.sidebar.button("Einführung starten", icon=":material/school:", key="einfuehrung_start",
                         help="Zeigt Schritt für Schritt, wie die App funktioniert."):
        st.session_state["einfuehrung_jetzt"] = time.time()


def einfuehrung_ausfuehren() -> None:
    """Am Ende des Skripts aufrufen (alle Elemente sind dann gezeichnet). Läuft nur direkt nach dem Klick."""
    start = st.session_state.pop("einfuehrung_jetzt", None)
    if not start:
        return
    skript = (_ASSETS / "einfuehrung.js").read_text(encoding="utf-8")
    # Streamlits HTML-Filter (DOMPurify) verwirft ein <script>, dessen Text nach Markup aussieht („<b>“ in den
    # Erklärtexten). Deshalb jedes „<“ vor Buchstabe, „/“ oder „!“ als \x3c schreiben – in JS-Strings identisch.
    skript = re.sub(r"<(?=[/\w!])", lambda _m: "\\x3c", skript)
    # Zeitstempel als Kommentar: jedes Starten ist ein neues Element, damit das Skript erneut läuft
    # <link> filtert Streamlit heraus -> Stylesheet von driver.js per Skript laden
    css = (f"if (!document.querySelector('link[href=\"{_DRIVER_CSS}\"]')) {{ const l = document.createElement('link');"
           f" l.rel = 'stylesheet'; l.href = '{_DRIVER_CSS}'; document.head.appendChild(l); }}")
    st.html(f"{_STIL}<script>/* {start} */\n{css}\n{skript}</script>", unsafe_allow_javascript=True, width="content")
