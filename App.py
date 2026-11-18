"""Praktikant-AI (RE-ALIS) – Belege analysieren, Nachfragen, Kategorien, Auswertung."""

from datetime import datetime
from types import SimpleNamespace
import csv
import html
import io
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import streamlit as st

from auth import aktueller_benutzer, darf_kategorien_bearbeiten, login_erzwingen
from beleg_schema import FEEDBACK_FELDER, KATEGORIEN, STANDARD_KATEGORIEN, BelegAnalyse, kategorien_registrieren, anzeige, normalisieren, werte_gleich, zeitpunkt_de
from datenschutz import ist_datenschutzseite, zeige_datenschutz
from extraction import MAX_SEITEN, MODEL, MODEL_GROSS, AnalyseFehler, dateien_zu_bildern, text_anfrage_json
from pipeline import beleg_lesen
from testlauf import MAX_BELEGE as MAX_TESTBELEGE
from testlauf import ausfuehren as testlauf_ausfuehren
from testlauf import erwartete_kategorien
from testlauf import zusammenfassung as testlauf_zusammenfassung
from marke import APP_NAME, kopfzeile
from kategorien import ASSISTENT_PROMPT, aktueller_stand, assistent_nachricht, labels, prompt_abschnitt, wissens_hash
from kategorien import aktiv as kat_aktiv
from kategorien import als_dokument as kat_dokument
from kategorien import auswahl as kat_auswahl
from kategorien import schluessel_aus_name as kat_schluessel
from kategorien import sichtbar as kat_sichtbar
from kategorien import FELD_LABEL as KAT_FELD_LABEL
from kategorien import LERN_PROMPT, URSACHEN, lern_antwort_pruefen, lern_nachricht
from prompts import prompt_version, system_prompt
from storage import (
    AblageFehler,
    ablage_beschreibung,
    ablage_link,
    ablage_pruefen,
    beleg_speichern,
    datei_hash,
    feedback_laden,
    beleg_laden,
    analyse_speichern,
    analysen_laden,
    entscheidungen_laden,
    anlage_ordner,
    beispielbeleg_hochladen,
    beispielbeleg_laden,
    beispielbelege,
    kategorie_dokument_ablegen,
    kategorie_speichern,
    kalender_abgleichen,
    kategorien_laden,
    feedback_speichern,
    nachfragen_laden,
    schon_bewertet,
)

_ASSETS = Path(__file__).parent / "assets"
if not _ASSETS.exists():  # z. B. wenn App.py aus einem anderen Ordner ausgeführt wird
    _ASSETS = Path.cwd() / "assets"
st.set_page_config(page_title=f"{APP_NAME} – RE-ALIS", page_icon=str(_ASSETS / "realis-symbol.svg"),
                   layout="wide")
st.logo(str(_ASSETS / "realis-logo.svg"), icon_image=str(_ASSETS / "realis-symbol.svg"), size="large")

if ist_datenschutzseite():  # öffentlich, ohne Login (von Google für den OAuth-Produktionsmodus verlangt)
    zeige_datenschutz()
    st.stop()

login_erzwingen()

try:
    ablage_pruefen()  # Drive falsch konfiguriert -> sofort sperren statt Daten zu verlieren
except AblageFehler as e:
    st.error(f"Ablage nicht verfügbar: {e}")
    st.stop()
_link = ablage_link()
st.sidebar.caption(
    f"Ablage: [{ablage_beschreibung()} :material/open_in_new:]({_link})" if _link
    else f"Ablage: {ablage_beschreibung()}",
    help="Öffnet den Drive-Ordner mit Belegen, Nachfragen und Feedback in einem neuen Tab. "
         "Funktioniert nur für Personen, die auf den Ordner Zugriff haben." if _link else None,
)

# Kategorie-Wissen aus der App -> gültige Kategorien + Prompt (gilt für alle Benutzer gleich)
try:
    KAT_STAND = aktueller_stand(kategorien_laden())
except Exception as e:
    st.warning(f"Kategorien konnten nicht geladen werden, es gelten nur die Standard-Kategorien: {e}")
    KAT_STAND = aktueller_stand([])
kategorien_registrieren(labels(KAT_STAND))
SYSTEM_PROMPT_AKTUELL = system_prompt(prompt_abschnitt(KAT_STAND))
PROMPT_VERSION = prompt_version(wissens_hash(KAT_STAND))


def _kategorie_label(key):
    """Anzeigename einer Belegart – auch für Entwürfe und inzwischen gelöschte Kategorien."""
    return (KAT_STAND.get(key) or {}).get("name") or KATEGORIEN.get(key, key)


# ---------------------------------------------------------------------------
# Analyse ausführen und Ergebnis im Session-State halten
# ---------------------------------------------------------------------------

def _dateien(uploads) -> list[tuple[str, bytes, str]]:
    return [(u.name, u.getvalue(), u.type) for u in uploads]


def beleg_hash(dateien) -> str:
    """Eine Datei: Hash der Datei (wie bisher). Mehrere: Hash über die Einzel-Hashes in Upload-Reihenfolge."""
    if len(dateien) == 1:
        return datei_hash(dateien[0][1])
    return datei_hash("|".join(datei_hash(b) for _, b, _ in dateien).encode())


def _belege_speichern(dateien) -> list[str]:
    return [beleg_speichern(b, name, datei_hash(b)) for name, b, _ in dateien]


class DateiFehler(Exception):
    """Datei nicht lesbar (defektes PDF, unbekanntes Bildformat)."""


def _beleg_verarbeiten(dateien, status=None) -> dict:
    """Ein Beleg (eine oder mehrere Dateien) -> Ergebnis-Dict. Ohne Streamlit-Aufrufe, läuft auch parallel.
    Wirft DateiFehler bzw. AnalyseFehler/Exception bei Modellproblemen. Legt KEINE Nachfrage ab."""
    sha = beleg_hash(dateien)
    dateiname = ", ".join(name for name, _, _ in dateien)
    try:
        bilder, seiten_gesamt, seiten_gelesen = dateien_zu_bildern(dateien)
    except Exception as e:
        raise DateiFehler("Datei konnte nicht geöffnet werden – beschädigt oder kein gültiges PDF/Bild.") from e

    t0 = time.time()
    lese = beleg_lesen(bilder, dateien, status=status, system=SYSTEM_PROMPT_AKTUELL)
    gruende = list(lese.gruende)
    if status:
        status("Gleiche mit dem Firmenkalender ab …")
    kalender = kalender_abgleichen(lese.analyse)
    if kalender.nachfrage_grund:
        gruende.append(kalender.nachfrage_grund)
    if seiten_gelesen < seiten_gesamt:
        gruende.append(f"Nur {seiten_gelesen} von {seiten_gesamt} Seiten gelesen (Obergrenze {MAX_SEITEN}).")
    return {
        "sha": sha,
        "dateiname": dateiname,
        "dateien": dateien,
        "vorschau": bilder,
        "seiten_gesamt": seiten_gesamt,
        "seiten_gelesen": seiten_gelesen,
        "analyse": lese.analyse,
        "modell": lese.modell,
        "lesungen": lese.lesungen,
        "abweichungen": lese.abweichungen,
        "ungeklaert": lese.ungeklaert,
        "schiedsrichter": lese.schiedsrichter,
        "befunde": lese.befunde,
        "nachfrage_gruende": gruende,
        "raw": lese.raw,
        "usage": lese.usage,
        "dauer_s": round(time.time() - t0, 1),
        "prompt_version": PROMPT_VERSION,
        "kalender": kalender,
    }


def analyse_starten(uploads):
    """Alle hochgeladenen Dateien als EINEN Beleg analysieren."""
    with st.status("Analysiere Beleg …", expanded=False) as fortschritt:
        try:
            erg = _beleg_verarbeiten(_dateien(uploads), status=lambda text: fortschritt.update(label=text))
        except DateiFehler as e:
            st.session_state["fehler"] = (str(e), None)
        except AnalyseFehler as e:
            st.session_state["fehler"] = (str(e), e.raw)
        except Exception as e:
            st.session_state["fehler"] = (f"Fehler beim Modell-Aufruf: {e}", None)
        else:
            fortschritt.update(label="Analyse fertig", state="complete")
            st.session_state.pop("fehler", None)
            erg["ablage_status"] = _beleg_ablegen(erg)
            st.session_state["ergebnis"] = erg
            return
        fortschritt.update(label="Analyse fehlgeschlagen", state="error")


MAX_STAPEL = 30
STAPEL_PARALLEL = 3


def stapel_starten(uploads):
    """Jede Datei als EIGENEN Beleg analysieren, mehrere gleichzeitig."""
    belege = [[d] for d in _dateien(uploads)[:MAX_STAPEL]]
    ergebnisse: list[dict] = [None] * len(belege)
    balken = st.progress(0.0, text=f"Analysiere {len(belege)} Belege …")
    with ThreadPoolExecutor(max_workers=STAPEL_PARALLEL) as pool:
        futures = {pool.submit(_beleg_verarbeiten, b): i for i, b in enumerate(belege)}
        for fertig, f in enumerate(as_completed(futures), start=1):
            i = futures[f]
            try:
                ergebnisse[i] = f.result()
            except Exception as e:
                ergebnisse[i] = {"sha": beleg_hash(belege[i]), "dateiname": belege[i][0][0], "fehler": str(e)}
            balken.progress(fertig / len(belege), text=f"{fertig} von {len(belege)} Belegen fertig")
    # Ablage im Hauptthread (Drive-Zugriffe), nicht in den Arbeits-Threads
    for erg in ergebnisse:
        if "fehler" not in erg:
            erg["ablage_status"] = _beleg_ablegen(erg)
    balken.empty()
    st.session_state["stapel"] = ergebnisse


def _status_text(erg: dict) -> str:
    if "fehler" in erg:
        return "Fehler"
    return "Nachfrage" if erg["nachfrage_gruende"] else "stimmig"


def zeige_stapel(ergebnisse: list[dict]):
    n = len(ergebnisse)
    stimmig = sum(1 for e in ergebnisse if _status_text(e) == "stimmig")
    nachfrage = sum(1 for e in ergebnisse if _status_text(e) == "Nachfrage")
    fehler = n - stimmig - nachfrage
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Belege", n)
    m2.metric("Stimmig", stimmig)
    m3.metric("Zur Nachfrage", nachfrage)
    m4.metric("Fehler", fehler)
    if nachfrage:
        st.info(f"{nachfrage} Beleg(e) mit Nachfrage – im Tab „Belege“ unter „Nachfragen offen“ prüfen "
                "und korrigieren. Alle anderen Belege liegen dort ebenfalls zum Durchsehen.", icon=":material/help:")
    fehlgeschlagen = [e["dateiname"] for e in ergebnisse if (e.get("ablage_status") or "").startswith("Ablegen")]
    if fehlgeschlagen:
        st.warning("Nicht abgelegt (bitte erneut hochladen): " + ", ".join(fehlgeschlagen))

    zeilen = []
    for e in ergebnisse:
        a = e.get("analyse")
        zeilen.append({
            "Datei": e["dateiname"],
            "Status": _status_text(e),
            "Belegart": _kategorie_label(a.kategorie) if a else "–",
            "Aussteller": (a.restaurant_name or "–") if a else "–",
            "Datum": anzeige(a.datum) or "–" if a else "–",
            "Gesamtbetrag": _wert(a.brutto) if a else "–",
            "Grund": " | ".join(e.get("nachfrage_gruende") or []) or e.get("fehler") or "",
        })
    _farbtabelle(zeilen, ["Status"], ganze_zeile=True)

    puffer = io.StringIO()
    schreiber = csv.DictWriter(puffer, fieldnames=list(zeilen[0]), delimiter=";")
    schreiber.writeheader()
    schreiber.writerows(zeilen)
    st.download_button("Tabelle als CSV herunterladen", puffer.getvalue().encode("utf-8-sig"),
                       file_name="beleg-stapel.csv", mime="text/csv", icon=":material/download:")

    analysierbar = [e for e in ergebnisse if "fehler" not in e]
    if not analysierbar:
        return
    st.divider()
    gewaehlt = st.selectbox(
        "Beleg im Detail ansehen und bewerten",
        analysierbar,
        format_func=lambda e: f"{e['dateiname']} – {_status_text(e)}",
        key="stapel_detail",
    )
    zeige_ergebnis(gewaehlt)


