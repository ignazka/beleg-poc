"""Datenmodell für die Beleg-Analyse: Kategorien, Felder, Validierung und Normalisierung."""

import re
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

# Feste Kategorienliste – das Modell MUSS eine davon wählen.
KATEGORIEN = {
    "restaurant_beleg": "Restaurant-Beleg / Quittung",
    "restaurant_mit_ec_beleg": "Restaurant-Beleg + EC-/Kartenbeleg",
    "nur_ec_beleg": "Nur EC-/Kartenbeleg (keine Rechnung)",
    "bewirtungsbeleg_intern": "Interner Bewirtungsbeleg (Formular)",
    "sonstiger_beleg": "Sonstiger Beleg (keine Bewirtung)",
    "unlesbar": "Unlesbar / kein Beleg",
}

# Felder, die im Feedback einzeln bewertet/korrigiert werden: (key, Label, Typ)
FEEDBACK_FELDER = [
    ("belegnummer", "Belegnummer", "text"),
    ("restaurant_name", "Restaurant", "text"),
    ("adresse", "Adresse", "text"),
    ("datum", "Datum (JJJJ-MM-TT)", "datum"),
    ("uhrzeit", "Uhrzeit (HH:MM)", "text"),
    ("netto", "Netto (€)", "betrag"),
    ("mwst", "MwSt. gesamt (€)", "betrag"),
    ("brutto", "Brutto (€)", "betrag"),
    ("trinkgeld", "Trinkgeld (€)", "betrag"),
    ("steuersaetze", "Steuersätze (%)", "saetze"),
]

_LEER = {"", "null", "none", "-", "–"}


# ---------------------------------------------------------------------------
# Normalisierung (für Modell-Ausgabe UND Nutzer-Korrekturen)
# ---------------------------------------------------------------------------

def parse_betrag(value):
    """'12,50 €', '1.234,56', 12.5, '' -> float | None"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    s = str(value).strip().replace("€", "").replace("EUR", "").replace(" ", "")
    if s.lower() in _LEER:
        return None
    if "," in s and "." in s:
        # Das zuletzt stehende Zeichen ist das Dezimaltrennzeichen
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def parse_datum(value):
    """'2026-11-17', '17.11.2026', '17.11.26' -> 'YYYY-MM-DD' | None"""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    if s.lower() in _LEER:
        return None
    for fmt, teil in (("%Y-%m-%d", s[:10]), ("%d.%m.%Y", s), ("%d.%m.%y", s), ("%d/%m/%Y", s)):
        try:
            return datetime.strptime(teil, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_text(value):
    if value is None:
        return None
    s = str(value).strip()
    return None if s.lower() in _LEER else s


def parse_saetze(value):
    """[7, 19], '7, 19', '19%' -> sortierte Liste von Zahlen"""
    if value is None:
        return []
    if isinstance(value, (int, float)):
        value = [value]
    if isinstance(value, str):
        value = [value]
    # Aus jedem Element die Zahlen ziehen ("7%", "7 und 19", "7,19", 19.0).
    # Komma gilt hier als Trennzeichen zwischen Sätzen, nur Punkt als Dezimaltrenner.
    teile = []
    for v in value:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            teile.append(v)
        else:
            teile.extend(re.findall(r"\d+(?:\.\d+)?", str(v)))
    value = teile
    result = set()
    for v in value:
        b = parse_betrag(v)
        if b is not None:
            result.add(int(b) if float(b).is_integer() else b)
    return sorted(result)


def normalisieren(feld_typ, value):
    if feld_typ == "betrag":
        return parse_betrag(value)
    if feld_typ == "datum":
        return parse_datum(value)
    if feld_typ == "saetze":
        return parse_saetze(value)
    return parse_text(value)


def werte_gleich(feld_typ, a, b):
    """Vergleich nach Normalisierung (bei Text: Groß-/Kleinschreibung & Leerzeichen egal)."""
    a, b = normalisieren(feld_typ, a), normalisieren(feld_typ, b)
    if feld_typ == "betrag":
        # 0,00 und "nicht vorhanden" gelten als gleich (Modell liefert z. B. trinkgeld 0.0 statt null)
        return abs((a or 0.0) - (b or 0.0)) < 0.005
    if feld_typ == "text":
        def norm(x):
            return re.sub(r"\s+", " ", x).casefold() if x else None
        return norm(a) == norm(b)
    return a == b


def anzeige(value):
    """Wert -> String für Anzeige / Editor."""
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(
            str(int(v)) if isinstance(v, float) and v.is_integer() else str(v) for v in value
        )
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


# ---------------------------------------------------------------------------
# Pydantic-Modell der Modell-Antwort
# ---------------------------------------------------------------------------

class BelegAnalyse(BaseModel):
    kategorie: str = "sonstiger_beleg"
    lesbar: bool = False
    steuersatz_erkennbar: bool = False
    datum_erkennbar: bool = False
    belegnummer: str | None = None
    restaurant_name: str | None = None
    adresse: str | None = None
    datum: str | None = None
    uhrzeit: str | None = None
    netto: float | None = None
    mwst: float | None = None
    brutto: float | None = None
    trinkgeld: float | None = None
    steuersaetze: list[float] = Field(default_factory=list)
    hinweise: list[str] = Field(default_factory=list)

    @field_validator("kategorie", mode="before")
    @classmethod
    def _kategorie(cls, v):
        s = str(v or "").strip().lower().replace("-", "_").replace(" ", "_")
        if s in KATEGORIEN:
            return s
        for key, label in KATEGORIEN.items():  # Toleranz: Label statt Schlüssel
            if str(v).strip().casefold() == label.casefold():
                return key
        return "sonstiger_beleg"

    @field_validator("netto", "mwst", "brutto", "trinkgeld", mode="before")
    @classmethod
    def _betrag(cls, v):
        return parse_betrag(v)

    @field_validator("datum", mode="before")
    @classmethod
    def _datum(cls, v):
        return parse_datum(v)

    @field_validator("belegnummer", "restaurant_name", "adresse", "uhrzeit", mode="before")
    @classmethod
    def _text(cls, v):
        return parse_text(v)

    @field_validator("steuersaetze", mode="before")
    @classmethod
    def _saetze(cls, v):
        return parse_saetze(v)

    @field_validator("hinweise", mode="before")
    @classmethod
    def _hinweise(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        return [str(x) for x in v if str(x).strip()]

    @field_validator("lesbar", "steuersatz_erkennbar", "datum_erkennbar", mode="before")
    @classmethod
    def _bool(cls, v):
        if isinstance(v, str):
            return v.strip().lower() in {"true", "ja", "yes", "1"}
        return bool(v)

    def bewirtungsdaten(self) -> dict:
        """Daten für den internen Bewirtungsbeleg – im Code abgeleitet, nicht doppelt vom Modell."""
        gesamt = None
        if self.brutto is not None:
            gesamt = round(self.brutto + (self.trinkgeld or 0), 2)
        return {
            "Belegnummer": self.belegnummer,
            "Restaurant": self.restaurant_name,
            "Adresse": self.adresse,
            "Tag der Bewirtung": self.datum,
            "Brutto (Rechnung)": self.brutto,
            "MwSt.": self.mwst,
            "Steuersätze": self.steuersaetze,
            "Trinkgeld": self.trinkgeld,
            "Gesamtbetrag inkl. Trinkgeld": gesamt,
        }

