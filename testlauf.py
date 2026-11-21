"""Testlauf: Beispielbelege einer Kategorie mit altem und neuem Kategorie-Wissen analysieren und vergleichen.

So sieht die Buchhaltung VOR dem Aktivieren, ob eine geänderte Beschreibung die Erkennung verbessert.
Ohne Musterlösung wird gemessen: Wird der Beleg als die erwartete Kategorie erkannt? Muss nachgefragt werden?
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from collections import Counter

from beleg_schema import BEWIRTUNG_KATEGORIEN, betrag_de, datum_de, konto_waehlen
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
    konto: str | None = None
    konto_unsicher: bool = False


def _analysieren(beleg: tuple[str, bytes, str], system: str, konten: dict | None = None) -> Ergebnis:
    """konten: Konto-Regeln DIESER Variante ({kategorie: {"standard", "ausnahmen"}}). Ohne: wie gelesen."""
    try:
        dateien = [beleg]
        bilder = dateien_zu_bildern(dateien)[0]
        e = beleg_lesen(bilder, dateien, system=system)
    except Exception as ex:
        return Ergebnis(None, True, [], "", "", f"{type(ex).__name__}: {ex}")
    a = e.analyse
    gruende, konto, unsicher = list(e.gruende), a.konto, a.konto_unsicher
    if konten is not None:
        konto, unsicher = konto_waehlen(konten.get(a.kategorie), a.konto, a.konto_unsicher)
        if not unsicher:  # Konto-Nachfrage gilt nur, wenn diese Variante Ausnahmen kennt
            gruende = [g for g in gruende if not g.startswith("Konto unsicher")]
    return Ergebnis(a.kategorie, bool(gruende), gruende,
                    f"{betrag_de(a.brutto)} €" if a.brutto is not None else "–", datum_de(a.datum) or "–",
                    konto=konto, konto_unsicher=unsicher)


def konten_vereinigen(*konten: dict[str, dict]) -> dict[str, dict]:
    """Für die Dauer des Probelaufs: alle Konten beider Varianten erlauben (ohne Standard), damit die Wahl der
    KI erhalten bleibt. Welches Konto je Variante gilt, entscheidet danach konto_waehlen mit deren Regeln."""
    alle: dict[str, set] = {}
    for k in konten:
        for kategorie, regel in k.items():
            alle.setdefault(kategorie, set()).update(
                x for x in [regel.get("standard"), *(regel.get("ausnahmen") or [])] if x)
    return {kategorie: {"standard": None, "ausnahmen": sorted(nummern)} for kategorie, nummern in alle.items()}


def ausfuehren(belege: list[tuple[str, bytes, str]], varianten: dict[str, str], fortschritt=None,
               konten: dict[str, dict] | None = None):
    """belege: [(name, bytes, mime)]; varianten: {"vorher": system_prompt, "nachher": system_prompt};
    konten: {variante: Konto-Regeln} (optional). -> {beleg_name: {variante: Ergebnis}}"""
    melden = fortschritt or (lambda _fertig, _gesamt: None)
    auftraege = [(b, v, system) for b in belege for v, system in varianten.items()]
    ergebnisse: dict[str, dict[str, Ergebnis]] = {b[0]: {} for b in belege}
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        futures = {pool.submit(_analysieren, b, system, (konten or {}).get(v)): (b[0], v)
                   for b, v, system in auftraege}
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
        "konten": Counter(e.konto for e in werte if e.konto and not e.fehler),
    }