def _lesungen_protokoll(erg) -> list[dict]:
    """Alle Lesungen (Modell, Variante, Rohantwort) – für spätere Auswertung, wer wie oft richtig lag."""
    return [{"modell": l.modell, "variante": l.variante, "rohantwort": l.raw, "fehler": l.fehler,
             "analyse": l.analyse.model_dump() if l.analyse else None} for l in erg["lesungen"]]


def _beleg_ablegen(erg) -> str:
    """Jeden analysierten Beleg ablegen (Original + Ergebnis) – damit er im Tab „Belege“ geprüft werden kann."""
    analyse = erg["analyse"]
    try:
        pfade = _belege_speichern(erg["dateien"])
        analyse_speichern({
            "datei_sha256": erg["sha"],
            "dateiname": erg["dateiname"],
            "beleg_pfad": pfade[0],
            "beleg_pfade": pfade,
            "seiten": erg["seiten_gesamt"],
            "kategorie": analyse.kategorie,
            "gruende": erg["nachfrage_gruende"],  # leer = keine Nachfrage
            "analyse": analyse.model_dump(),
            "rohantwort": erg["raw"],
            "lesungen": _lesungen_protokoll(erg),
            "schiedsrichter": erg.get("schiedsrichter", False),
            "kalender": erg["kalender"].als_dict() if erg.get("kalender") else None,
            "modell": erg["modell"],
            "prompt_version": erg["prompt_version"],
            "dauer_s": erg["dauer_s"],
            "benutzer_email": aktueller_benutzer(),
        })
    except Exception as e:
        return f"Ablegen fehlgeschlagen: {e}"
    return "abgelegt"


# ---------------------------------------------------------------------------
# Anzeige
# ---------------------------------------------------------------------------

def _wert(v) -> str:
    """Tabellenwert: Beträge mit €, leere Werte als Gedankenstrich (wird vorgelesen als 'Strich')."""
    if isinstance(v, float):
        return f"{anzeige(v)} €"
    if isinstance(v, list):  # Steuersätze
        return ", ".join(f"{anzeige([s])} %" for s in v) or "–"
    return anzeige(v) or "–"


# Status-Farben (Hintergrund, Schrift) für helles Design – Kontrast > 8:1.
# Der Status steht immer zusätzlich als Wort in der Zelle (nicht nur Farbe).
_STATUS_FARBE = {
    "ok": ("#DCFCE7", "#14532D"),
    "nachfrage": ("#FEE2E2", "#7F1D1D"),
    "fehler": ("#E5E7EB", "#1F2937"),
}


def _status_von(text) -> str | None:
    t = str(text).casefold()
    if "fehler" in t:
        return "fehler"
    if "nachfrage" in t or "korrigiert" in t:
        return "nachfrage"
    if t.endswith("ok") or "stimmig" in t:
        return "ok"
    return None


def _farbtabelle(zeilen: list[dict], status_spalten: list[str], ganze_zeile: bool = False) -> None:
    """Tabelle mit grün (ok) / rot (Nachfrage) / grau (Fehler) hinterlegten Zellen bzw. Zeilen."""
    import pandas as pd

    def css(status):
        if not status:
            return ""
        hg, schrift = _STATUS_FARBE[status]
        return f"background-color: {hg}; color: {schrift}"

    df = pd.DataFrame(zeilen)
    if ganze_zeile:
        stil = df.style.apply(lambda zeile: [css(_status_von(zeile[status_spalten[0]]))] * len(zeile), axis=1)
    else:
        stil = df.style.map(lambda wert: css(_status_von(wert)), subset=status_spalten)
    st.table(stil.hide(axis="index"))


def _tabelle(zeilen: list[dict]) -> None:
    # st.table statt st.dataframe: echte HTML-Tabelle, für Screenreader lesbar, nichts wird abgeschnitten
    st.table(zeilen)


def _pruefzeile(name: str, ok: bool) -> str:
    # Ergebnis immer als Wort, nicht nur als Symbol/Farbe
    return f"- {':material/check_circle:' if ok else ':material/cancel:'} {name}: **{'ja' if ok else 'nein'}**"


_VORSCHAU_SEITEN = 3  # mehr Seiten nur in der großen Ansicht


@st.dialog("Beleg in voller Größe", width="large")
def _beleg_gross(bilder: list[bytes], titel: str) -> None:
    st.caption(f"{titel} · Klick aufs Bild schließt die Ansicht")
    with st.container(key="grossbild"):
        for i, bild in enumerate(bilder, start=1):
            st.image(bild, width="stretch", caption=f"Seite {i} von {len(bilder)}")
        # unsichtbarer Button über den Bildern (CSS): Klick schließt das Fenster (Neuladen ohne Dialog)
        if st.button("Große Ansicht schließen", key="bildklick_schliessen"):
            st.rerun()


def _beleg_vorschau(bilder: list[bytes], titel: str, key: str) -> None:
    """Kompakte Vorschau (Höhe begrenzt, mehrere Seiten nebeneinander) + Button für die große Ansicht."""
    if not bilder:
        return
    n = len(bilder)
    with st.container(key=f"belegbild_{key}"):
        spalten = st.columns(min(n, _VORSCHAU_SEITEN)) if n > 1 else [st]
        for spalte, bild in zip(spalten, bilder):
            spalte.image(bild)
        # unsichtbarer Button über dem ganzen Bild (CSS): Klick aufs Bild öffnet die große Ansicht
        bild_klick = st.button("Beleg vergrößern", key=f"bildklick_{key}")
    if n > _VORSCHAU_SEITEN:
        st.caption(f"{n} Seiten – {n - _VORSCHAU_SEITEN} weitere in der großen Ansicht")
    elif n > 1:
        st.caption(f"{n} Seiten")
    knopf = st.button("Größer ansehen", icon=":material/zoom_in:", key=f"gross_{key}")
    if bild_klick or knopf:
        _beleg_gross(bilder, titel)


def _zeige_kalender(k: dict) -> None:
    """Ergebnis des Kalenderabgleichs (auch für gespeicherte Nachfragen, daher als dict)."""
    if not k or k["status"] == "nicht_noetig":
        return
    if k["status"] == "nicht_eingerichtet":
        st.caption(":material/event: Kalenderabgleich ist noch nicht eingerichtet.")
        return
    t = k.get("termin")
    if k["status"] == "passend" and t:
        zeilen = [f"**Kalendertermin:** {t['titel']} · {t['zeitraum']}" + (f" · {t['ort']}" if t["ort"] else ""),
                  k["grund"]]
        if t["teilnehmer"]:
            zeilen.append("**Teilnehmer laut Kalender** (Vorschlag für „bewirtete Personen“): "
                          + ", ".join(t["teilnehmer"]))
        if t.get("link"):
            zeilen.append(f"[Termin im Kalender öffnen]({t['link']})")
        st.info("\n\n".join(zeilen), icon=":material/event_available:")
        return
    st.warning(f"**Kein passender Kalendertermin:** {k['grund']}", icon=":material/event_busy:")
    if k.get("kandidaten"):
        with st.expander(f"Termine im Zeitraum ({len(k['kandidaten'])}) – vielleicht ist einer davon gemeint"):
            for kand in k["kandidaten"]:
                st.markdown(f"- {kand['titel']} · {kand['zeitraum']}" + (f" · {kand['ort']}" if kand["ort"] else "")
                            + (f" · Teilnehmer: {', '.join(kand['teilnehmer'])}" if kand["teilnehmer"] else ""))


def zeige_ergebnis(erg):
    analyse = erg["analyse"]
    gruende = erg.get("nachfrage_gruende") or []
    status = erg.get("ablage_status") or ""

    # 1. Gesamtergebnis zuerst – das Wichtigste oben
    if gruende:
        st.error("**Nachfrage nötig.** Der Beleg liegt im Tab „Belege“ unter „Nachfragen offen“ – dort "
                 "prüfen und korrigieren (oder gleich unten).\n\n" + "\n".join(f"- {g}" for g in gruende),
                 icon=":material/help:")
    else:
        st.success("**Alles stimmig.** Alle Prüfungen bestanden, keine Nachfrage nötig.",
                   icon=":material/check_circle:")
    if status.startswith("Ablegen fehlgeschlagen"):
        st.warning(f"{status} – der Beleg erscheint deshalb nicht im Tab „Belege“.")

    col_bild, col_daten = st.columns([2, 3], gap="large")

    # 2. Beleg
    with col_bild:
        st.subheader("Beleg")
        gesamt, gelesen = erg["seiten_gesamt"], erg["seiten_gelesen"]
        if gelesen < gesamt:
            st.warning(f"{gesamt} Seiten – nur die ersten {gelesen} wurden gelesen (Obergrenze {MAX_SEITEN}).")
        else:
            st.caption(f"{gesamt} Seite(n) aus {len(erg['dateien'])} Datei(en): {erg['dateiname']}")
        _beleg_vorschau(erg["vorschau"], erg["dateiname"], f"analyse_{erg['sha']}")

    # 3. Ergebnis
    with col_daten:
        st.subheader("Ergebnis")
        st.markdown(f"**Belegart:** {_kategorie_label(analyse.kategorie)}")
        if analyse.kategorie_unsicher:
            st.markdown(f"**Unsicher, weil:** {analyse.kategorie_grund or 'keine Begründung'}")

        st.markdown("**Prüfungen**")
        st.markdown("\n".join([
            _pruefzeile("Lesbar", analyse.lesbar),
            _pruefzeile("Steuersatz ausgewiesen", analyse.steuersatz_erkennbar or bool(analyse.steuerzeilen)),
            _pruefzeile("Datum erkennbar", analyse.datum_erkennbar or bool(analyse.datum)),
            _pruefzeile("Rechnung geht rechnerisch auf", not erg.get("befunde")),
            _pruefzeile("Zweimal gleich gelesen", not erg.get("abweichungen")),
        ] + ([_pruefzeile("EC-Beleg passt zur Rechnung", analyse.ec_passt_zur_rechnung)]
             if analyse.ec_passt_zur_rechnung is not None else [])
          + ([_pruefzeile("Passender Kalendertermin", erg["kalender"].status == "passend")]
             if erg.get("kalender") and erg["kalender"].status in ("passend", "kein_termin", "fehler") else [])))
        if erg.get("kalender"):
            _zeige_kalender(erg["kalender"].als_dict())
        if erg.get("schiedsrichter"):
            geklaert = len(erg.get("abweichungen", {})) - len(erg.get("ungeklaert", []))
            st.caption(
                f"Wegen Zweifeln hat zusätzlich das große Modell (`{erg['modell']}`) in höherer Auflösung "
                f"gelesen; dessen Ergebnis wird angezeigt. Abweichungen geklärt: {geklaert} von "
                f"{len(erg.get('abweichungen', {}))}."
            )

        if analyse.korrekturen:
            st.info("**Vom Code umgedeutet** (rechnerisch eindeutig, Summe geht danach exakt auf)\n\n"
                    + "\n".join(f"- {k}" for k in analyse.korrekturen), icon=":material/calculate:")

        if analyse.hinweise:
            st.info("**Hinweise der KI**\n\n" + "\n".join(f"- {h}" for h in analyse.hinweise),
                    icon=":material/info:")

        st.subheader("Daten für den Bewirtungsbeleg" if analyse.ist_bewirtung else "Belegdaten")
        _tabelle([{"Feld": k, "Wert": _wert(v)} for k, v in analyse.bewirtungsdaten().items()])

        if analyse.steuerzeilen:
            st.markdown("**MwSt.-Aufstellung** (wie auf dem Beleg abgelesen)")
            _tabelle([{
                "Steuersatz": f"{anzeige(z.satz)} %" if z.satz is not None else "–",
                "Netto": _wert(z.netto), "MwSt.": _wert(z.mwst), "Brutto": _wert(z.brutto),
            } for z in analyse.steuerzeilen])

        with st.expander("Technische Details"):
            dauer = f"{erg['dauer_s']:.1f}".replace(".", ",")
            st.caption(f"Ergebnis von `{erg['modell']}` · Prompt-Version `{erg['prompt_version']}` · Dauer {dauer} s")
            if erg.get("abweichungen"):
                _tabelle([{"Feld": f, "Lesung 1": x or "–", "Lesung 2": y or "–"}
                          for f, (x, y) in erg["abweichungen"].items()])
            for nr, l in enumerate(erg["lesungen"], start=1):
                st.markdown(f"**Lesung {nr}:** `{l.modell}`, Bild {l.variante}")
                st.code(l.raw or l.fehler or "", language="json")
            st.json(erg["usage"])

    st.divider()
    zeige_feedback(erg)


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def _felder_eingabe(analyse, praefix: str) -> dict[str, str]:
    """Eingabefelder für alle FEEDBACK_FELDER, vorbelegt mit dem KI-Ergebnis (nur innerhalb von st.form)."""
    korrekturen = {}
    # Zeilenweise (je 2 Felder), damit die Tab-Reihenfolge der Lesereihenfolge entspricht
    for i in range(0, len(FEEDBACK_FELDER), 2):
        for spalte, (key, label, _typ) in zip(st.columns(2), FEEDBACK_FELDER[i:i + 2]):
            korrekturen[key] = spalte.text_input(
                label, value=anzeige(getattr(analyse, key)), key=f"{praefix}_{key}"
            )
    return korrekturen


