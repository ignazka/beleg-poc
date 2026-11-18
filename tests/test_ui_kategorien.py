"""Tab „Kategorien“: KI-Assistent über den Lauf-Knopf, Meldungen, Probelauf-Knopf (simulierte Ablage/KI)."""
import attrappe as A

ASSISTENT = {"erkennungsmerkmale": "Zimmer, Übernachtung", "abgrenzung": "keine Bewirtung",
             "pflichtangaben": ["Name des Gastes", "Zeitraum"], "nachfragen_wenn": "Name fehlt",
             "rueckfragen": ["Gehört Frühstück dazu?"]}


def _kategorie_uebernachtung(at):
    at.selectbox(key="kategorie_wahl").set_value("uebernachtung").run()
    return next(t for t in at.text_area if t.label.startswith("Beschreibung der Buchhaltung"))


def test_assistent_fuellt_regeln():
    at = A.app(ki={"text_antwort": ASSISTENT})
    _kategorie_uebernachtung(at).set_value("Hotelrechnungen mit Zimmer").run()
    A.knopf(at, "Mit KI-Assistent in klare Regeln übersetzen").click().run()
    A.keine_fehler(at)
    assert "Hotelrechnungen mit Zimmer" in A.ANFRAGEN[0]
    werte = {t.label: t.value for t in at.text_area}
    assert werte["Woran erkennt man die Kategorie?"] == "Zimmer, Übernachtung"
    assert werte["Pflichtangaben auf dem Beleg (eine pro Zeile)"] == "Name des Gastes\nZeitraum"
    assert any("Gehört Frühstück dazu?" in w.value for w in at.warning)
    assert A.knopf(at, "Mit KI-Assistent in klare Regeln übersetzen").disabled is False  # wieder klickbar


def test_assistent_ohne_beschreibung_meldet_fehler():
    at = A.app(ki={"text_antwort": ASSISTENT})
    _kategorie_uebernachtung(at).set_value("   ").run()
    A.knopf(at, "Mit KI-Assistent in klare Regeln übersetzen").click().run()
    assert any("zuerst eine Beschreibung" in e.value for e in at.error)
    assert A.ANFRAGEN == []


def test_assistent_nicht_erreichbar():
    at = A.app()  # keine text_antwort -> Attrappe wirft einen Fehler
    _kategorie_uebernachtung(at).set_value("Hotel").run()
    A.knopf(at, "Mit KI-Assistent in klare Regeln übersetzen").click().run()
    assert any("Assistent nicht erreichbar" in e.value for e in at.error)


def test_lernen_fehler_wird_nach_dem_lauf_angezeigt():
    at = A.app(analysen=[A.analyse_eintrag("x")],
               feedback=[A.bewertung("x", richtige_kat="uebernachtung", kommentar="Zimmer")])
    next(b for b in at.button if b.label.startswith("Verbesserungsvorschläge erstellen")).click().run()
    assert any("Vorschläge konnten nicht erstellt werden" in e.value for e in at.error)
    assert not next(b for b in at.button if b.label.startswith("Verbesserungsvorschläge erstellen")).disabled
