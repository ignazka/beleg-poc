"""Schnelltests ohne API-Aufruf:  python test_logik.py"""

from beleg_schema import BelegAnalyse, parse_betrag, parse_datum, parse_saetze, werte_gleich
from extraction import _json_extrahieren

# Beträge
assert parse_betrag("12,50 €") == 12.5
assert parse_betrag("1.234,56") == 1234.56
assert parse_betrag("1,234.56") == 1234.56
assert parse_betrag("") is None
assert parse_betrag("abc") is None
assert parse_betrag(7) == 7.0

# Datum
assert parse_datum("17.11.2026") == "2026-11-17"
assert parse_datum("17.11.26") == "2026-11-17"
assert parse_datum("2026-11-17T12:00") == "2026-11-17"
assert parse_datum("Montag") is None

# Steuersätze
assert parse_saetze("7, 19") == [7, 19]
assert parse_saetze([19, "7%"]) == [7, 19]
assert parse_saetze(None) == []
assert parse_saetze("7,19") == [7, 19]
assert parse_saetze("7 und 19 %") == [7, 19]

# Vergleich
assert werte_gleich("betrag", 85.0, "85,00")
assert not werte_gleich("betrag", 85.0, "85,10")
assert werte_gleich("betrag", None, "")
assert werte_gleich("betrag", 0.0, "")
assert not werte_gleich("betrag", None, "5,00")
assert werte_gleich("text", "Gasthaus  Zur Linde", "gasthaus zur linde")
assert werte_gleich("datum", "2026-11-14", "14.11.2026")
assert werte_gleich("saetze", [19, 7], "7,19")

# JSON-Extraktion
assert _json_extrahieren('{"a": 1}') == {"a": 1}
assert _json_extrahieren('```json\n{"a": 1}\n```') == {"a": 1}
assert _json_extrahieren('Hier das Ergebnis:\n```\n{"a": 1}\n```\nViel Erfolg') == {"a": 1}
assert _json_extrahieren('Ergebnis: {"a": {"b": 2}} fertig') == {"a": {"b": 2}}

# Pydantic: tolerant gegenüber schmutzigen Modell-Ausgaben
a = BelegAnalyse.model_validate({
    "kategorie": "Restaurant-Beleg / Quittung",
    "lesbar": "true",
    "brutto": "85,00",
    "trinkgeld": 5,
    "datum": "14.11.2026",
    "steuersaetze": "7 und 19",
    "hinweise": "nur ein String",
    "unbekanntes_feld": 1,
})
assert a.kategorie == "restaurant_beleg", a.kategorie
assert a.lesbar is True and a.brutto == 85.0 and a.datum == "2026-11-14"
assert a.steuersaetze == [7, 19] and a.hinweise == ["nur ein String"]
assert a.bewirtungsdaten()["Gesamtbetrag inkl. Trinkgeld"] == 90.0
assert BelegAnalyse.model_validate({"kategorie": "Quatsch"}).kategorie == "sonstiger_beleg"

from beleg_schema import anzeige  # noqa: E402

assert anzeige([7.0, 19.0]) == "7, 19"
assert anzeige(85.0) == "85.00"
assert anzeige(None) == ""

print("Alle Logik-Tests bestanden.")
