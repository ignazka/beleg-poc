"""Browser-Skripte aus assets/*.js in die Seite einbetten (st.html mit JavaScript).

Streamlit filtert st.html mit DOMPurify: Ein <script>, dessen Text nach Markup aussieht (z. B. „<b>“ in einem
String), wird komplett entfernt. Deshalb wird jedes „<“ vor Buchstabe, „/“ oder „!“ als \\x3c geschrieben –
in JavaScript-Strings bedeutet das dasselbe Zeichen.
"""
import re
from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).with_name("assets")


def maskieren(js: str) -> str:
    return re.sub(r"<(?=[/\w!])", lambda _m: "\\x3c", js)


def skript_html(datei: str, vorab: str = "", kennung: str = "", stil: str = "") -> str:
    """HTML mit <script> aus assets/<datei>. vorab: JS davor (z. B. Schalter). kennung: Kommentar – ein neuer Wert
    erzeugt ein neues Element, das Skript läuft dann erneut; gleicher Inhalt bleibt im Browser bestehen."""
    js = maskieren((ASSETS / datei).read_text(encoding="utf-8"))
    return f"{stil}<script>/* {kennung or datei} */\n{maskieren(vorab)}\n{js}</script>"


def skript_einbetten(datei: str, vorab: str = "", kennung: str = "", stil: str = "") -> None:
    st.html(skript_html(datei, vorab, kennung, stil), unsafe_allow_javascript=True, width="content")