def _felder_auswerten(analyse, korrekturen: dict[str, str]):
    """-> (felder, ungueltig). felder[key] = {modell, korrekt, richtig}; ungueltig = Labels nicht lesbarer Eingaben."""
    ungueltig, felder = [], {}
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
    return felder, ungueltig


def _belegart_frage(ki_kat: str, key: str, richtig: bool | None = None, korrekt: str | None = None) -> str | None:
    """Ausdrückliche Frage „Stimmt die Belegart?“ – nichts ist vorausgewählt, damit niemand die KI-Antwort
    versehentlich bestätigt. -> richtige Belegart oder None (noch nicht beantwortet). Außerhalb von st.form."""
    namen = kat_auswahl(KAT_STAND)  # auch Entwürfe: neu angelegte Kategorien sofort wählbar
    antwort = st.radio(
        f"Stimmt die Belegart „{_kategorie_label(ki_kat)}“?",
        ["ja", "nein"],
        format_func={"ja": "Ja, stimmt", "nein": "Nein, es ist eine andere Belegart"}.get,
        index=None if richtig is None else (0 if richtig else 1),
        horizontal=True,
        key=f"{key}_kat_ok",
    )
    if antwort == "ja":
        return ki_kat
    if antwort == "nein":
        andere = [k for k in namen if k != ki_kat]
        return st.selectbox("Richtige Belegart", andere, format_func=namen.get, key=f"{key}_kat_wahl",
                            index=andere.index(korrekt) if korrekt in andere else None,
                            placeholder="Bitte wählen …")
    return None


def zeige_feedback(erg):
    analyse = erg["analyse"]
    st.subheader("Feedback: Was hat die KI richtig erkannt?")
    st.caption("Hilft uns zu messen, wie gut die Erkennung ist. Bitte nur falsche Werte korrigieren.")

    if erg["sha"] in st.session_state.get("gespeichert_shas", set()):
        st.success("Feedback gespeichert – danke! Du kannst den nächsten Beleg hochladen.")
        return
    try:
        bereits_bewertet = schon_bewertet(erg["sha"])
    except Exception:  # Ablage kurz nicht erreichbar -> Hinweis weglassen, Speichern meldet den Fehler
        bereits_bewertet = False
    if bereits_bewertet:
        st.info("Dieser Beleg wurde schon einmal bewertet. Ein neues Feedback wird zusätzlich gespeichert.")

    st.caption(
        "Korrigiere nur die Werte, die falsch sind. Leeres Feld = steht nicht auf dem Beleg. "
        "Beträge wie 1.234,50 – Datum als TT.MM.JJJJ."
    )

    kat_korrekt = _belegart_frage(analyse.kategorie, f"fb_{erg['sha']}")
    with st.form(key=f"feedback_{erg['sha']}"):
        korrekturen = _felder_eingabe(analyse, f"fb_{erg['sha']}")

        hinweise_ok = st.radio(
            "Waren die Hinweise der KI zutreffend?",
            ["Ja", "Teilweise", "Nein", "Keine Hinweise ausgegeben"],
            horizontal=True,
            index=None,
        )
        kommentar = st.text_area(
            "Kommentar (optional)", placeholder="z. B. Trinkgeld stand handschriftlich auf dem Beleg"
        )
        bearbeiter = st.text_input(
            "Dein Kürzel", value=st.session_state.get("bearbeiter") or aktueller_benutzer() or ""
        )
        abgeschickt = st.form_submit_button("Feedback speichern", type="primary")

    if not abgeschickt:
        return
    if kat_korrekt is None:
        st.error("Bitte oben beantworten, ob die Belegart stimmt (und ggf. die richtige wählen).")
        return

    felder, ungueltig = _felder_auswerten(analyse, korrekturen)
    if ungueltig:
        st.error("Bitte prüfe diese Eingaben, sie konnten nicht gelesen werden: " + ", ".join(ungueltig))
        return

    st.session_state["bearbeiter"] = bearbeiter
    try:
        pfade = _belege_speichern(erg["dateien"])
        _feedback_ablegen(erg, analyse, kat_korrekt, felder, hinweise_ok, kommentar, bearbeiter, pfade)
    except Exception as e:  # z. B. Drive nicht erreichbar, Ordner nicht beschreibbar
        st.error(f"Speichern fehlgeschlagen – bitte erneut versuchen: {e}")
        return
    st.session_state.setdefault("gespeichert_shas", set()).add(erg["sha"])
    st.rerun()


def _feedback_ablegen(erg, analyse, kat_korrekt, felder, hinweise_ok, kommentar, bearbeiter, pfade):
    feedback_speichern({
        "datei_sha256": erg["sha"],
        "dateiname": erg["dateiname"],
        "beleg_pfad": pfade[0],
        "beleg_pfade": pfade,
        "seiten": erg["seiten_gesamt"],
        "modell": erg["modell"],
        "schiedsrichter": erg.get("schiedsrichter", False),
        "abweichungen": {f: list(w) for f, w in erg.get("abweichungen", {}).items()},
        "lesungen": _lesungen_protokoll(erg),
        "kalender": erg["kalender"].als_dict() if erg.get("kalender") else None,
        "prompt_version": erg["prompt_version"],
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
        "benutzer_email": aktueller_benutzer(),
        "befunde_code": erg.get("befunde", []),
        "nachfrage_gruende": erg.get("nachfrage_gruende", []),
    })



# ---------------------------------------------------------------------------
# Auswertung
# ---------------------------------------------------------------------------

def _juengste_bewertungen(eintraege: list[dict]) -> list[dict]:
    """Pro Beleg (Datei-Hash) nur die jüngste Bewertung – sonst zählt ein mehrfach bewerteter Beleg doppelt."""
    juengste = {}
    for e in sorted(eintraege, key=lambda e: e.get("zeitpunkt") or ""):
        juengste[e.get("datei_sha256") or id(e)] = e
    return list(juengste.values())


def _feld_fehler(e: dict) -> list[str]:
    return [key for key, f in (e.get("felder") or {}).items() if not f.get("richtig")]


def _fehlerfrei(e: dict) -> bool:
    return e["kategorie"]["richtig"] and not _feld_fehler(e)


def zeige_auswertung():
    st.caption(
        "Wie gut erkennt die KI eure Belege? Gezählt werden alle Belege, die ein Mensch geprüft hat – über das "
        "Feedback nach einer Analyse oder im Tab „Belege“. Jeder Beleg zählt einmal (mit seiner jüngsten "
        "Prüfung). Einzelne Belege ansehen und korrigieren: Tab „Belege“."
    )
    try:
        alle = feedback_laden()
        belege = _alle_belege()
    except Exception as e:
        st.error(f"Daten konnten nicht geladen werden: {e}")
        return
    if belege:
        st.markdown("**Belege und Nachfragen**")
        nf = [b for b in belege if b["gruende"]]
        nf_geprueft = [b for b in nf if b.get("bewertung")]
        unnoetig = sum(1 for b in nf_geprueft if _fehlerfrei(b["bewertung"]))
        geprueft = sum(1 for b in belege if b.get("bewertung"))
        offen = sum(1 for b in nf if b["status"] == "offen")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Belege", len(belege), help="Alle analysierten Belege (je Beleg einmal).")
        c2.metric("Geprüft", f"{geprueft} von {len(belege)}", f"{geprueft / len(belege):.0%}", delta_color="off",
                  delta_arrow="off", help="Von einem Menschen geprüft (Feedback oder Tab „Belege“).")
        c3.metric("Nachfragen der KI", len(nf), f"{offen} offen" if offen else "alle erledigt", delta_color="off",
                  delta_arrow="off", help="Belege, bei denen die KI unsicher war oder eine Prüfung nicht aufging.")
        c4.metric("Unnötige Nachfragen", f"{unnoetig} von {len(nf_geprueft)}" if nf_geprueft else "–",
                  help="Die KI hat nachgefragt, bei der Prüfung war aber alles richtig. Viele unnötige Nachfragen "
                       "= Regeln zu streng.")
        st.divider()
    if not alle:
        st.info("Noch keine geprüften Belege. Prüfe Belege nach dem Hochladen oder im Tab „Belege“.")
        return
    st.markdown("**Erkennungsqualität**")

    with st.expander("Filter: Prompt-Version und Modell"):
        versionen = sorted({e.get("prompt_version") or "?" for e in alle}, reverse=True)
        modelle = sorted({e.get("modell") or "?" for e in alle})
        f1, f2 = st.columns(2)
        v_auswahl = f1.multiselect("Prompt-Version", versionen, default=versionen,
                                   help="Jede Änderung an Anweisungen oder Kategorien erzeugt eine neue Version.")
        m_auswahl = f2.multiselect("Modell", modelle, default=modelle)
    eintraege = _juengste_bewertungen([
        e for e in alle
        if (e.get("prompt_version") or "?") in v_auswahl and (e.get("modell") or "?") in m_auswahl
    ])
    if not eintraege:
        st.warning("Keine Einträge für diese Auswahl.")
        return

    n = len(eintraege)
    kat_richtig = sum(1 for e in eintraege if e["kategorie"]["richtig"])
    felder_richtig = sum(1 for e in eintraege if not _feld_fehler(e))
    komplett = sum(1 for e in eintraege if _fehlerfrei(e))
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Geprüfte Belege", n)
    m2.metric("Belegart richtig", f"{kat_richtig} von {n}", f"{kat_richtig / n:.0%}", delta_color="off", delta_arrow="off",
              help="Die KI hat die Belegart (Kategorie) gewählt, die auch der Mensch als richtig bestätigt hat.")
    m3.metric("Alle Felder richtig", f"{felder_richtig} von {n}", f"{felder_richtig / n:.0%}", delta_color="off", delta_arrow="off",
              help="Kein einziges Feld (Belegnummer, Datum, Beträge, …) musste korrigiert werden. "
                   "Ein Feld gilt als richtig, wenn es beim Bewerten nicht geändert wurde.")
    m4.metric("Komplett richtig", f"{komplett} von {n}", f"{komplett / n:.0%}", delta_color="off", delta_arrow="off",
              help="Belegart richtig UND alle Felder richtig.")

    # Falsche Belegart, weil die richtige Kategorie neu ist? Dann konnte die KI sie evtl. noch nicht kennen.
    neue_kat = [e for e in eintraege
                if not e["kategorie"]["richtig"] and e["kategorie"]["korrekt"] not in STANDARD_KATEGORIEN]
    if neue_kat:
        namen = sorted({_kategorie_label(e["kategorie"]["korrekt"]) for e in neue_kat})
        st.info(
            f"{len(neue_kat)} der {n - kat_richtig} falschen Belegarten betreffen eigene, neu angelegte Kategorien "
            f"({', '.join(f'„{x}“' for x in namen)}). Wurden die Belege analysiert, bevor die Kategorie aktiv war, "
            "konnte die KI sie noch nicht kennen – dann ist das kein echter Fehler. Am besten diese Belege nach dem "
            "Aktivieren noch einmal analysieren.",
            icon=":material/info:",
        )

    links, rechts = st.columns(2, gap="large")
    with links:
        st.markdown("**Trefferquote pro Feld**")
        zeilen = []
        for key, label, _typ in FEEDBACK_FELDER:
            werte = [e["felder"][key]["richtig"] for e in eintraege if key in (e.get("felder") or {})]
            if werte:
                zeilen.append({"Feld": label, "Trefferquote": sum(werte) / len(werte),
                               "Richtig": f"{sum(werte)} von {len(werte)}"})
        st.dataframe(zeilen, hide_index=True, width="stretch", column_config={
            "Trefferquote": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)})
    with rechts:
        st.markdown("**Trefferquote pro Belegart** (laut Bewertung)")
        pro_art = {}
        for e in eintraege:
            z = pro_art.setdefault(e["kategorie"]["korrekt"], [0, 0])
            z[0] += e["kategorie"]["richtig"]
            z[1] += 1
        st.dataframe([{"Belegart": _kategorie_label(k), "Trefferquote": r / g, "Richtig": f"{r} von {g}"}
                      for k, (r, g) in sorted(pro_art.items(), key=lambda x: -x[1][1])],
                     hide_index=True, width="stretch", column_config={
                         "Belegart": st.column_config.TextColumn(width="medium"),
                         "Trefferquote": st.column_config.ProgressColumn(format="percent", min_value=0,
                                                                         max_value=1, width="small"),
                         "Richtig": st.column_config.TextColumn(width="small")})
        verwechslungen = [e for e in eintraege if not e["kategorie"]["richtig"]]
        if verwechslungen:
            st.caption("Verwechslungen: " + "; ".join(
                f"„{_kategorie_label(e['kategorie']['modell'])}“ statt „{_kategorie_label(e['kategorie']['korrekt'])}“"
                for e in verwechslungen))

    st.divider()
    _aus_korrekturen_lernen(eintraege)


