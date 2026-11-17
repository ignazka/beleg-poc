"""Testlauf: Beispielbelege einer Kategorie mit altem und neuem Kategorie-Wissen analysieren und vergleichen.

So sieht die Buchhaltung VOR dem Aktivieren, ob eine geänderte Beschreibung die Erkennung verbessert.
Ohne Musterlösung wird gemessen: Wird der Beleg als die erwartete Kategorie erkannt? Muss nachgefragt werden?
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from beleg_schema import BEWIRTUNG_KATEGORIEN, betrag_de, datum_de
from extraction import dateien_zu_bildern
from pipeline import beleg_lesen

MAX_BELEGE = 15
PARALLEL = 4


def erwartete_kategorien(schluessel: str) -> set[str]:
    """Bewirtungs-Unterarten zählen gegenseitig als Treffer (Restaurant-Beleg vs. Restaurant + EC)."""
    return set(BEWIRTUNG_KATEGORIEN) if schluessel in BEWIRTUNG_KATEGORIEN else {schluessel}


@dataclass
class Ergebnis:
    kategorie: str | None
    nachfrage: bool
    gruende: list[str]
    betrag: str
    datum: str
    fehler: str | None = None


def _analysieren(beleg: tuple[str, bytes, str], system: str) -> Ergebnis:
    try:
        dateien = [beleg]
        bilder = dateien_zu_bildern(dateien)[0]
        e = beleg_lesen(bilder, dateien, system=system)
    except Exception as ex:
        return Ergebnis(None, True, [], "", "", f"{type(ex).__name__}: {ex}")
    a = e.analyse
    return Ergebnis(a.kategorie, bool(e.gruende), e.gruende,
                    f"{betrag_de(a.brutto)} €" if a.brutto is not None else "–", datum_de(a.datum) or "–")


def ausfuehren(belege: list[tuple[str, bytes, str]], varianten: dict[str, str], fortschritt=None):
    """belege: [(name, bytes, mime)]; varianten: {"vorher": system_prompt, "nachher": system_prompt}.
    -> {beleg_name: {variante: Ergebnis}}"""
    melden = fortschritt or (lambda _fertig, _gesamt: None)
    auftraege = [(b, v, system) for b in belege for v, system in varianten.items()]
    ergebnisse: dict[str, dict[str, Ergebnis]] = {b[0]: {} for b in belege}
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        futures = {pool.submit(_analysieren, b, system): (b[0], v) for b, v, system in auftraege}
        for fertig, f in enumerate(as_completed(futures), start=1):
            name, variante = futures[f]
            ergebnisse[name][variante] = f.result()
            melden(fertig, len(auftraege))
    return ergebnisse


def zusammenfassung(ergebnisse: dict, variante: str, erwartet: set[str]) -> dict:
    werte = [e[variante] for e in ergebnisse.values() if variante in e]
    return {
        "belege": len(werte),
        "erkannt": sum(1 for e in werte if e.kategorie in erwartet),
        "nachfragen": sum(1 for e in werte if e.nachfrage),
        "fehler": sum(1 for e in werte if e.fehler),
    }
