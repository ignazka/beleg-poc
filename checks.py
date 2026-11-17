"""Rechen- und Plausibilitätsprüfungen im Code + Entscheidung, ob ein Beleg zur Nachfrage abgelegt wird.

Das Modell liest nur ab; alles Rechnen und Gegenprüfen passiert hier.
"""

from datetime import date

from beleg_schema import (
    TOLERANZ_SUMME,
    TOLERANZ_ZEILE,
    BelegAnalyse,
    betrag_de,
    datum_de,
    mwst_aus_brutto,
    mwst_aus_netto,
    summe,
)

TOLERANZ = TOLERANZ_SUMME
TRINKGELD_MAX_ANTEIL = 0.30


def _eur(x: float) -> str:
    return f"{betrag_de(x)} €"


def pruefen(a: BelegAnalyse) -> list[str]:
    """Liefert Auffälligkeiten (leere Liste = alles stimmig). Alle Rechnungen cent-genau (Decimal)."""
    befunde = []

    # 1. Ergeben die Steuerzeilen zusammen den gedruckten Gesamtbetrag?
    #    (fängt falsch gelesene Gesamtbeträge, Überträge/Zwischensummen und vergessene Zeilen ab)
    zeilen_brutto = []
    for z in a.steuerzeilen:
        if z.brutto is not None:
            zeilen_brutto.append(z.brutto)
        elif z.netto is not None and z.mwst is not None:
            zeilen_brutto.append(summe([z.netto, z.mwst]))
        else:
            zeilen_brutto = None
            break
    if zeilen_brutto and a.brutto is not None:
        gesamt = summe(zeilen_brutto)
        if abs(gesamt - a.brutto) > TOLERANZ_SUMME:
            befunde.append(
                f"Steuerzeilen ergeben {_eur(gesamt)}, als Gesamtbetrag erkannt wurde {_eur(a.brutto)} – "
                "Gesamtbetrag oder Steuerzeilen falsch gelesen."
            )

    saetze = [z.satz for z in a.steuerzeilen if z.satz is not None]
    if len(saetze) != len(set(saetze)):
        befunde.append("Ein Steuersatz kommt in der MwSt.-Aufstellung mehrfach vor – Zeile falsch gelesen.")

    # 2. Passt jede Zeile in sich? MwSt. = Satz × Netto, und Netto + MwSt. = Brutto (falls gedruckt)
    for z in a.steuerzeilen:
        if z.satz and z.netto is not None and z.mwst is not None:
            erwartet = mwst_aus_netto(z.netto, z.satz)
            if abs(erwartet - z.mwst) > TOLERANZ_ZEILE:
                zusatz = ""
                if abs(mwst_aus_brutto(z.netto, z.satz) - z.mwst) <= TOLERANZ_ZEILE:
                    zusatz = " (passt, wenn der Betrag brutto ist – dann stimmt aber die Gesamtsumme nicht)"
                befunde.append(
                    f"MwSt. {z.satz:g} %: {_eur(z.netto)} netto ergibt {_eur(erwartet)}, "
                    f"gelesen wurde {_eur(z.mwst)}{zusatz}."
                )
        if z.netto is not None and z.mwst is not None and z.brutto is not None:
            if abs(summe([z.netto, z.mwst]) - z.brutto) > TOLERANZ_SUMME:
                befunde.append(
                    f"Zeile {f'{z.satz:g}' if z.satz is not None else '?'} %: Netto {_eur(z.netto)} + MwSt. {_eur(z.mwst)} "
                    f"≠ Brutto {_eur(z.brutto)}."
                )

    # 3. EC-Betrag vs. Rechnung
    if a.ec_betrag is not None and a.brutto is not None:
        if a.ec_betrag < a.brutto - TOLERANZ:
            befunde.append(
                f"EC-Betrag {_eur(a.ec_betrag)} ist kleiner als die Rechnung {_eur(a.brutto)} "
                "(Teilzahlung oder falsch gelesen?)."
            )
        elif a.trinkgeld and a.trinkgeld > a.brutto * TRINKGELD_MAX_ANTEIL:
            befunde.append(
                f"Trinkgeld {_eur(a.trinkgeld)} ist mehr als {TRINKGELD_MAX_ANTEIL:.0%} der Rechnung – "
                "EC-Beleg gehört evtl. nicht zu dieser Rechnung."
            )

    # 4. Datum
    if a.datum and a.datum > date.today().isoformat():
        befunde.append(f"Datum {datum_de(a.datum)} liegt in der Zukunft.")

    return befunde


