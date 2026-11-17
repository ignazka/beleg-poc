"""Beleg-Klassifizierung PoC – Upload, Analyse, Feedback."""

import time

import streamlit as st

from beleg_schema import FEEDBACK_FELDER, KATEGORIEN, anzeige, normalisieren, werte_gleich
from extraction import MAX_PDF_SEITEN, MODEL, AnalyseFehler, analysieren, datei_zu_bildern
from prompts import PROMPT_VERSION
from storage import beleg_speichern, datei_hash, feedback_laden, feedback_speichern, schon_bewertet

st.set_page_config(page_title="Beleg-Klassifizierung PoC", layout="wide")


def _kategorie_label(key):
    return KATEGORIEN.get(key, key)


# ---------------------------------------------------------------------------
# Analyse ausführen und Ergebnis im Session-State halten
# ---------------------------------------------------------------------------

def analyse_starten(uploaded_file):
    file_bytes = uploaded_file.getvalue()
    sha = datei_hash(file_bytes)
    try:
        bilder, seiten = datei_zu_bildern(file_bytes, uploaded_file.type)
    except Exception as e:  # defektes PDF / Bildformat
        st.session_state["fehler"] = (f"Datei konnte nicht gelesen werden: {e}", None)
        return

    t0 = time.time()
    try:
        analyse, raw, usage = analysieren(bilder)
    except AnalyseFehler as e:
        st.session_state["fehler"] = (str(e), e.raw)
        return
    except Exception as e:
        st.session_state["fehler"] = (f"Fehler beim Modell-Aufruf: {e}", None)
        return

    st.session_state.pop("fehler", None)
    st.session_state["ergebnis"] = {
        "sha": sha,
        "dateiname": uploaded_file.name,
        "mime": uploaded_file.type,
        "file_bytes": file_bytes,
        "vorschau": bilder,
        "seiten": seiten,
        "analyse": analyse,
        "raw": raw,
        "usage": usage,
        "dauer_s": round(time.time() - t0, 1),
    }
    st.session_state["gespeichert"] = False


# ---------------------------------------------------------------------------
# Anzeige
# ---------------------------------------------------------------------------

