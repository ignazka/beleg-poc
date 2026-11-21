"""SKR04-Konten: Vorlage zur Auswahl im Kategorien-Tab.

Jede Kategorie bekommt ein SKR04-Sachkonto – das braucht später die Übergabe an sevDesk, damit der Beleg
richtig gebucht wird. Die Vorlage ist nur eine Auswahlhilfe: Der Kontenrahmen ist viel größer und wird
jährlich angepasst, deshalb lässt sich jede Nummer und Bezeichnung im Formular frei ändern oder eintragen.

Quelle: Kontenliste von Papierkram (https://www.papierkram.de/hilfe/kontenliste/, Stand 14.10.2026).
Weggelassen: zwei Zeilen mit dem Platzhalter 9999 (kein echtes SKR04-Konto). Korrigiert: „Reisekosten
Arbeitnehmer Fahrtkosten“ steht dort unter 6680 (das ist Übernachtung Unternehmer); laut SKR03-Gegenstück
4663 ist es 6663.
"""

import re

# (Kontonummer, Bezeichnung) – Reihenfolge wie in der Quelle
VORLAGE: tuple[tuple[str, str], ...] = (
    ("0135", "Software (Anlage)"),
    ("0200", "Grundstücke"),
    ("0400", "Technische Anlagen und Maschinen"),
    ("0510", "Andere Anlagen"),
    ("0520", "Fahrzeuge (PKW)"),
    ("0630", "Betriebsausstattung (Anlage)"),
    ("0635", "Geschäftsausstattung"),
    ("0650", "Büroeinrichtung"),
    ("0670", "GWG ab 2018: bis 800 EUR (Geringwertiges Wirtschaftsgut)"),
    ("0670", "GWG vor 2018: bis 410 EUR (Geringwertiges Wirtschaftsgut)"),
    ("0675", "GWG ab 2018: 250 bis 1000 EUR (Sammelposten)"),
    ("0675", "GWG vor 2018: 150 bis 1000 EUR (Sammelposten)"),
    ("3820", "Umsatzsteuervorauszahlungen"),
    ("3820", "Umsatzsteuervorauszahlung Guthaben"),
    ("3830", "Umsatzsteuervorauszahlungen 1/11"),
    ("3841", "Umsatzsteuer Vorjahr"),
    ("3841", "Umsatzsteuer Erstattung"),
    ("3845", "Umsatzsteuer frühere Jahre"),
    ("3845", "Umsatzsteuer Erstattung frühere Jahre"),
    ("3786", "Umsatzsteuer Erstattung (nur EÜR)"),
    ("2100", "Privatentnahmen allgemein"),
    ("2190", "Privateinlagen"),
    ("7300", "Zinsen und ähnliche Aufwendungen"),
    ("7600", "Körperschaftsteuer"),
    ("7680", "Grundsteuer"),
    ("6390", "Zuwendungen, Spenden steuerl. n. abziehb."),
    ("7100", "Zinsen und ähnliche Erträge"),
    ("7100", "Zuschüsse"),
    ("5110", "Roh-, Hilfs- und Betriebsstoffe"),
    ("5906", "Fremdleistung (Dienstleistung)"),
    ("5400", "Wareneingang"),
    ("5420", "EG-Erwerb 7% Vorsteuer 7% USt (5 %)"),
    ("5425", "EG-Erwerb 19% Vorsteuer 19% USt (16 %)"),
    ("5800", "Bezugsnebenkosten"),
    ("5820", "Leergut"),
    ("5820", "Erlöse Leergut"),
    ("6010", "Löhne"),
    ("6020", "Gehälter"),
    ("6110", "Gesetzliche Sozialaufwendungen"),
    ("6090", "Fahrtkostenerstatt. Whg./Arbeitsstätte"),
    ("6310", "Miete"),
    ("6320", "Heizung"),
    ("6325", "Gas, Strom, Wasser"),
    ("6330", "Reinigung"),
    ("6348", "Aufwendungen für ein häusliches Arbeitszimmer"),
    ("7610", "Gewerbesteuer"),
    ("6400", "Versicherungen"),
    ("7685", "Kfz-Steuern"),
    ("6520", "Kfz-Versicherungen"),
    ("6530", "Laufende Kfz-Betriebskosten"),
    ("6540", "Kfz-Reparaturen"),
    ("6550", "Garagenmieten"),
    ("6560", "Leasing"),
    ("6570", "Sonstige Kfz-Kosten"),
    ("6600", "Werbekosten"),
    ("6630", "Repräsentationskosten"),
    ("6640", "Bewirtungskosten"),
    ("6640", "Geschäftsessen"),
    ("6644", "Nicht abzugsfähige Bewirtungskosten"),
    ("6650", "Reisekosten Arbeitnehmer"),
    ("6663", "Reisekosten Arbeitnehmer Fahrtkosten"),
    ("6664", "Reisekosten Arbeitnehmer Verpflegungsmehraufwand"),
    ("6660", "Reisekosten Arbeitnehmer Übernachtungsaufwand"),
    ("6670", "Reisekosten Unternehmer"),
    ("6670", "Spesen (Reisekosten Unternehmer)"),
    ("6673", "Reisekosten Unternehmer Fahrtkosten"),
    ("6674", "Reisekosten Unternehmer Verpflegungsmehraufwand"),
    ("6680", "Reisekosten Unternehmer Übernachtungsaufwand"),
    ("6688", "Fahrten zwischen Wohnung und Betriebsstätte und Familienheimfahrten (abziehbarer Anteil)"),
    ("6689", "Fahrten zwischen Wohnung und Betriebsstätte und Familienheimfahrten (nicht abziehbarer Anteil)"),
    ("6740", "Ausgangsfrachten"),
    ("6770", "Verkaufsprovisionen"),
    ("6470", "Reparatur/Instandh. Betriebs- u. Geschäftsausstattung"),
    ("6498", "Mietleasing bewegliche Wirtschaftsgüter für technische Anlagen und Maschinen"),
    ("6300", "Sonstige betriebliche Aufwendungen"),
    ("6800", "Porto"),
    ("6805", "Telefon"),
    ("6815", "Bürobedarf"),
    ("6815", "Büroartikel"),
    ("6815", "Software/EDV (Büroartikel)"),
    ("6820", "Zeitschriften, Bücher"),
    ("6821", "Fortbildung"),
    ("6825", "Rechts- und Beratungskosten"),
    ("6830", "Buchführungskosten"),
    ("6855", "Nebenkosten des Geldverkehrs"),
    ("6850", "Betriebsbedarf"),
    ("4200", "Sonstige Einnahme (Erlöse)"),
    ("4500", "Provisionserlöse"),
    ("4620", "Privatentnahme Waren / Gegenstände"),
    ("4640", "Private Verwendung von Gegenständen"),
    ("4645", "Private KFZ Nutzung (1% Regel)"),
)

QUELLE = "Vorlage: Kontenliste von Papierkram (Stand 14.10.2026)"


def sortiert() -> list[tuple[str, str]]:
    """Vorlage nach Kontonummer sortiert – für die Auswahlliste."""
    return sorted(VORLAGE, key=lambda k: (k[0], k[1]))


def als_text(nummer: str | None, bezeichnung: str | None = None) -> str:
    """'6640', 'Bewirtungskosten' -> '6640 · Bewirtungskosten'; ohne Nummer leer."""
    nummer = (nummer or "").strip()
    if not nummer:
        return ""
    bezeichnung = (bezeichnung or "").strip()
    return f"{nummer} · {bezeichnung}" if bezeichnung else nummer


def nummer_pruefen(nummer: str) -> str | None:
    """Fehlertext oder None. Leer ist erlaubt (Konto noch nicht festgelegt).

    SKR04-Sachkonten haben vier Stellen (0000–9999); Personenkonten (fünf- und mehrstellig) gehören nicht
    an eine Belegart.
    """
    nummer = (nummer or "").strip()
    if nummer and not re.fullmatch(r"\d{4}", nummer):
        return "Die SKR04-Kontonummer hat genau vier Ziffern (z. B. 6640)."
    return None

