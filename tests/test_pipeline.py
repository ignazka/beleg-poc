"""Ablauf einer Analyse (pipeline.beleg_lesen): doppelt lesen, vergleichen, Schiedsrichter – mit simulierter KI."""
import importlib

import pytest

from beleg_schema import BelegAnalyse
from extraction import AnalyseFehler

# Aral-Tankbeleg aus tests/test_logik.py: stimmig, keine Nachfrage
SAUBER = {"kategorie": "tankbeleg", "lesbar": True, "steuersatz_erkennbar": True, "datum_erkennbar": True,
          "restaurant_name": "Aral Tankstelle", "datum": "2026-08-24", "brutto": 103.27, "ec_betrag": 103.27,
          "steuerzeilen": [{"satz": 19, "netto": 86.78, "mwst": 16.49, "brutto": 103.27}]}
ANDERS = {**SAUBER, "restaurant_name": "Arel Tankstelle"}  # Lesefehler im Text (Rechnung bleibt stimmig)
NOCH_ANDERS = {**SAUBER, "restaurant_name": "Esso Station"}  # deutlich anders (kleine Tippfehler gelten als gleich)


@pytest.fixture
def pipeline(monkeypatch):
    import pipeline as modul
    modul = importlib.reload(modul)  # falls ein UI-Test beleg_lesen ersetzt hat
    monkeypatch.setattr(modul, "kontrast_variante", lambda bild: b"KONTRAST")
    return modul


def ki(pipeline, monkeypatch, original, kontrast, gross=None):
    """Antworten je Lesung: dict -> Analyse, Exception -> Fehler. Gibt die Liste der Aufrufe zurück."""
    aufrufe = []

    def analysieren(bilder, modell, system):
        variante = "gross" if modell == pipeline.MODEL_GROSS else ("kontrast" if bilder[0] == b"KONTRAST" else "original")
        aufrufe.append(variante)
        antwort = {"original": original, "kontrast": kontrast, "gross": gross}[variante]
        if isinstance(antwort, Exception):
            raise antwort
        return BelegAnalyse.model_validate(antwort), "{}", {"tokens": 1}

    monkeypatch.setattr(pipeline, "analysieren", analysieren)
    return aufrufe


def test_gleiche_lesungen_ohne_schiedsrichter(pipeline, monkeypatch):
    aufrufe = ki(pipeline, monkeypatch, SAUBER, SAUBER)
    erg = pipeline.beleg_lesen([b"BILD"])
    assert sorted(aufrufe) == ["kontrast", "original"]
    assert not erg.schiedsrichter and erg.modell == pipeline.MODEL
    assert erg.gruende == [] and erg.abweichungen == {}


def test_abweichung_schiedsrichter_entscheidet(pipeline, monkeypatch):
    aufrufe = ki(pipeline, monkeypatch, SAUBER, ANDERS, gross=SAUBER)
    erg = pipeline.beleg_lesen([b"BILD"])
    assert "gross" in aufrufe and erg.schiedsrichter and erg.modell == pipeline.MODEL_GROSS
    assert erg.abweichungen and erg.ungeklaert == []  # großes Modell bestätigt eine der beiden Lesungen
    assert not any(g.startswith("Unsicher gelesen") for g in erg.gruende)
    assert erg.analyse.restaurant_name == "Aral Tankstelle"


def test_abweichung_bleibt_unklar(pipeline, monkeypatch):
    ki(pipeline, monkeypatch, SAUBER, ANDERS, gross=NOCH_ANDERS)
    erg = pipeline.beleg_lesen([b"BILD"])
    assert erg.ungeklaert  # großes Modell liest etwas Drittes -> Mensch muss schauen
    assert any(g.startswith("Unsicher gelesen") for g in erg.gruende)


def test_eine_lesung_fehlt_schiedsrichter_liest(pipeline, monkeypatch):
    aufrufe = ki(pipeline, monkeypatch, SAUBER, RuntimeError("Zeitüberschreitung"), gross=SAUBER)
    erg = pipeline.beleg_lesen([b"BILD"])
    assert "gross" in aufrufe and erg.schiedsrichter
    assert erg.gruende == []


def test_grosses_modell_nicht_erreichbar(pipeline, monkeypatch):
    ki(pipeline, monkeypatch, SAUBER, ANDERS, gross=RuntimeError("503"))
    erg = pipeline.beleg_lesen([b"BILD"])
    assert erg.modell == pipeline.MODEL  # Ergebnis der ersten Lesung gilt …
    assert any("Großes Modell nicht verfügbar" in g for g in erg.gruende)  # … aber mit Nachfrage
    assert any("Doppelt gelesen, Abweichung" in g for g in erg.gruende)


def test_alle_lesungen_fehlgeschlagen(pipeline, monkeypatch):
    fehler = RuntimeError("kein Netz")
    ki(pipeline, monkeypatch, fehler, fehler, gross=fehler)
    with pytest.raises(AnalyseFehler):
        pipeline.beleg_lesen([b"BILD"])


def test_rechenpruefung_loest_schiedsrichter_aus(pipeline, monkeypatch):
    falsch = {**SAUBER, "brutto": 113.27}  # Gesamtbetrag passt nicht zu den Steuerzeilen
    aufrufe = ki(pipeline, monkeypatch, falsch, falsch, gross=falsch)
    erg = pipeline.beleg_lesen([b"BILD"])
    assert "gross" in aufrufe
    assert any(g.startswith("Rechenprüfung") for g in erg.gruende)