def zeige_ergebnis(erg):
    analyse = erg["analyse"]
    col_bild, col_daten = st.columns([1, 2])

    with col_bild:
        st.subheader("Hochgeladener Beleg")
        if erg["seiten"] > MAX_PDF_SEITEN:
            st.warning(
                f"PDF hat {erg['seiten']} Seiten – nur die ersten {MAX_PDF_SEITEN} wurden analysiert."
            )
        for bild in erg["vorschau"]:
            st.image(bild, width="stretch")

    with col_daten:
        st.subheader("Ergebnis der KI")
        c1, c2, c3 = st.columns(3)
        c1.metric("Kategorie", _kategorie_label(analyse.kategorie))
        c2.metric("Lesbar", "Ja" if analyse.lesbar else "Nein")
        c3.metric("Dauer", f"{erg['dauer_s']} s")

        st.write("**Prüfungen:**")
        p1, p2 = st.columns(2)
        p1.write(f"Steuersatz erkennbar: {'✅' if analyse.steuersatz_erkennbar else '❌'}")
        p2.write(f"Datum erkennbar: {'✅' if analyse.datum_erkennbar else '❌'}")

        if analyse.hinweise:
            st.warning("**Hinweise der KI:**\n\n" + "\n".join(f"- {h}" for h in analyse.hinweise))
        else:
            st.success("Keine besonderen Hinweise.")

        st.subheader("Daten für Bewirtungsbeleg")
        daten = analyse.bewirtungsdaten()
        st.dataframe(
            {"Feld": list(daten.keys()), "Wert": [anzeige(v) or "–" for v in daten.values()]},
            hide_index=True,
            width="stretch",
        )

        with st.expander("Technische Details (Rohantwort, Tokens)"):
            st.caption(f"Modell: `{MODEL}` · Prompt-Version: `{PROMPT_VERSION}`")
            st.json(erg["usage"])
            st.code(erg["raw"], language="json")

    st.divider()
    zeige_feedback(erg)


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def zeige_feedback(erg):
    analyse = erg["analyse"]
    st.subheader("Feedback: Was hat die KI richtig erkannt?")

    if st.session_state.get("gespeichert"):
        st.success("Feedback gespeichert – danke! Du kannst den nächsten Beleg hochladen.")
        return
    if schon_bewertet(erg["sha"]):
        st.info("Dieser Beleg wurde schon einmal bewertet. Ein neues Feedback wird zusätzlich gespeichert.")

    st.caption(
        "Korrigiere nur die Werte, die falsch sind. Leeres Feld = steht nicht auf dem Beleg. "
        "Beträge mit Komma oder Punkt, Datum als JJJJ-MM-TT oder TT.MM.JJJJ."
    )

    kategorie_keys = list(KATEGORIEN.keys())
    with st.form(key=f"feedback_{erg['sha']}"):
        kat_korrekt = st.selectbox(
            "Richtige Kategorie",
            options=kategorie_keys,
            index=kategorie_keys.index(analyse.kategorie),
            format_func=_kategorie_label,
        )

        korrekturen = {}
        links, rechts = st.columns(2)
        for i, (key, label, _typ) in enumerate(FEEDBACK_FELDER):
            spalte = links if i % 2 == 0 else rechts
            korrekturen[key] = spalte.text_input(
                label, value=anzeige(getattr(analyse, key)), key=f"fb_{erg['sha']}_{key}"
            )

        hinweise_ok = st.radio(
            "Waren die Hinweise der KI zutreffend?",
            ["Ja", "Teilweise", "Nein", "Keine Hinweise ausgegeben"],
            horizontal=True,
            index=None,
        )
        kommentar = st.text_area(
            "Kommentar (optional)", placeholder="z. B. Trinkgeld stand handschriftlich auf dem Beleg"
        )
        bearbeiter = st.text_input("Dein Kürzel", value=st.session_state.get("bearbeiter", ""))
        abgeschickt = st.form_submit_button("Feedback speichern", type="primary")

    if not abgeschickt:
        return

    ungueltig = []
    felder = {}
    for key, label, typ in FEEDBACK_FELDER:
        modell_wert = getattr(analyse, key)
        eingabe = korrekturen[key]
        korrekt = normalisieren(typ, eingabe)
        # Eingabe nicht leer, aber nicht lesbar -> Fehler statt stillem Datenverlust
        if eingabe.strip() and typ in ("betrag", "datum") and korrekt is None:
            ungueltig.append(label)
        felder[key] = {
            "modell": modell_wert,
            "korrekt": korrekt,
            "richtig": werte_gleich(typ, modell_wert, eingabe),
        }

    if ungueltig:
        st.error("Bitte prüfe diese Eingaben, sie konnten nicht gelesen werden: " + ", ".join(ungueltig))
        return

    st.session_state["bearbeiter"] = bearbeiter
    pfad = beleg_speichern(erg["file_bytes"], erg["dateiname"], erg["sha"])
    feedback_speichern({
        "datei_sha256": erg["sha"],
        "dateiname": erg["dateiname"],
        "beleg_pfad": pfad,
        "modell": MODEL,
        "prompt_version": PROMPT_VERSION,
        "dauer_s": erg["dauer_s"],
        "usage": erg["usage"],
        "rohantwort": erg["raw"],
        "analyse": analyse.model_dump(),
        "kategorie": {
            "modell": analyse.kategorie,
            "korrekt": kat_korrekt,
            "richtig": analyse.kategorie == kat_korrekt,
        },
        "felder": felder,
        "hinweise_zutreffend": hinweise_ok,
        "kommentar": kommentar.strip() or None,
        "bearbeiter": bearbeiter.strip() or None,
    })
    st.session_state["gespeichert"] = True
    st.rerun()



# ---------------------------------------------------------------------------
# Auswertung
# ---------------------------------------------------------------------------