def nachfragen_gruende(a: BelegAnalyse, befunde: list[str]) -> list[str]:
    """Gründe, warum ein Mensch draufschauen muss. Leere Liste = kann automatisch weiter."""
    gruende = []
    if not a.lesbar or a.kategorie == "unlesbar":
        gruende.append("Beleg nicht (vollständig) lesbar.")
    if a.kategorie_unsicher:
        gruende.append(f"Belegart unsicher: {a.kategorie_grund or 'ohne Begründung'}")
    if a.kategorie == "sonstiger_beleg":
        gruende.append("Belegart keiner bekannten Kategorie zuzuordnen.")
    if a.kategorie == "nur_ec_beleg":
        gruende.append("Nur Zahlungsnachweis, keine Rechnung – Rechnung anfordern.")
    if a.fehlende_pflichtangaben:
        gruende.append("Pflichtangaben laut Kategorie fehlen: " + ", ".join(a.fehlende_pflichtangaben))
    if a.ausland:
        gruende.append("Auslandsbeleg – dafür gilt eine abweichende Handhabe.")
    if a.ec_passt_zur_rechnung is False:
        gruende.append("EC-Beleg passt nicht zur Rechnung (Datum/Uhrzeit/Adresse/Name).")

    # Pflichtanforderungen der Buchhaltung an eine Quittung: lesbar, Steuersatz, Datum
    if a.kategorie not in ("unlesbar", "nur_ec_beleg"):
        if not a.steuersatz_erkennbar and not a.steuerzeilen:
            gruende.append("Kein Steuersatz ausgewiesen.")
        elif a.steuersaetze and not a.ausland and set(a.steuersaetze) - {7, 19}:
            andere = ", ".join(f"{s:g} %" for s in a.steuersaetze if s not in (7, 19))
            gruende.append(f"Ungewöhnlicher Steuersatz: {andere} (erwartet 7 % und/oder 19 %).")
        fehlend = [name for name, wert in (("Gesamtbetrag", a.brutto), ("Datum", a.datum)) if wert is None]
        if fehlend:
            gruende.append("Pflichtangabe fehlt: " + ", ".join(fehlend))
    gruende += [f"Rechenprüfung: {b}" for b in befunde]
    return gruende


# ---------------------------------------------------------------------------
# Doppelt-Lesen: zwei Lesungen vergleichen
# ---------------------------------------------------------------------------

def _text_norm(s: str | None) -> str:
    return "".join(ch for ch in (s or "").casefold() if ch.isalnum())


def vergleichswerte(a: BelegAnalyse) -> dict[str, str]:
    """Die Felder, die beim Doppelt-Lesen übereinstimmen müssen – normalisiert, als Anzeigetext."""
    zeilen = sorted(
        (z.satz or 0, z.netto, z.mwst) for z in a.steuerzeilen
    )
    return {
        "Belegart": a.kategorie,
        "Belegnummer": _text_norm(a.belegnummer),
        "Aussteller": _text_norm(a.restaurant_name),
        "Datum": a.datum or "",
        "Gesamtbetrag": _eur(a.brutto) if a.brutto is not None else "",
        "EC-Betrag": _eur(a.ec_betrag) if a.ec_betrag is not None else "",
        "MwSt.-Aufstellung": "; ".join(
            f"{s:g} %: {_eur(n) if n is not None else '–'} / {_eur(m) if m is not None else '–'}"
            for s, n, m in zeilen
        ),
    }


def _gleich(feld: str, x: str, y: str) -> bool:
    if feld == "Aussteller" and x and y:
        return x in y or y in x  # "Aral Tankstelle" vs. "Aral" ist kein Lesefehler
    return x == y


def abweichungen(a: BelegAnalyse, b: BelegAnalyse) -> dict[str, tuple[str, str]]:
    """{Feld: (Wert Lesung 1, Wert Lesung 2)} für alle Felder, die nicht übereinstimmen."""
    va, vb = vergleichswerte(a), vergleichswerte(b)
    return {f: (va[f], vb[f]) for f in va if not _gleich(f, va[f], vb[f])}


def anzeige_wert(feld: str, a: BelegAnalyse) -> str:
    """Wert eines Vergleichsfelds lesbar (nicht normalisiert) für Meldungen."""
    roh = {"Belegnummer": a.belegnummer, "Aussteller": a.restaurant_name, "Datum": datum_de(a.datum)}
    return (roh.get(feld) if feld in roh else vergleichswerte(a)[feld]) or "–"