# ---------------------------------------------------------------------------
# Aus Korrekturen lernen: Vorschläge für das Kategorie-Wissen
# ---------------------------------------------------------------------------

_MAX_LERNFAELLE = 60


def _lernfaelle(eintraege: list[dict]) -> tuple[list[dict], list[dict]]:
    """Bewertungen, aus denen sich etwas lernen lässt: korrigiert, kommentiert oder nachgefragt.
    -> (nummerierte Fälle fürs Modell, zugehörige Einträge)."""
    quellen = [e for e in eintraege
               if not _fehlerfrei(e) or (e.get("kommentar") or "").strip() or e.get("nachfrage_gruende")]
    quellen = sorted(quellen, key=lambda e: e.get("zeitpunkt") or "", reverse=True)[:_MAX_LERNFAELLE]
    faelle = []
    for nr, e in enumerate(quellen, start=1):
        k, felder = e["kategorie"], e.get("felder") or {}
        faelle.append({
            "nr": nr,
            "belegart_ki": f'{k["modell"]} ({_kategorie_label(k["modell"])})',
            "belegart_richtig": f'{k["korrekt"]} ({_kategorie_label(k["korrekt"])})',
            "belegart_vom_menschen_bestaetigt": bool(k["richtig"]),
            "beleg_laut_ki": {  # Eckdaten, damit das Modell nicht raten muss, worum es ging
                "aussteller": (e.get("analyse") or {}).get("restaurant_name"),
                "ausland": (e.get("analyse") or {}).get("ausland"),
                "begruendung_der_belegart": (e.get("analyse") or {}).get("kategorie_grund"),
                "hinweise": ((e.get("analyse") or {}).get("hinweise") or [])[:5],
            },
            "geaenderte_felder": [{"feld": label, "ki": anzeige(felder[key]["modell"]) or "–",
                                   "richtig": anzeige(felder[key]["korrekt"]) or "–"}
                                  for key, label, _t in FEEDBACK_FELDER
                                  if key in felder and not felder[key]["richtig"]],
            "nachfragegruende_ki": e.get("nachfrage_gruende") or [],
            "kommentar_mensch": (e.get("kommentar") or "").strip(),
            "quelle": _QUELLE.get(e.get("quelle"), "Feedback nach der Analyse"),
        })
    return faelle, quellen


def _aus_korrekturen_lernen(eintraege: list[dict]) -> None:
    st.subheader("Aus Korrekturen lernen")
    st.caption(
        "Ein großes KI-Modell liest alle Korrekturen, die Nachfragegründe der KI und eure Kommentare, sucht "
        "Muster und schlägt Ergänzungen für die Kategorie-Beschreibungen vor. Automatisch übernommen wird "
        "nichts: Ein Vorschlag landet als ungespeicherte Änderung im Tab „Kategorien“ – dort prüfen, mit "
        "„Testen“ ausprobieren und erst dann speichern."
    )
    faelle, quellen = _lernfaelle(eintraege)
    if not faelle:
        st.info("Noch keine Korrekturen, Kommentare oder Nachfragen – es gibt noch nichts zu lernen.")
        return
    if len(faelle) < 20:
        st.caption(f":material/info: Erst {len(faelle)} Fälle. Mit wenigen Fällen sind Vorschläge oft "
                   "Einzelfälle – richtig aussagekräftig wird es ab etwa 20–30 Korrekturen.")
    if st.button(f"Verbesserungsvorschläge erstellen ({len(faelle)} {'Fall' if len(faelle) == 1 else 'Fälle'})", icon=":material/auto_awesome:",
                 help=f"Modell: {MODEL_GROSS}. Ein Aufruf, dauert etwa eine halbe bis eine Minute."):
        # auch Entwürfe zeigen: Fehler betreffen oft gerade neu angelegte Kategorien
        stand = {key: {**k, "status": "aktiv"} for key, k in kat_sichtbar(KAT_STAND).items()}
        try:
            with st.spinner("KI sucht Muster in den Korrekturen …"):
                antwort = text_anfrage_json(LERN_PROMPT, lern_nachricht(stand, faelle))
        except Exception as ex:
            st.error(f"Vorschläge konnten nicht erstellt werden: {ex}")
            return
        st.session_state["lern_ergebnis"] = {
            **lern_antwort_pruefen(antwort, KAT_STAND, len(faelle)),
            "dateien": [e.get("dateiname") or "?" for e in quellen],
            "erstellt": datetime.now().isoformat(timespec="seconds"),
            "anzahl": len(faelle),
        }
        st.session_state["lern_uebernommen"] = set()
    erg = st.session_state.get("lern_ergebnis")
    if erg:
        _lern_ergebnis_zeigen(erg)


def _lern_ergebnis_zeigen(erg: dict) -> None:
    dateien = erg["dateien"]

    def datei(nr):
        return dateien[nr - 1] if 1 <= nr <= len(dateien) else f"Fall {nr}"

    st.caption(f"Erstellt am {zeitpunkt_de(erg['erstellt'])} aus {erg['anzahl']} Fällen.")
    zaehler = {u: sum(1 for f in erg["faelle"] if f["ursache"] == u) for u in URSACHEN}
    sichtbar = [(u, n) for u, n in zaehler.items() if n]
    if sichtbar:
        for spalte, (u, n) in zip(st.columns(len(sichtbar)), sichtbar):
            spalte.metric(URSACHEN[u], n)

    widerspruch = [f for f in erg["faelle"] if f["ursache"] == "bewertung_widerspruechlich"]
    if widerspruch:
        st.warning(
            "**Bewertung widerspricht dem Kommentar** – bitte im Tab „Belege“ (Ansicht „Mit Kommentar“) öffnen "
            "und über „Korrigieren“ richtigstellen:\n\n"
            + "\n".join(f"- {datei(f['nr'])}: {f['kurz']}" for f in widerspruch),
            icon=":material/rule:",
        )

    vorschlaege = erg["vorschlaege"]
    if not vorschlaege:
        st.info("Keine Vorschläge für die Kategorie-Beschreibungen – die Fälle zeigen (noch) kein Muster, "
                "das sich mit einer Regel beheben lässt.")
    darf = darf_kategorien_bearbeiten()
    uebernommen = st.session_state.setdefault("lern_uebernommen", set())
    sicherheit = {"hoch": ("Sicherheit hoch", "green"), "mittel": ("Sicherheit mittel", "orange"),
                  "niedrig": ("Einzelfall", "gray")}
    for i, v in enumerate(vorschlaege):
        with st.container(border=True):
            text, farbe = sicherheit[v["sicherheit"]]
            st.markdown(f"**{_kategorie_label(v['kategorie'])}** · {KAT_FELD_LABEL[v['feld']]}  "
                        f":{farbe}-badge[{text}]")
            st.markdown(f"> {v['text']}")
            st.caption(v["begruendung"] + (" · Fälle: " + ", ".join(datei(n) for n in v["faelle"])
                                           if v["faelle"] else ""))
            if i in uebernommen:
                st.caption(":material/check: Im Tab „Kategorien“ vorbereitet – dort prüfen, testen, speichern.")
            elif darf and st.button("In Kategorie übernehmen", icon=":material/playlist_add:", key=f"lern_ueb_{i}"):
                st.session_state.setdefault("kategorie_vorschlaege_naechste", []).append(
                    {"key": v["kategorie"], "feld": v["feld"], "text": v["text"]})
                uebernommen.add(i)
                st.toast("Vorschlag im Tab „Kategorien“ vorbereitet – dort prüfen, testen und speichern.",
                         icon=":material/playlist_add:")
                st.rerun()
    if erg["rueckfragen"]:
        st.warning("**Rückfragen an die Buchhaltung**\n\n" + "\n".join(f"- {r}" for r in erg["rueckfragen"]),
                   icon=":material/help:")
    with st.expander("Alle Fälle mit Einordnung"):
        st.dataframe([{"Nr": f["nr"], "Datei": datei(f["nr"]), "Ursache": URSACHEN[f["ursache"]],
                       "Was schiefging": f["kurz"]} for f in erg["faelle"]], hide_index=True, width="stretch")


# ---------------------------------------------------------------------------
# Belege: alle analysierten Belege durchsehen, prüfen und korrigieren – an einer Stelle
# ---------------------------------------------------------------------------

_MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
_BELEG_STATUS = {
    "offen": "Nachfrage offen",
    "ungeprueft": "Noch nicht geprüft",
    "fehlerfrei": "Geprüft – alles richtig",
    "korrigiert": "Geprüft – korrigiert",
    "erledigt_alt": "Erledigt (ohne Bewertung)",
}


def _letzte_entscheidungen(entscheidungen: list[dict]) -> dict[str, dict]:
    """Datei-Hash -> jüngste Entscheidung (erste Version des Nachfragen-Tabs, nur noch lesend)."""
    letzte = {}
    for e in sorted(entscheidungen, key=lambda e: e.get("zeitpunkt") or ""):
        letzte[e.get("datei_sha256")] = e
    return letzte


@st.cache_data(show_spinner="Lade Beleg …", max_entries=30)
def _vorschau(pfad: str) -> list[bytes]:
    endung = "." + pfad.rsplit(".", 1)[-1].lower()
    daten = beleg_laden(pfad)
    return dateien_zu_bildern([(pfad, daten, _MIME.get(endung, "image/jpeg"))])[0]


def _belegbilder(eintrag: dict, bereich: str) -> None:
    pfade = eintrag.get("beleg_pfade") or ([eintrag["beleg_pfad"]] if eintrag.get("beleg_pfad") else [])
    if not pfade:
        st.caption("Zu diesem Eintrag ist kein Original gespeichert.")
    bilder = []
    for pfad in pfade:
        try:
            bilder += _vorschau(pfad)
        except Exception as e:
            st.warning(f"{pfad} konnte nicht geladen werden: {e}")
    _beleg_vorschau(bilder, eintrag.get("dateiname") or ", ".join(pfade),
                    f"{bereich}_{eintrag.get('datei_sha256') or 'ohne_hash'}")