def zeige_auswertung():
    eintraege = feedback_laden()
    if not eintraege:
        st.info("Noch kein Feedback vorhanden. Lade Belege hoch und bewerte die Ergebnisse.")
        return

    versionen = sorted({e.get("prompt_version", "?") for e in eintraege}, reverse=True)
    modelle = sorted({e.get("modell", "?") for e in eintraege})
    f1, f2 = st.columns(2)
    v_auswahl = f1.multiselect("Prompt-Version", versionen, default=versionen)
    m_auswahl = f2.multiselect("Modell", modelle, default=modelle)
    eintraege = [
        e for e in eintraege
        if e.get("prompt_version", "?") in v_auswahl and e.get("modell", "?") in m_auswahl
    ]
    if not eintraege:
        st.warning("Keine Einträge für diese Auswahl.")
        return

    n = len(eintraege)
    kat_richtig = sum(1 for e in eintraege if e["kategorie"]["richtig"])
    komplett = sum(
        1 for e in eintraege
        if e["kategorie"]["richtig"] and all(f["richtig"] for f in e["felder"].values())
    )
    m1, m2, m3 = st.columns(3)
    m1.metric("Bewertete Belege", n)
    m2.metric("Kategorie richtig", f"{kat_richtig / n:.0%}")
    m3.metric("Beleg komplett richtig", f"{komplett / n:.0%}")

    st.subheader("Trefferquote pro Feld")
    zeilen = []
    for key, label, _typ in FEEDBACK_FELDER:
        werte = [e["felder"][key]["richtig"] for e in eintraege if key in e.get("felder", {})]
        if werte:
            zeilen.append({
                "Feld": label,
                "Trefferquote": sum(werte) / len(werte),
                "Richtig": sum(werte),
                "Falsch": len(werte) - sum(werte),
            })
    st.dataframe(
        zeilen,
        hide_index=True,
        width="stretch",
        column_config={
            "Trefferquote": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
        },
    )

    st.subheader("Kategorien: KI vs. korrekt")
    matrix = {}
    for e in eintraege:
        paar = (_kategorie_label(e["kategorie"]["korrekt"]), _kategorie_label(e["kategorie"]["modell"]))
        matrix[paar] = matrix.get(paar, 0) + 1
    st.dataframe(
        [{"Korrekt": k, "KI sagte": m, "Anzahl": c} for (k, m), c in sorted(matrix.items())],
        hide_index=True,
        width="stretch",
    )

    st.subheader("Fehler im Detail")
    fehler = []
    for e in reversed(eintraege):
        for key, label, _typ in FEEDBACK_FELDER:
            f = e["felder"].get(key)
            if f and not f["richtig"]:
                fehler.append({
                    "Zeitpunkt": e.get("zeitpunkt"),
                    "Datei": e.get("dateiname"),
                    "Feld": label,
                    "KI": anzeige(f["modell"]),
                    "Korrekt": anzeige(f["korrekt"]),
                    "Kommentar": e.get("kommentar") or "",
                })
    if fehler:
        st.dataframe(fehler, hide_index=True, width="stretch")
    else:
        st.success("Keine Feldfehler in der Auswahl.")


# ---------------------------------------------------------------------------
# Seitenaufbau
# ---------------------------------------------------------------------------

def _zustand_zuruecksetzen():
    for k in ("ergebnis", "fehler", "gespeichert"):
        st.session_state.pop(k, None)


st.title("Beleg-Klassifizierung – PoC")
tab_analyse, tab_auswertung = st.tabs(["Beleg analysieren", "Auswertung"])

with tab_analyse:
    st.caption(
        "Lade einen Beleg hoch. Die KI ordnet ihn ein, extrahiert die Daten für den Bewirtungsbeleg "
        "und weist auf Probleme hin. Danach bitte unten bewerten, was richtig erkannt wurde."
    )
    uploaded_file = st.file_uploader("Beleg hochladen (JPG, PNG, PDF)", type=["jpg", "jpeg", "png", "pdf"])

    if uploaded_file is None:
        _zustand_zuruecksetzen()  # Datei entfernt -> alten Zustand verwerfen
    else:
        erg = st.session_state.get("ergebnis")
        if erg and erg["sha"] != datei_hash(uploaded_file.getvalue()):
            _zustand_zuruecksetzen()  # andere Datei -> altes Ergebnis passt nicht mehr

        if st.button("Beleg analysieren", type="primary"):
            with st.spinner("Analysiere Beleg..."):
                analyse_starten(uploaded_file)

    if "fehler" in st.session_state:
        meldung, raw = st.session_state["fehler"]
        st.error(meldung)
        if raw:
            st.code(raw)

    if "ergebnis" in st.session_state:
        zeige_ergebnis(st.session_state["ergebnis"])

with tab_auswertung:
    zeige_auswertung()

st.divider()
st.caption(f"Proof of Concept · Modell `{MODEL}` · Prompt-Version `{PROMPT_VERSION}`")

