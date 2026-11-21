"""SKR04-Vorlage und Konto der Kategorie (skr04.py, kategorien.konto_text) – Konto ändert den Prompt nicht."""
import re

import skr04
from kategorien import aktueller_stand, als_dokument, konto_text, prompt_abschnitt, wissens_hash


def test_vorlage_nur_vierstellige_nummern_ohne_doppelte():
    assert len(skr04.VORLAGE) > 80
    assert all(re.fullmatch(r"\d{4}", n) and b.strip() for n, b in skr04.VORLAGE)
    assert len(set(skr04.VORLAGE)) == len(skr04.VORLAGE)
    assert ("6640", "Bewirtungskosten") in skr04.VORLAGE
    assert "9999" not in {n for n, _ in skr04.VORLAGE}


def test_nummer_pruefen():
    assert skr04.nummer_pruefen("") is None and skr04.nummer_pruefen(" 6640 ") is None
    for falsch in ("664", "66400", "66a0", "6640 Bewirtung"):
        assert skr04.nummer_pruefen(falsch)


def test_konto_text():
    assert konto_text({"skr04_konto": "6640", "skr04_bezeichnung": "Bewirtungskosten"}) == "6640 · Bewirtungskosten"
    assert konto_text({"skr04_konto": "6999", "skr04_bezeichnung": " "}) == "6999"
    assert konto_text({"skr04_bezeichnung": "ohne Nummer"}) == ""
    assert konto_text({}) == ""  # alte Versionen ohne Konto


def test_konto_aendert_prompt_und_version_nicht():
    vorher = aktueller_stand([])
    nachher = aktueller_stand([{"schluessel": "tankbeleg", "version": 1, "zeitpunkt": "2026-11-21T10:00:00",
                                "beschreibung": vorher["tankbeleg"]["beschreibung"],
                                "skr04_konto": "6530", "skr04_bezeichnung": "Laufende Kfz-Betriebskosten"}])
    assert prompt_abschnitt(nachher) == prompt_abschnitt(vorher)
    assert wissens_hash(nachher) == wissens_hash(vorher)
    assert "SKR04-Konto: 6530 · Laufende Kfz-Betriebskosten" in als_dokument(nachher["tankbeleg"])
    assert "SKR04-Konto: noch nicht festgelegt" in als_dokument(vorher["uebernachtung"])