def _alle_belege() -> list[dict]:
    """Analysen (neu), Nachfragen (alt), Bewertungen und alte Entscheidungen pro Datei-Hash zusammenführen.

    Ein Beleg: {sha, analyse_eintrag (jüngste Analyse/Nachfrage), bewertung (jüngste), entscheidung (alt),
    gruende (Nachfragegründe der KI), status, zeitpunkt}."""
    belege: dict[str, dict] = {}
    for quelle in sorted(nachfragen_laden() + analysen_laden(), key=lambda e: e.get("zeitpunkt") or ""):
        if quelle.get("datei_sha256"):
            belege.setdefault(quelle["datei_sha256"], {})["analyse_eintrag"] = quelle  # jüngste gewinnt
    for f in _juengste_bewertungen(feedback_laden()):
        if f.get("datei_sha256"):
            belege.setdefault(f["datei_sha256"], {})["bewertung"] = f
    for sha, ent in _letzte_entscheidungen(entscheidungen_laden()).items():
        if sha in belege:
            belege[sha]["entscheidung"] = ent

    for sha, b in belege.items():
        a, f, ent = b.get("analyse_eintrag"), b.get("bewertung"), b.get("entscheidung")
        b["sha"] = sha
        b["gruende"] = (a.get("gruende") if a else None) or (f.get("nachfrage_gruende") if f else None) or []
        if f:
            b["status"] = "fehlerfrei" if _fehlerfrei(f) else "korrigiert"
        elif ent and ent.get("status", "offen") != "offen":
            b["status"] = "erledigt_alt"
        else:
            b["status"] = "offen" if b["gruende"] else "ungeprueft"
        basis = f or a
        b["dateiname"] = basis.get("dateiname")
        b["zeitpunkt"] = max((x.get("zeitpunkt") or "") for x in (a, f) if x)
        b["ki_kategorie"] = (f["kategorie"]["modell"] if f else
                             (a.get("analyse") or {}).get("kategorie") or a.get("kategorie") or "sonstiger_beleg")
    return sorted(belege.values(), key=lambda b: b["zeitpunkt"], reverse=True)


def _offene_nachfragen() -> int | None:
    try:
        return sum(1 for b in _alle_belege() if b["status"] == "offen")
    except Exception:
        return None


def _vergleichstabelle(kategorie: dict, felder: dict) -> None:
    """KI-Lesung neben dem richtigen Wert – grün = stimmte, rot = korrigiert."""
    if kategorie["richtig"]:
        st.success(f"**Belegart richtig:** {_kategorie_label(kategorie['korrekt'])}", icon=":material/check_circle:")
    else:
        st.error(f"**Belegart korrigiert:** KI sagte „{_kategorie_label(kategorie['modell'])}“, richtig ist "
                 f"„{_kategorie_label(kategorie['korrekt'])}“", icon=":material/cancel:")
    _farbtabelle([{
        "Feld": label,
        "KI hat gelesen": anzeige(felder[key]["modell"]) or "–",
        "Richtig ist": anzeige(felder[key]["korrekt"]) or "–",
        "Status": "ok" if felder[key]["richtig"] else "korrigiert",
    } for key, label, _typ in FEEDBACK_FELDER if key in felder], ["Status"], ganze_zeile=True)


_QUELLE = {"nachfrage": "Korrektur einer Nachfrage", "pruefung": "Prüfung im Tab „Belege“"}


def zeige_belege():
    ss = st.session_state
    st.caption(
        "Alle analysierten Belege an einer Stelle: durchsehen, prüfen und – falls die KI etwas falsch gelesen "
        "hat – korrigieren. Das geht auch bei Belegen, die schon als richtig abgehakt sind. Jede Prüfung wird "
        "gespeichert und fließt in die Auswertung und in die Verbesserungsvorschläge ein."
    )
    zuletzt = ss.pop("beleg_zuletzt", None)
    if zuletzt:  # Ergebnis der gerade gespeicherten Prüfung; darunter geht es mit dem nächsten Beleg weiter
        with st.container(border=True):
            st.success(f"„{zuletzt['dateiname']}“ gespeichert. So sieht die Prüfung aus:",
                       icon=":material/check_circle:")
            _vergleichstabelle(zuletzt["kategorie"], zuletzt["felder"])

    try:
        belege = _alle_belege()
    except Exception as e:
        st.error(f"Belege konnten nicht geladen werden: {e}")
        return
    if not belege:
        st.info("Noch keine Belege. Lade im Tab „Belege hochladen“ Belege hoch.")
        return

    def mit_kommentar(b):
        return bool(((b.get("bewertung") or {}).get("kommentar") or "").strip())

    filter_ = {
        "offen": ("Nachfragen offen", lambda b: b["status"] == "offen"),
        "nachfrage": ("Alle Nachfragen", lambda b: bool(b["gruende"])),
        "ungeprueft": ("Ungeprüft", lambda b: b["status"] in ("offen", "ungeprueft")),
        "korrigiert": ("Korrigiert", lambda b: b["status"] == "korrigiert"),
        "kommentar": ("Mit Kommentar", mit_kommentar),
        "alle": ("Alle", lambda b: True),
    }
    anzahl = {k: sum(1 for b in belege if f(b)) for k, (_l, f) in filter_.items()}
    if ss.get("belege_filter") is None:  # Start: dort, wo es etwas zu tun gibt
        ss["belege_filter"] = next((k for k in ("offen", "ungeprueft") if anzahl[k]), "alle")
    wahl = st.segmented_control("Anzeigen", list(filter_), format_func=lambda k: f"{filter_[k][0]} ({anzahl[k]})",
                                key="belege_filter", label_visibility="collapsed") or "alle"
    liste = [b for b in belege if filter_[wahl][1](b)]
    if not liste:
        st.info("Alles erledigt – in dieser Ansicht gibt es keine Belege." if wahl in ("offen", "ungeprueft")
                else "In dieser Ansicht gibt es keine Belege.")
        return

    shas = [b["sha"] for b in liste]
    nach_sha = {b["sha"]: b for b in liste}
    naechster = ss.pop("beleg_wahl_naechste", None)
    if naechster in shas:
        ss["beleg_wahl"] = naechster
    if ss.get("beleg_wahl") not in shas:
        ss["beleg_wahl"] = shas[0]

    def beschriftung(sha):
        b = nach_sha[sha]
        return (f"{zeitpunkt_de(b['zeitpunkt'])} · {b['dateiname']} · {_kategorie_label(b['ki_kategorie'])}"
                f" · {_BELEG_STATUS[b['status']]}")

    spalte_wahl, spalte_zurueck, spalte_vor = st.columns([8, 1, 1], vertical_alignment="bottom")
    sha = spalte_wahl.selectbox(f"Beleg auswählen ({len(liste)})", shas, format_func=beschriftung, key="beleg_wahl")
    pos = shas.index(sha)
    if spalte_zurueck.button("", icon=":material/chevron_left:", help="Vorheriger Beleg", disabled=pos == 0,
                             width="stretch", key="beleg_zurueck"):
        ss["beleg_wahl_naechste"] = shas[pos - 1]
        st.rerun()
    if spalte_vor.button("", icon=":material/chevron_right:", help="Nächster Beleg",
                         disabled=pos == len(shas) - 1, width="stretch", key="beleg_vor"):
        ss["beleg_wahl_naechste"] = shas[pos + 1]
        st.rerun()

    st.divider()
    _beleg_ansicht(nach_sha[sha], shas[pos + 1] if pos + 1 < len(shas) else None)


def _beleg_ansicht(b: dict, naechster: str | None) -> None:
    f, a = b.get("bewertung"), b.get("analyse_eintrag")
    if b["gruende"]:
        titel = "Warum die KI nachfragt" if b["status"] == "offen" else "Die KI hatte nachgefragt"
        (st.error if b["status"] == "offen" else st.warning)(
            f"**{titel}**\n\n" + "\n".join(f"- {g}" for g in b["gruende"]), icon=":material/help:")

    col_bild, col_daten = st.columns([2, 3], gap="large")
    with col_bild, st.container(key="belegspalte_belege"):  # so hoch wie die rechte Spalte (CSS)
        st.subheader("Beleg")
        _belegbilder(f or a, "belege")

    with col_daten:
        if f:
            st.subheader(_BELEG_STATUS[b["status"]])
            offen_key = f"beleg_korrigieren_{b['sha']}_{f.get('zeitpunkt')}"
            if st.session_state.get(offen_key):
                _beleg_formular(b, "korrigieren", naechster, offen_key)
            else:
                _vergleichstabelle(f["kategorie"], f.get("felder") or {})
                details = [f"Geprüft am {zeitpunkt_de(f.get('zeitpunkt'))} von "
                           f"{f.get('bearbeiter') or f.get('benutzer_email') or '–'}",
                           f"Quelle: {_QUELLE.get(f.get('quelle'), 'Feedback nach der Analyse')}",
                           f"Modell: `{f.get('modell') or '–'}` · Prompt-Version: `{f.get('prompt_version') or '–'}`"]
                if f.get("korrektur_von"):
                    details.append(f"Ersetzt die Prüfung vom {zeitpunkt_de(f['korrektur_von'])}")
                st.caption("  \n".join(details))
                if f.get("kommentar"):
                    st.info(f"**Kommentar:** {f['kommentar']}", icon=":material/comment:")
                if st.button("Korrigieren", icon=":material/edit:", key=f"{offen_key}_auf"):
                    st.session_state[offen_key] = True
                    st.rerun()
        elif a and a.get("analyse"):
            if b["status"] == "erledigt_alt":
                ent = b["entscheidung"]
                st.info(f"In der ersten Version erledigt ({ent.get('status')}) am "
                        f"{zeitpunkt_de(ent.get('zeitpunkt'))} von {ent.get('bearbeiter') or '–'}"
                        + (f": {ent['kommentar']}" if ent.get("kommentar") else "")
                        + ". Bitte einmal prüfen.", icon=":material/history:")
            st.subheader("Prüfen und korrigieren" if b["status"] == "offen" else "Prüfen")
            _beleg_formular(b, "nachfrage" if b["gruende"] else "pruefen", naechster)
        else:
            st.warning("Zu diesem Beleg ist kein Analyseergebnis gespeichert.")


def _beleg_formular(b: dict, modus: str, naechster: str | None, offen_key: str | None = None) -> None:
    """modus: "nachfrage" (offene Nachfrage), "pruefen" (noch nicht geprüft), "korrigieren" (Prüfung ändern)."""
    f, a = b.get("bewertung"), b.get("analyse_eintrag")
    basis = f or a
    ki = BelegAnalyse.model_validate(basis.get("analyse") or {})
    ki_kat = b["ki_kategorie"]
    key = f"bf_{b['sha']}_{basis.get('zeitpunkt')}"

    if modus != "korrigieren":
        st.caption("Die Felder zeigen, was die KI gelesen hat. Bitte mit dem Beleg vergleichen und nur Falsches "
                   "korrigieren. Beträge wie 1.234,50 – Datum als TT.MM.JJJJ.")
        if a and a.get("kalender"):
            _zeige_kalender(a["kalender"])
        if ki.korrekturen:
            st.info("**Vom Code umgedeutet**\n\n" + "\n".join(f"- {k}" for k in ki.korrekturen),
                    icon=":material/calculate:")
    kat = f["kategorie"] if f else {}
    kat_korrekt = _belegart_frage(ki_kat, key, richtig=kat.get("richtig") if f else None, korrekt=kat.get("korrekt"))
    felder_alt = (f or {}).get("felder") or {}
    start = SimpleNamespace(**{  # Startwerte: bisher als richtig festgehaltene Werte, sonst die KI-Lesung
        k: (felder_alt[k]["korrekt"] if k in felder_alt else getattr(ki, k)) for k, _l, _t in FEEDBACK_FELDER})
    pflicht = modus != "pruefen"
    with st.form(key=f"{key}_form"):
        korrekturen = _felder_eingabe(start, key)
        begruendung = st.text_area(
            "Begründung (Pflicht)" if pflicht else "Begründung (Pflicht, wenn du etwas änderst)",
            help="Warum lag die KI falsch – oder warum war ihre Nachfrage unnötig? Aus diesen Begründungen werden "
                 "später die Anweisungen an die KI verbessert.",
            placeholder="z. B. Zimmerbuchung – ist eine Übernachtung, keine Bewirtung / Trinkgeld steht "
                        "handschriftlich unten",
        )
        links, mitte, _rest = st.columns([3, 2, 3])
        abgeschickt = links.form_submit_button(
            "Korrigieren" if modus != "pruefen" else "Prüfung speichern", type="primary", width="stretch")
        abbrechen = (mitte.form_submit_button("Abbrechen", icon=":material/close:", width="stretch")
                     if modus == "korrigieren" else False)
    if abbrechen:
        st.session_state[offen_key] = False
        st.rerun()
    if not abgeschickt:
        return
    if kat_korrekt is None:
        st.error("Bitte beantworten, ob die Belegart stimmt (und ggf. die richtige wählen).")
        return
    felder, ungueltig = _felder_auswerten(ki, korrekturen)  # verglichen wird immer mit der KI-Lesung
    if ungueltig:
        st.error("Bitte prüfe diese Eingaben, sie konnten nicht gelesen werden: " + ", ".join(ungueltig))
        return
    geaendert = kat_korrekt != ki_kat or any(not x["richtig"] for x in felder.values())
    if not begruendung.strip() and (pflicht or geaendert):
        st.error("Bitte kurz begründen." + ("" if pflicht else " Du hast etwas geändert – warum lag die KI falsch?"))
        return

    bearbeiter = aktueller_benutzer()
    if f:
        neu = {k: v for k, v in f.items() if k != "zeitpunkt"}
        neu.update({"korrektur_von": f.get("zeitpunkt"), "kommentar_vorher": f.get("kommentar")})
    else:
        neu = {k: a.get(k) for k in ("datei_sha256", "dateiname", "beleg_pfad", "beleg_pfade", "modell",
                                      "prompt_version", "analyse", "kalender", "schiedsrichter")}
        neu.update({"quelle": "nachfrage" if b["gruende"] else "pruefung", "nachfrage_gruende": b["gruende"]})
    neu.update({
        "datei_sha256": b["sha"],
        "kategorie": {"modell": ki_kat, "korrekt": kat_korrekt, "richtig": ki_kat == kat_korrekt},
        "felder": felder,
        "kommentar": begruendung.strip() or None,
        "bearbeiter": bearbeiter,
        "benutzer_email": bearbeiter,
    })
    try:
        feedback_speichern(neu)
    except Exception as ex:
        st.error(f"Speichern fehlgeschlagen – bitte erneut versuchen: {ex}")
        return
    if offen_key:
        st.session_state[offen_key] = False
    elif naechster:  # neu geprüft -> gleich weiter mit dem nächsten Beleg der Liste
        st.session_state["beleg_wahl_naechste"] = naechster
    st.session_state["beleg_zuletzt"] = {"dateiname": b["dateiname"], "kategorie": neu["kategorie"], "felder": felder}
    st.rerun()


