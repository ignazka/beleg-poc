"""Tab „Auswertung“: Kennzahlen und „Aus Korrekturen lernen“ bis zur Übernahme in den Kategorien-Tab."""
import attrappe as A


def _daten():
    return dict(
        analysen=[A.analyse_eintrag("n1", gruende=["Unsicher gelesen"]),
                  A.analyse_eintrag("n2", gruende=["Ausland"]),
                  A.analyse_eintrag("ok")],
        feedback=[
            A.bewertung("n1"),                                         # Nachfrage, alles richtig -> unnötig
            A.bewertung("ok", ki_kat="restaurant_mit_ec_beleg", richtige_kat="uebernachtung",
                        kommentar="Zimmerbuchung"),
            A.bewertung("ok", ki_kat="restaurant_mit_ec_beleg", richtige_kat="uebernachtung",
                        felder_falsch=["brutto"], kommentar="Zimmerbuchung, Betrag falsch"),  # jüngste zählt
        ],
    )


def _metrik(at, label):
    return next(m.value for m in at.metric if m.label == label)


def test_kennzahlen():
    at = A.app(**_daten())
    assert _metrik(at, "Belege") == "3"
    assert _metrik(at, "Geprüft") == "2 von 3"
    assert _metrik(at, "Nachfragen der KI") == "2"
    assert _metrik(at, "Unnötige Nachfragen") == "1 von 1"
    assert _metrik(at, "Geprüfte Belege") == "2"            # „ok“ zählt einmal (jüngste Prüfung)
    assert _metrik(at, "Belegart richtig") == "1 von 2"
    assert _metrik(at, "Alle Felder richtig") == "1 von 2"


def test_aus_korrekturen_lernen_bis_kategorie():
    antwort = {
        "faelle": [{"nr": 1, "ursache": "regel", "kurz": "Zimmer als Bewirtung"},
                   {"nr": 2, "ursache": "bewertung_widerspruechlich", "kurz": "Kommentar widerspricht"}],
        "vorschlaege": [{"kategorie": "uebernachtung", "feld": "erkennungsmerkmale",
                         "text": "Zimmer-Positionen bedeuten Übernachtung.", "faelle": [1, 2],
                         "sicherheit": "hoch"},
                        {"kategorie": "gibtsnicht", "feld": "abgrenzung", "text": "wird verworfen"}],
        "rueckfragen": ["Gehört Frühstück zur Übernachtung?"],
    }
    at = A.app(ki={"text_antwort": antwort}, **_daten())
    knopf = next(b for b in at.button if b.label.startswith("Verbesserungsvorschläge erstellen"))
    assert "(2 Fälle)" in knopf.label  # korrigiert oder nachgefragt; „Unsicher gelesen“ zählt mit
    knopf.click().run()
    A.keine_fehler(at)
    assert "FÄLLE" in A.ANFRAGEN[0] and "Zimmerbuchung, Betrag falsch" in A.ANFRAGEN[0]
    assert [m.value for m in at.markdown if m.value.startswith("> ")] == ["> Zimmer-Positionen bedeuten Übernachtung."]
    assert any("Bewertung widerspricht dem Kommentar" in w.value for w in at.warning)
    assert any("Rückfragen an die Buchhaltung" in w.value for w in at.warning)

    A.knopf(at, "In Kategorie übernehmen").click().run()
    A.keine_fehler(at)
    assert at.selectbox(key="kategorie_wahl").value == "uebernachtung"
    feld = next(t for t in at.text_area if t.label == "Woran erkennt man die Kategorie?")
    assert feld.value.endswith("Zimmer-Positionen bedeuten Übernachtung.")
    assert any("noch nicht gespeichert" in i.value for i in at.info)
    assert A.SPEICHER["kategorien"] == []  # übernommen heißt nicht gespeichert


def test_ohne_pruefungen():
    at = A.app(analysen=[A.analyse_eintrag("x")])
    assert any("Noch keine geprüften Belege" in i.value for i in at.info)


def test_belege_nur_einmal_pro_durchlauf_geladen():
    A.app(**_daten())
    assert A.LADEN.get("analysen") == 1, A.LADEN  # Tab-Beschriftung, Tab „Belege“ und Auswertung teilen sich
