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


# --- SKR04-Konto ---------------------------------------------------------------------------------------

def _speichern(at):
    A.knopf(at, "Speichern").click().run()
    A.keine_fehler(at)


def test_konto_aus_vorlage_uebernehmen_und_speichern():
    at = A.app()
    _kategorie_uebernachtung(at)
    vorlage = next(s for s in at.selectbox if s.label == "Aus der Vorlage übernehmen")
    nr = next(i for i in vorlage.options if "6660" in i)  # Optionen sind die Anzeigetexte
    vorlage.select(nr).run()
    assert A.feld(at, "Kontonummer").value == "6660"
    assert A.feld(at, "Bezeichnung").value == "Reisekosten Arbeitnehmer Übernachtungsaufwand"
    A.feld(at, "Bezeichnung").set_value("Übernachtung Mitarbeiter").run()  # Vorlage anpassen
    _speichern(at)
    gespeichert = A.SPEICHER["kategorien"][-1]
    assert gespeichert["schluessel"] == "uebernachtung"
    assert (gespeichert["skr04_konto"], gespeichert["skr04_bezeichnung"]) == ("6660", "Übernachtung Mitarbeiter")
    # nach dem Speichern steht das Konto wieder im Formular und in der Übersicht
    assert A.feld(at, "Kontonummer").value == "6660"
    assert any("6660 · Übernachtung Mitarbeiter" in str(t.value) for t in at.table)


def test_eigenes_konto_ohne_bezeichnung():
    at = A.app()
    _kategorie_uebernachtung(at)
    A.feld(at, "Kontonummer").set_value("6999").run()
    _speichern(at)
    gespeichert = A.SPEICHER["kategorien"][-1]
    assert (gespeichert["skr04_konto"], gespeichert["skr04_bezeichnung"]) == ("6999", "")


def test_falsche_kontonummer_wird_nicht_gespeichert():
    at = A.app()
    _kategorie_uebernachtung(at)
    A.feld(at, "Kontonummer").set_value("66a").run()
    assert any("vier Ziffern" in e.value for e in at.error)
    A.knopf(at, "Speichern").click().run()
    assert any("Nicht gespeichert" in e.value for e in at.error)
    assert A.SPEICHER["kategorien"] == []


def test_ohne_konto_nur_hinweis():
    at = A.app()
    _kategorie_uebernachtung(at)
    assert any("Noch kein Konto festgelegt" in c.value for c in at.caption)
    _speichern(at)  # ohne Konto speichern bleibt erlaubt
    assert A.SPEICHER["kategorien"][-1]["skr04_konto"] == ""