# ---------------------------------------------------------------------------
# Kategorien: anlegen, bearbeiten, löschen – fließen in den Prompt ein
# ---------------------------------------------------------------------------

_KAT_STATUS = {"aktiv": "Aktiv (wird bei jeder Analyse verwendet)", "entwurf": "Entwurf (noch nicht aktiv)"}
_NEU = "__neu__"


def _kat_feld_init(praefix: str, k: dict) -> None:
    """Startwerte der Eingabefelder einmalig setzen (danach gehören sie dem Formular / dem Assistenten)."""
    werte = {
        "name": k.get("name", ""),
        "beschreibung": k.get("beschreibung", ""),
        "erkennungsmerkmale": k.get("erkennungsmerkmale", ""),
        "abgrenzung": k.get("abgrenzung", ""),
        "pflichtangaben": "\n".join(k.get("pflichtangaben") or []) if isinstance(k.get("pflichtangaben"), list)
        else (k.get("pflichtangaben") or ""),
        "nachfragen_wenn": k.get("nachfragen_wenn", ""),
        "status": k.get("status", "entwurf"),
    }
    for feld, wert in werte.items():
        st.session_state.setdefault(f"{praefix}_{feld}", wert)


def zeige_kategorien():
    meldung = st.session_state.pop("kategorie_meldung", None)
    if meldung:  # als Einblendung, damit die Auswahl ganz oben bleibt
        st.toast(meldung, icon=":material/check_circle:")
    darf = darf_kategorien_bearbeiten()
    ss = st.session_state
    st.caption(
        "Hier legt ihr fest, welche Belegarten die KI kennt und woran sie sie erkennt. Aktive Kategorien und ihre "
        "Beschreibungen fließen bei jeder Analyse in die Anweisungen an die KI ein. Jede Änderung bekommt eine "
        f"neue Prompt-Version (aktuell `{PROMPT_VERSION}`), damit die Auswertung vorher und nachher vergleichen kann."
    )
    if not darf:
        st.caption(":material/lock: Nur ansehen – bearbeiten dürfen freigeschaltete Personen.")

    sichtbare = kat_sichtbar(KAT_STAND)
    optionen = list(sichtbare)
    # Vorschläge aus „Aus Korrekturen lernen“ (Tab Auswertung) als ungespeicherte Änderung einsetzen
    for v in ss.pop("kategorie_vorschlaege_naechste", []):
        if v["key"] not in sichtbare:
            continue
        px = f"kat_{v['key']}_{ss.setdefault('kategorie_generation', 0)}"
        _kat_feld_init(px, sichtbare[v["key"]])
        alt = (ss.get(f"{px}_{v['feld']}") or "").rstrip()
        if v["text"] not in alt:
            ss[f"{px}_{v['feld']}"] = f"{alt}\n{v['text']}" if alt else v["text"]
            ss.setdefault(f"{px}_lernvorschlag", []).append(f"{KAT_FELD_LABEL[v['feld']]}: {v['text']}")
        ss["kategorie_wahl_naechste"] = v["key"]
        ss.pop("kategorie_neu_modus", None)
    # Auswahl im Code ändern (nach Speichern/Löschen) geht nur vor dem Zeichnen der Auswahlliste
    if "kategorie_wahl_naechste" in ss:
        naechste = ss.pop("kategorie_wahl_naechste")
        if naechste in optionen:
            ss["kategorie_wahl"] = naechste
    if ss.get("kategorie_wahl") not in optionen:
        ss.pop("kategorie_wahl", None)

    spalte_liste, spalte_neu = st.columns([3, 1], vertical_alignment="bottom")
    auswahl = spalte_liste.selectbox(
        "Kategorie",
        optionen,
        format_func=lambda key: f"{sichtbare[key]['name']}"
        + ("" if sichtbare[key].get("status") == "aktiv" else " (Entwurf)"),
        key="kategorie_wahl",
        on_change=lambda: ss.pop("kategorie_neu_modus", None),  # Auswahl beendet "neu anlegen"
        label_visibility="collapsed",  # unsichtbar, bleibt aber für Screenreader als Name der Liste erhalten
    )
    if darf and spalte_neu.button("Neue Kategorie", icon=":material/add:", type="primary", width="stretch"):
        ss["kategorie_neu_modus"] = True
    wahl = _NEU if darf and ss.get("kategorie_neu_modus") else auswahl
    k = {} if wahl == _NEU else sichtbare[wahl]
    standard = bool(k.get("standard"))
    # Zähler im Schlüssel: nach dem Speichern frische Eingabefelder mit den gespeicherten Werten
    praefix = f"kat_{wahl}_{st.session_state.setdefault('kategorie_generation', 0)}"
    _kat_feld_init(praefix, k)

    st.divider()
    st.subheader("Neue Kategorie" if wahl == _NEU else k["name"])
    if ss.get(f"{praefix}_lernvorschlag"):
        st.info("**Aus Korrekturen übernommen – noch nicht gespeichert:**\n\n"
                + "\n".join(f"- {t}" for t in ss[f"{praefix}_lernvorschlag"])
                + "\n\nBitte prüfen, mit „Testen“ ausprobieren und dann „Speichern“.",
                icon=":material/playlist_add:")
    st.text_input("Name der Kategorie", key=f"{praefix}_name", disabled=standard or not darf,
                  help="Standard-Kategorien sind im Code verankert und behalten ihren Namen.")
    st.text_area(
        "Beschreibung der Buchhaltung (Freitext)",
        key=f"{praefix}_beschreibung", height=180, disabled=not darf,
        help="So, wie ihr es einem neuen Kollegen erklären würdet: Woran erkennt man den Beleg, was ist "
             "wichtig, was ist Pflicht, wann soll nachgefragt werden?",
    )

    # Assistent: Freitext -> klare Regeln + Rückfragen (Vorschlag, wird erst mit "Speichern" übernommen)
    if darf and st.button("Mit KI-Assistent in klare Regeln übersetzen", icon=":material/auto_awesome:",
                          help=f"Modell: {MODEL_GROSS}. Füllt die Felder unten als Vorschlag aus."):
        freitext = st.session_state[f"{praefix}_beschreibung"].strip()
        if not freitext:
            st.error("Bitte zuerst eine Beschreibung eingeben.")
        else:
            andere = {key: v for key, v in kat_aktiv(KAT_STAND).items() if key != wahl}
            try:
                with st.spinner("Assistent arbeitet …"):
                    vorschlag = text_anfrage_json(
                        ASSISTENT_PROMPT,
                        assistent_nachricht(st.session_state[f"{praefix}_name"], freitext, andere),
                    )
            except Exception as e:
                st.error(f"Assistent nicht erreichbar: {e}")
            else:
                for feld in ("erkennungsmerkmale", "abgrenzung", "nachfragen_wenn"):
                    if vorschlag.get(feld):
                        st.session_state[f"{praefix}_{feld}"] = str(vorschlag[feld])
                pflicht = vorschlag.get("pflichtangaben")
                if pflicht:
                    st.session_state[f"{praefix}_pflichtangaben"] = (
                        "\n".join(pflicht) if isinstance(pflicht, list) else str(pflicht))
                st.session_state[f"{praefix}_rueckfragen"] = [str(r) for r in vorschlag.get("rueckfragen") or []]
                st.rerun()

    rueckfragen = st.session_state.get(f"{praefix}_rueckfragen")
    if rueckfragen:
        st.warning("**Rückfragen des Assistenten** – bitte in der Beschreibung klären:\n\n"
                   + "\n".join(f"- {r}" for r in rueckfragen), icon=":material/help:")

    st.markdown("**Regeln für die KI** (aus der Beschreibung abgeleitet, frei änderbar)")
    st.text_area("Woran erkennt man die Kategorie?", key=f"{praefix}_erkennungsmerkmale", disabled=not darf)
    st.text_area("Abgrenzung zu anderen Kategorien", key=f"{praefix}_abgrenzung", disabled=not darf)
    st.text_area("Pflichtangaben auf dem Beleg (eine pro Zeile)", key=f"{praefix}_pflichtangaben",
                 disabled=not darf, help="Fehlt eine davon auf dem Beleg, geht er zur Nachfrage.")
    st.text_area("Wann muss nachgefragt werden?", key=f"{praefix}_nachfragen_wenn", disabled=not darf)
    if not standard:
        st.radio("Status", list(_KAT_STATUS), format_func=_KAT_STATUS.get, key=f"{praefix}_status",
                 horizontal=True, disabled=not darf)

    if darf:
        knoepfe = st.columns(4)
        if knoepfe[0].button("Speichern", type="primary", icon=":material/save:", width="stretch"):
            _kategorie_sichern(wahl, k, praefix)
        if knoepfe[1].button(
            "Testen", icon=":material/science:", width="stretch",
            help="Probelauf: Die KI liest eure Beispielbelege einmal mit der bisherigen und einmal mit der "
                 "neuen Beschreibung (so, wie sie gerade hier steht). So siehst du **vor dem Speichern**, ob "
                 "deine Änderung die Erkennung verbessert. Es wird dabei nichts gespeichert.",
        ):
            _probelauf_dialog(wahl, k, praefix)
        if wahl == _NEU:
            if knoepfe[2].button("Abbrechen", icon=":material/close:", width="stretch"):
                _kategorie_auswahl_setzen(None)
                st.rerun()
        elif not standard:
            with knoepfe[2].popover("Löschen", icon=":material/delete:", width="stretch"):
                st.write(f"„{k['name']}“ wirklich löschen? Sie wird nicht mehr verwendet. "
                         "Frühere Versionen bleiben im Verlauf erhalten.")
                if st.button("Ja, löschen", type="primary"):
                    _kategorie_sichern(wahl, k, praefix, loeschen=True)

    if wahl != _NEU:
        with st.expander("So sieht die KI diese Kategorie"):
            st.code(prompt_abschnitt({wahl: {**k, "status": "aktiv"}}), language=None)
        with st.expander("Versionsverlauf"):
            try:
                verlauf = [v for v in kategorien_laden() if v.get("schluessel") == wahl]
            except Exception as e:
                st.error(f"Verlauf nicht ladbar: {e}")
                verlauf = []
            if verlauf:
                _tabelle([{
                    "Version": v.get("version"), "Zeitpunkt": zeitpunkt_de(v.get("zeitpunkt")),
                    "Von": v.get("bearbeiter") or "–", "Status": v.get("status"),
                    "Beschreibung": (v.get("beschreibung") or "")[:120],
                } for v in reversed(verlauf)])
            else:
                st.caption("Noch nicht in der App geändert.")

    # Ergebnis eines Probelaufs bleibt nach dem Schließen des Fensters sichtbar
    ergebnis = ss.get(f"testlauf_{wahl}")
    if ergebnis:
        with st.expander("Letzter Probelauf", expanded=True):
            _zeige_testergebnis(ergebnis)


