"""Lesequalität: Welche Lesung lag bei geprüften Belegen richtig?

Jede Analyse speichert ihre einzelnen Lesungen (pipeline.py): Lesung 1 vom Original, Lesung 2 von einer
Kontrast-Variante und – bei Zweifel – den Schiedsrichter in hoher Auflösung. Eine Prüfung im Tab „Belege“
liefert den richtigen Wert je Feld. Hier wird beides verglichen – ohne Modellaufruf.

Ziel: sehen, WO falsch gelesen wird (Textfelder, Beträge, Datum; Fotos/PDFs; ein-/mehrseitig) und ob die
zweite Lesung und der Schiedsrichter etwas bringen. Daraus folgen gezielte Verbesserungen (Bildaufbereitung,
Lesungen weglassen, spezielles Texterkennungs-Modell für bestimmte Felder).
"""
from beleg_schema import FEEDBACK_FELDER, BelegAnalyse, anzeige, werte_gleich

VARIANTEN = {  # Name in pipeline.py -> Anzeige
    "original": "Lesung 1 (Original)",
    "kontrast": "Lesung 2 (Kontrast)",
    "hochaufgelöst": "Schiedsrichter",
}
GRUPPEN = {  # Feldart -> Felder
    "Textfelder": ["belegnummer", "restaurant_name", "adresse"],
    "Beträge und Steuersätze": ["netto", "mwst", "brutto", "ec_betrag", "trinkgeld", "steuersaetze"],
    "Datum und Uhrzeit": ["datum", "uhrzeit"],
}
_TYP = {key: typ for key, _label, typ in FEEDBACK_FELDER}


def _gleich(key: str, a, b) -> bool:
    # wie im Prüfformular: beide Seiten als Anzeige-Text vergleichen (normalisiert Beträge, Datum, Sätze, Text)
    return werte_gleich(_TYP[key], anzeige(a), anzeige(b))


def _lesungen_je_variante(lesungen: list[dict]) -> dict[str, BelegAnalyse]:
    """Nur gelungene Lesungen (mit Analyse); bei doppelten Varianten zählt die erste."""
    ergebnis = {}
    for lesung in lesungen or []:
        variante, analyse = lesung.get("variante"), lesung.get("analyse")
        if variante in VARIANTEN and analyse and variante not in ergebnis:
            try:
                ergebnis[variante] = BelegAnalyse.model_validate(analyse)
            except Exception:  # alte/kaputte Einträge überspringen
                continue
    return ergebnis


def belegtyp(dateiname: str | None, seiten: int | None) -> list[str]:
    name = (dateiname or "").lower()
    typen = ["PDF" if ".pdf" in name else "Foto/Bild"]
    if seiten:
        typen.append("mehrseitig" if seiten > 1 else "einseitig")
    return typen


def auswerten(faelle: list[dict]) -> dict:
    """faelle: [{"bewertung": Feedback-Eintrag, "lesungen": [...], "dateiname": str, "seiten": int|None}].

    Rückgabe (Zähler immer als [richtig, gesamt]):
      belege            – geprüfte Belege mit mindestens einer gespeicherten Lesung
      felder[key][v]    – je Feld und Variante (+ "ergebnis" = gespeichertes Endergebnis)
      belegart[v]       – Belegart je Variante
      gruppen[name][v]  – zusammengefasst je Feldart
      je_typ[typ]       – Endergebnis je Belegtyp (Foto/PDF, ein-/mehrseitig): [richtige Felder, Felder]
      nur_original / nur_kontrast – Felder, in denen genau eine der beiden ersten Lesungen richtig lag
      schiedsrichter    – eingesetzt (Belege), gerettet (beide ersten falsch, er richtig),
                          verschlechtert (eine der ersten richtig, Endergebnis falsch)
    """
    erg = {
        "belege": 0,
        "felder": {key: {} for key, _l, _t in FEEDBACK_FELDER},
        "belegart": {},
        "gruppen": {name: {} for name in GRUPPEN},
        "je_typ": {},
        "nur_original": 0,
        "nur_kontrast": 0,
        "schiedsrichter": {"eingesetzt": 0, "gerettet": 0, "verschlechtert": 0},
    }

    def zaehlen(ziel: dict, variante: str, richtig: bool) -> None:
        z = ziel.setdefault(variante, [0, 0])
        z[0] += bool(richtig)
        z[1] += 1

    for fall in faelle:
        bewertung = fall["bewertung"]
        lesungen = _lesungen_je_variante(fall.get("lesungen"))
        if not lesungen:
            continue
        erg["belege"] += 1
        if "hochaufgelöst" in lesungen:
            erg["schiedsrichter"]["eingesetzt"] += 1
        typen = belegtyp(fall.get("dateiname"), fall.get("seiten"))

        # Belegart
        kat = bewertung["kategorie"]
        for variante, analyse in lesungen.items():
            zaehlen(erg["belegart"], variante, analyse.kategorie == kat["korrekt"])
        zaehlen(erg["belegart"], "ergebnis", kat["richtig"])

        # Felder
        for key, feld in (bewertung.get("felder") or {}).items():
            if key not in erg["felder"]:
                continue
            richtig = {v: _gleich(key, getattr(a, key), feld["korrekt"]) for v, a in lesungen.items()}
            richtig["ergebnis"] = bool(feld["richtig"])
            for variante, ok in richtig.items():
                zaehlen(erg["felder"][key], variante, ok)
                for gruppe, keys in GRUPPEN.items():
                    if key in keys:
                        zaehlen(erg["gruppen"][gruppe], variante, ok)
            for typ in typen:
                zaehlen(erg["je_typ"], typ, richtig["ergebnis"])
            if "original" in richtig and "kontrast" in richtig and richtig["original"] != richtig["kontrast"]:
                erg["nur_original" if richtig["original"] else "nur_kontrast"] += 1
            if "hochaufgelöst" in richtig:
                kleine = [richtig[v] for v in ("original", "kontrast") if v in richtig]
                if kleine and not any(kleine) and richtig["hochaufgelöst"]:
                    erg["schiedsrichter"]["gerettet"] += 1
                if any(kleine) and not richtig["ergebnis"]:
                    erg["schiedsrichter"]["verschlechtert"] += 1
    return erg


def quote(zaehler: list[int] | None) -> float | None:
    return zaehler[0] / zaehler[1] if zaehler and zaehler[1] else None