def _kategorie_auswahl_setzen(schluessel_neu: str | None) -> None:
    """Auswahl im Code ändern (nach Speichern/Löschen/Abbrechen); wird vor dem nächsten Zeichnen übernommen."""
    ss = st.session_state
    ss["kategorie_wahl_naechste"] = schluessel_neu
    ss.pop("kategorie_neu_modus", None)


@st.dialog("Probelauf: Hilft meine Änderung?", width="large")
def _probelauf_dialog(wahl: str, k: dict, praefix: str) -> None:
    _zeige_testlauf(wahl, k, praefix)


def _kategorie_aus_formular(schluessel: str, name: str, praefix: str) -> dict:
    ss = st.session_state
    return {
        "schluessel": schluessel,
        "name": name,
        "beschreibung": ss[f"{praefix}_beschreibung"].strip(),
        "erkennungsmerkmale": ss[f"{praefix}_erkennungsmerkmale"].strip(),
        "abgrenzung": ss[f"{praefix}_abgrenzung"].strip(),
        "pflichtangaben": [z.strip() for z in ss[f"{praefix}_pflichtangaben"].splitlines() if z.strip()],
        "nachfragen_wenn": ss[f"{praefix}_nachfragen_wenn"].strip(),
    }


def _standard_ordner(name: str, ordner: list[str]) -> int:
    """Index des Beispielordners, der am besten zum Kategorienamen passt (gleiches erstes Wort)."""
    wort = name.split(":")[-1].split()[0].lower() if name.strip() else ""
    for i, o in enumerate(ordner):
        if o.lower() == name.lower():
            return i
    for i, o in enumerate(ordner):
        if wort and wort[:6] in o.lower():
            return i
    return 0


def _zeige_testlauf(wahl: str, k: dict, praefix: str) -> None:
    st.markdown(
        "Die KI liest eure Beispielbelege zweimal – mit der **bisherigen** und mit der **neuen** Beschreibung "
        "(so, wie sie gerade im Formular steht). Danach siehst du, welche besser funktioniert.",
        help="Es wird dabei nichts gespeichert und nichts verändert. Dauer: etwa 2–4 Minuten. Kosten: "
             "insgesamt nur wenige Cent. Die Beispielbelege liegen in Google Drive im Ordner "
             "„Anlage KI Assistent“ und dort im passenden Unterordner.",
    )
    name = (k.get("name") or st.session_state.get(f"{praefix}_name", "")).strip()
    try:
        ordner = anlage_ordner()
    except Exception as e:
        st.error(f"Beispielordner nicht ladbar: {e}")
        return
    neuer = f"Neuer Ordner: {name}" if name and name not in ordner else None
    optionen = ordner + ([neuer] if neuer else [])
    if not optionen:
        st.info("Bitte zuerst einen Namen für die Kategorie eingeben.")
        return
    gewaehlt = st.selectbox("Welche Beispielbelege sollen genutzt werden? (Ordner in Google Drive)", optionen,
                            index=_standard_ordner(name, ordner) if ordner else 0, key=f"{praefix}_testordner")
    ordnername = name if gewaehlt == neuer else gewaehlt
    belege = beispielbelege(ordnername) if gewaehlt != neuer else []
    anzahl = "1 Beispielbeleg" if len(belege) == 1 else f"{len(belege)} Beispielbelege"
    st.markdown(
        f'<p style="font-size:1.15rem;margin:.25rem 0 .75rem">'
        f'<strong>{anzahl.split(" ")[0]}</strong> {anzahl.split(" ", 1)[1]} im Ordner „{html.escape(ordnername)}“'
        + (f" – getestet werden die ersten <strong>{MAX_TESTBELEGE}</strong>." if len(belege) > MAX_TESTBELEGE
           else ("." if belege else " – bitte zuerst Belege hinzufügen."))
        + "</p>",
        unsafe_allow_html=True,
    )

    upload_ok = st.session_state.pop(f"{praefix}_upload_ok", None)
    if upload_ok:
        st.success(upload_ok, icon=":material/check_circle:")

    spalte_start, spalte_upload = st.columns(2)
    starten = spalte_start.button("Probelauf starten", type="primary", icon=":material/science:",
                                  width="stretch", disabled=not belege)
    with spalte_upload.popover("Weitere Belege hinzufügen", icon=":material/upload:", width="stretch"):
        neue = st.file_uploader(f"Belege für „{ordnername}“ auswählen", type=["pdf", "jpg", "jpeg", "png"],
                                accept_multiple_files=True, key=f"{praefix}_testupload")
        if neue and st.button(f"{len(neue)} Datei(en) hochladen", type="primary", icon=":material/upload:"):
            try:
                for datei in neue:
                    beispielbeleg_hochladen(ordnername, datei.name, datei.getvalue(), datei.type)
            except Exception as e:
                st.error(f"Hochladen fehlgeschlagen: {e}")
            else:
                st.session_state[f"{praefix}_upload_ok"] = (
                    f"{len(neue)} Beleg(e) in „{ordnername}“ abgelegt.")
                st.rerun(scope="fragment")  # nur das Fenster neu aufbauen, nicht schließen

    if starten:
        schluessel = wahl if wahl != _NEU else kat_schluessel(name)
        entwurf = {**KAT_STAND, schluessel: {**KAT_STAND.get(schluessel, {}),
                                             **_kategorie_aus_formular(schluessel, name, praefix),
                                             "status": "aktiv", "standard": bool(k.get("standard"))}}
        nachher = system_prompt(prompt_abschnitt(entwurf))
        varianten = {"vorher": SYSTEM_PROMPT_AKTUELL, "nachher": nachher}
        if nachher == SYSTEM_PROMPT_AKTUELL:
            varianten = {"nachher": nachher}  # nichts geändert -> nur ein Durchlauf
        auswahl = belege[:MAX_TESTBELEGE]
        kategorien_registrieren({**labels(KAT_STAND), schluessel: name})  # neue Kategorie für den Test gültig
        balken = st.progress(0.0, text="Probelauf läuft …")
        try:
            daten = [(b["name"], beispielbeleg_laden(b["ref"]), b["mime"]) for b in auswahl]
            ergebnisse = testlauf_ausfuehren(
                daten, varianten,
                fortschritt=lambda f, g: balken.progress(f / g, text=f"Probelauf: {f} von {g} Lesungen fertig"),
            )
        except Exception as e:
            st.error(f"Probelauf fehlgeschlagen: {e}")
            return
        finally:
            kategorien_registrieren(labels(KAT_STAND))
        balken.empty()
        st.session_state[f"testlauf_{wahl}"] = {"ergebnisse": ergebnisse, "varianten": list(varianten),
                                                "erwartet": erwartete_kategorien(schluessel), "name": name}

    ergebnis = st.session_state.get(f"testlauf_{wahl}")
    if ergebnis:
        _zeige_testergebnis(ergebnis)


_VARIANTE_TITEL = {"vorher": "Bisherige Beschreibung", "nachher": "Neue Beschreibung"}


def _testlauf_fazit(vorher: dict, nachher: dict) -> tuple[str, str]:
    """-> (Art: success/warning/info, verständlicher Satz)."""
    d_erkannt = nachher["erkannt"] - vorher["erkannt"]
    d_nachfragen = nachher["nachfragen"] - vorher["nachfragen"]
    if d_erkannt == 0 and d_nachfragen == 0:
        return "info", "**Kein Unterschied.** Beide Beschreibungen liefern bei den Beispielbelegen dasselbe Ergebnis."

    def belege(n):
        return "1 Beleg" if n == 1 else f"{n} Belege"

    teile = []
    if d_erkannt:
        teile.append(f"{belege(abs(d_erkannt))} {'mehr' if d_erkannt > 0 else 'weniger'} richtig erkannt")
    if d_nachfragen:
        teile.append(f"{belege(abs(d_nachfragen))} {'mehr' if d_nachfragen > 0 else 'weniger'} müssten "
                     "nachgefragt werden")
    satz = "Mit der neuen Beschreibung: " + " und ".join(teile) + "."
    if abs(d_erkannt) + abs(d_nachfragen) <= 1:
        satz += (" Ein Unterschied von nur einem Beleg kann auch Zufall sein – im Zweifel den Probelauf "
                 "noch einmal starten.")
    # Richtig erkennen ist wichtiger als wenige Rückfragen
    besser = d_erkannt > 0 or (d_erkannt == 0 and d_nachfragen < 0)
    if besser:
        return "success", "**Die Änderung hilft.** " + satz
    return "warning", "**Eher nicht speichern.** " + satz


def _zeige_testergebnis(t: dict) -> None:
    ergebnisse, varianten, erwartet = t["ergebnisse"], t["varianten"], t["erwartet"]
    if varianten == ["nachher"]:
        st.info("Die Beschreibung oben ist unverändert – deshalb wurde nur einmal gelesen.",
                icon=":material/info:")
    zahlen = {v: testlauf_zusammenfassung(ergebnisse, v, erwartet) for v in varianten}
    for spalte, v in zip(st.columns(len(varianten)), varianten):
        z = zahlen[v]
        spalte.markdown(f"**{_VARIANTE_TITEL[v]}**")
        spalte.markdown(
            f"- Richtig als „{t['name']}“ erkannt: **{z['erkannt']} von {z['belege']}**\n"
            f"- Müssten nachgefragt werden: **{z['nachfragen']} von {z['belege']}**"
            + (f"\n- Konnten nicht gelesen werden: **{z['fehler']}**" if z["fehler"] else "")
        )
    if len(varianten) == 2:
        art, text = _testlauf_fazit(zahlen["vorher"], zahlen["nachher"])
        getattr(st, art)(text, icon={"success": ":material/thumb_up:", "warning": ":material/thumb_down:",
                                     "info": ":material/drag_handle:"}[art])
    st.caption("Grün = KI ist sicher, rot = müsste nachgefragt werden, grau = Datei nicht lesbar. "
               "✓ = als diese Kategorie erkannt, ✗ = als andere Kategorie erkannt.")

    def zelle(e) -> str:
        if e is None:
            return "–"
        if e.fehler:
            return f"Fehler: {e.fehler[:60]}"
        treffer = "✓" if e.kategorie in erwartet else "✗"
        return (f"{treffer} {_kategorie_label(e.kategorie)} · {e.betrag} · "
                f"{'Nachfrage' if e.nachfrage else 'ok'}")

    zeilen = []
    for name, je_variante in ergebnisse.items():
        zeile = {"Beleg": name}
        for v in varianten:
            zeile[_VARIANTE_TITEL[v]] = zelle(je_variante.get(v))
        if len(varianten) == 2:
            a, b = je_variante.get("vorher"), je_variante.get("nachher")
            geaendert = a and b and (a.kategorie != b.kategorie or a.nachfrage != b.nachfrage)
            zeile["Unterschied"] = "anders" if geaendert else "–"
        zeilen.append(zeile)
    _farbtabelle(zeilen, [_VARIANTE_TITEL[v] for v in varianten])
    with st.expander("Warum müsste nachgefragt werden? (Details)"):
        for name, je_variante in ergebnisse.items():
            for v, e in je_variante.items():
                if e.gruende:
                    st.markdown(f"**{name}** ({_VARIANTE_TITEL[v]}): " + "; ".join(e.gruende))


def _kategorie_sichern(wahl: str, k: dict, praefix: str, loeschen: bool = False) -> None:
    ss = st.session_state
    name = (k["name"] if k.get("standard") else ss[f"{praefix}_name"]).strip()
    if not name:
        st.error("Bitte einen Namen eingeben.")
        return
    schluessel = wahl
    if wahl == _NEU:
        schluessel = kat_schluessel(name)
        if schluessel in KAT_STAND and KAT_STAND[schluessel].get("status") != "geloescht":
            st.error(f"Eine Kategorie mit diesem Namen gibt es schon („{KAT_STAND[schluessel]['name']}“).")
            return
    eintrag = {
        **_kategorie_aus_formular(schluessel, name, praefix),
        "status": "geloescht" if loeschen else ("aktiv" if k.get("standard") else ss[f"{praefix}_status"]),
        "version": int(KAT_STAND.get(schluessel, {}).get("version") or 0) + 1,
        "bearbeiter": aktueller_benutzer(),
    }
    try:
        kategorie_speichern(eintrag)
    except Exception as e:
        st.error(f"Speichern fehlgeschlagen: {e}")
        return
    hinweis = ""
    try:  # lesbare Kopie im Drive – Fehler hier blockieren nicht
        kategorie_dokument_ablegen(name, kat_dokument({**eintrag, "zeitpunkt": None}))
    except Exception as e:
        hinweis = f" (Dokument im Drive konnte nicht aktualisiert werden: {e})"
    ss["kategorie_generation"] = ss.get("kategorie_generation", 0) + 1
    _kategorie_auswahl_setzen(None if loeschen else schluessel)
    aktion = "gelöscht" if loeschen else f"gespeichert (Version {eintrag['version']}, {eintrag['status']})"
    ss["kategorie_meldung"] = f"„{name}“ {aktion}.{hinweis}"
    st.rerun()


# ---------------------------------------------------------------------------
# Seitenaufbau
# ---------------------------------------------------------------------------

def _zustand_zuruecksetzen():
    for k in ("ergebnis", "fehler", "stapel"):
        st.session_state.pop(k, None)


# Tabs farbig absetzen (RE-ALIS-Farben): jede Ansicht hat ihre Farbe (Rand), der aktive Tab ist gefüllt.
# Alle Füllfarben mit weißer Schrift >= 4,5:1 (WCAG AA), als Rand auf hellem Grund >= 3:1.
# (Füllung aktiver Tab, Rand) je Tab: Analyse (Marken-Orange), Nachfragen (Marken-Blau), Kategorien
# (Schiefer aus der Firmenseite), Auswertung (Grün).
_TAB_FARBEN = [("#C25100", "#C25100"), ("#036BD3", "#036BD3"), ("#373A43", "#373A43"), ("#15803D", "#15803D")]
# Selektoren passend zu Streamlit 1.67 (react-aria-Tabs): role="tablist"/"tab", aktiv = [data-selected].
_TAB = '.stTabs [role="tab"]'
st.markdown(
    "<style>"
    '[class*="st-key-belegbild_"] img {max-height: 560px; width: auto; max-width: 100%; object-fit: contain;'
    ' display: block; margin: 0 auto;}'
    '[class*="st-key-belegbild_"] {position: relative;}'
    + ''.join(f'[class*="st-key-belegbild_"] {x}, ' for x in (
        '[data-testid="stElementContainer"]', '[data-testid="stFullScreenFrame"]',
        '[data-testid="stFullScreenFrame"] > div', '[data-testid="stImage"]'))
    + '[class*="st-key-belegbild_"] [data-testid="stImageContainer"]'
    ' {width: 100% !important; align-items: center; justify-content: center;}'
    # Bild selbst ist klickbar: unsichtbarer Button liegt darüber; Hover-Vollbild-Symbol ausblenden
    '[class*="st-key-bildklick_"] {position: absolute !important; inset: 0; z-index: 2; height: 100% !important;'
    ' width: 100% !important;}'
    '[class*="st-key-bildklick_"] > div, [class*="st-key-bildklick_"] .stButton {height: 100%; width: 100%;}'
    '[class*="st-key-bildklick_"] button {width: 100% !important; height: 100%; opacity: 0; cursor: zoom-in;}'
    '.st-key-grossbild {position: relative;}'
    '.st-key-grossbild [data-testid="stElementToolbar"] {display: none;}'
    '.st-key-bildklick_schliessen button {cursor: zoom-out;}'
    '[class*="st-key-gross_"] button {width: auto !important; flex: 0 0 auto;}'
    '[class*="st-key-belegbild_"] [data-testid="stElementToolbar"] {display: none;}'
    # Button und Seitenangabe mittig unter dem Bild
    '[class*="st-key-gross_"], [class*="st-key-gross_"] > div, [class*="st-key-gross_"] .stButton'
    ' {display: flex; justify-content: center; width: 100%;}'
    '[class*="st-key-belegspalte_"] [data-testid="stCaptionContainer"] {text-align: center;}'
    # Analyse-Knopf: während der Analyse nur ein drehender Spinner (Text bleibt für Screenreader erhalten)
    "@keyframes beleg-dreh {to {transform: rotate(360deg);}}"
    '.st-key-analyse_knopf button:disabled [data-testid="stIconMaterial"] {animation: beleg-dreh 1s linear infinite;}'
    '.st-key-analyse_knopf button:disabled [data-testid="stMarkdownContainer"] {position: absolute; width: 1px;'
    ' height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap;}'
    '.st-key-analyse_knopf button {min-width: 12rem;}'
    # solange der Knopf gesperrt ist (Analyse läuft), sind die anderen Tabs gesperrt
    '.stApp:has(.st-key-analyse_knopf button:disabled) .stTabs [role="tab"]:not([data-selected])'
    ' {pointer-events: none; opacity: .4;}'
    # Belegspalte (Nachfragen/Auswertung) genau so hoch wie die rechte Spalte: Inhalt absolut im Spaltenrahmen,
    # das Bild füllt den Platz zwischen Überschrift und Button. Nur nebeneinander (ab 640 px), sonst normal.
    "@media (min-width: 640px) {"
    '[data-testid="stColumn"]:has([class*="st-key-belegspalte_"]) {position: relative; min-height: 360px;}'
    '[class*="st-key-belegspalte_"] {position: absolute; inset: 0; flex-wrap: nowrap;}'
    '[class*="st-key-belegspalte_"] > :has([class*="st-key-belegbild_"]) {flex: 1 1 0; min-height: 0;}'
    '[class*="st-key-belegspalte_"] [class*="st-key-belegbild_"],'
    ' [class*="st-key-belegspalte_"] [class*="st-key-belegbild_"] div {height: 100%; min-height: 0;}'
    '[class*="st-key-belegspalte_"] [class*="st-key-belegbild_"] img {height: 100%; max-height: none;'
    ' width: 100%; object-fit: contain; object-position: center top;}'
    "}"
    '.stTabs [role="tablist"] {gap: .5rem; padding: .4rem; border-radius: .75rem; flex-wrap: wrap;'
    " background: rgba(128,128,128,.14);}"
    '.stTabs [role="tablist"]::after {display: none;}'  # graue Grundlinie ausblenden
    f"{_TAB} {{padding: .5rem 1.1rem; border-radius: .55rem; height: auto; border: 2px solid transparent;"
    " font-weight: 600; color: inherit;}"
    f"{_TAB} p {{font-size: 1rem;}}"
    f"{_TAB} .react-aria-SelectionIndicator {{display: none;}}"  # Unterstrich ersetzt durch Füllung
    + "".join(
        f"{_TAB}:nth-child({i}) {{border-color: {rand};}}"
        f"{_TAB}:nth-child({i})[data-hovered]:not([data-selected]) {{background: {fuellung}55; color: inherit;}}"
        f"{_TAB}:nth-child({i})[data-selected] {{background: {fuellung}; color: #fff;}}"
        f"{_TAB}:nth-child({i})[data-selected] p {{color: #fff;}}"
        for i, (fuellung, rand) in enumerate(_TAB_FARBEN, start=1)
    )
    + "</style>",
    unsafe_allow_html=True,
)


kopfzeile()
_offen = _offene_nachfragen()
tab_analyse, tab_belege, tab_kategorien, tab_auswertung = st.tabs([
    ":material/upload_file: Belege hochladen",
    ":material/receipt_long: Belege" + (f" ({_offen} {'Nachfrage' if _offen == 1 else 'Nachfragen'} offen)" if _offen else ""),
    ":material/category: Kategorien",
    ":material/monitoring: Auswertung",
])

with tab_analyse:
    st.caption(
        "Lade einen oder mehrere Belege hoch. Die KI ordnet sie ein (Bewirtung, Tanken, Übernachtung, ...), "
        "liest die Daten ab, der Code rechnet nach. Jeder Beleg landet danach im Tab „Belege“ – dort lässt er "
        "sich prüfen und korrigieren; Belege mit Nachfrage der KI sind dort eigens gefiltert."
    )
    uploads = st.file_uploader(
        "Belege hochladen (JPG, PNG, PDF)",
        type=["jpg", "jpeg", "png", "pdf"],
        accept_multiple_files=True,
        help=f"Mehrere Dateien auf einmal möglich (bis {MAX_STAPEL}). Mehrseitige PDFs werden vollständig "
             f"gelesen (bis {MAX_SEITEN} Seiten).",
    )

    stapel_modus = False
    if uploads and len(uploads) > 1:
        modus = st.radio(
            "Wie sollen die Dateien gelesen werden?",
            ["stapel", "ein_beleg"],
            format_func={
                "stapel": f"Jede Datei ist ein eigener Beleg (Stapel, {len(uploads)} Belege)",
                "ein_beleg": "Alle Dateien gehören zu EINEM Beleg (z. B. Rechnung + EC-Beleg, mehrere Fotos)",
            }.get,
            key="upload_modus",
        )
        stapel_modus = modus == "stapel"
        if stapel_modus and len(uploads) > MAX_STAPEL:
            st.warning(f"Es werden die ersten {MAX_STAPEL} von {len(uploads)} Dateien analysiert.")

    if not uploads:
        _zustand_zuruecksetzen()  # Dateien entfernt -> alten Zustand verwerfen
    else:
        # andere Dateien oder anderer Modus -> alte Ergebnisse passen nicht mehr
        kennung = (stapel_modus, beleg_hash(_dateien(uploads)))
        if st.session_state.get("upload_kennung") != kennung:
            _zustand_zuruecksetzen()
            st.session_state["upload_kennung"] = kennung

        knopf = f"{min(len(uploads), MAX_STAPEL)} Belege analysieren" if stapel_modus else "Beleg analysieren"
        if st.session_state.pop("analyse_laeuft", False):
            # Läuft: Knopf zeigt nur einen Spinner, die anderen Tabs sind gesperrt – ein Klick dort würde
            # die Analyse abbrechen (Streamlit startet das Skript bei jeder Eingabe neu).
            st.button(knopf, type="primary", disabled=True, icon=":material/progress_activity:",
                      key="analyse_knopf")  # gleicher Schlüssel: derselbe Knopf, nur gesperrt mit Spinner
            st.caption("Analyse läuft – bitte warten. Die anderen Tabs sind so lange gesperrt.")
            if stapel_modus:
                stapel_starten(uploads)
            else:
                analyse_starten(uploads)  # zeigt den Fortschritt selbst an (st.status)
            st.rerun()
        elif st.button(knopf, type="primary", key="analyse_knopf"):
            st.session_state["analyse_laeuft"] = True
            st.rerun()

    if "fehler" in st.session_state:
        meldung, raw = st.session_state["fehler"]
        st.error(meldung)
        if raw:
            st.code(raw)

    if "stapel" in st.session_state:
        zeige_stapel(st.session_state["stapel"])
    elif "ergebnis" in st.session_state:
        zeige_ergebnis(st.session_state["ergebnis"])

with tab_belege:
    zeige_belege()

with tab_kategorien:
    zeige_kategorien()

with tab_auswertung:
    zeige_auswertung()

st.divider()
st.caption(
    f"Proof of Concept · Modell `{MODEL}` (liest doppelt) · Schiedsrichter `{MODEL_GROSS}` · "
    f"Prompt-Version `{PROMPT_VERSION}`"
)

