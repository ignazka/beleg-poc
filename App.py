"""Praktikant-AI (RE-ALIS) – Belege analysieren, Nachfragen, Kategorien, Auswertung."""

import csv
import io
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import streamlit as st

from auth import aktueller_benutzer, darf_kategorien_bearbeiten, login_erzwingen
from beleg_schema import FEEDBACK_FELDER, KATEGORIEN, BelegAnalyse, kategorien_registrieren, anzeige, normalisieren, werte_gleich, zeitpunkt_de
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
from kategorien import schluessel_aus_name as kat_schluessel
from kategorien import sichtbar as kat_sichtbar
from prompts import prompt_version, system_prompt
from storage import (
    AblageFehler,
    ablage_beschreibung,
    ablage_pruefen,
    beleg_speichern,
    datei_hash,
    feedback_laden,
    beleg_laden,
    entscheidung_speichern,
    entscheidungen_laden,
    anlage_ordner,
    beispielbeleg_hochladen,
    beispielbeleg_laden,
    beispielbelege,
    kategorie_dokument_ablegen,
    kategorie_speichern,
    kategorien_laden,
    feedback_speichern,
    nachfrage_speichern,
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
st.sidebar.caption(f"Ablage: {ablage_beschreibung()}")

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
    return KATEGORIEN.get(key, key)


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
            erg["nachfrage_status"] = _nachfrage_ablegen(erg) if erg["nachfrage_gruende"] else None
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
            erg["nachfrage_status"] = _nachfrage_ablegen(erg) if erg["nachfrage_gruende"] else None
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
        st.info(f"{nachfrage} Beleg(e) liegen jetzt im Tab „Nachfragen“ zur Prüfung.", icon=":material/help:")

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


def _nachfrage_ablegen(erg) -> str:
    """Unsichere/unstimmige Belege sofort ablegen – unabhängig davon, ob jemand Feedback gibt."""
    analyse = erg["analyse"]
    try:
        pfade = _belege_speichern(erg["dateien"])
        neu = nachfrage_speichern({
            "datei_sha256": erg["sha"],
            "dateiname": erg["dateiname"],
            "beleg_pfad": pfade[0],
            "beleg_pfade": pfade,
            "kategorie": analyse.kategorie,
            "gruende": erg["nachfrage_gruende"],
            "analyse": analyse.model_dump(),
            "rohantwort": erg["raw"],
            "lesungen": _lesungen_protokoll(erg),
            "modell": erg["modell"],
            "prompt_version": erg["prompt_version"],
            "benutzer_email": aktueller_benutzer(),
        })
    except Exception as e:
        return f"Ablegen fehlgeschlagen: {e}"
    return "abgelegt" if neu else "war schon abgelegt"


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
    if "nachfrage" in t:
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


def zeige_ergebnis(erg):
    analyse = erg["analyse"]
    gruende = erg.get("nachfrage_gruende") or []
    status = erg.get("nachfrage_status") or ""

    # 1. Gesamtergebnis zuerst – das Wichtigste oben
    if gruende:
        text = "**Nachfrage nötig.** " + (
            f"Ablegen fehlgeschlagen: {status}" if status.startswith("Ablegen fehlgeschlagen")
            else f"Der Beleg wurde unter „Nachfragen“ {status} – dort prüfen, korrigieren und entscheiden."
        )
        st.error(text + "\n\n" + "\n".join(f"- {g}" for g in gruende), icon=":material/help:")
    else:
        st.success("**Alles stimmig.** Alle Prüfungen bestanden, keine Nachfrage nötig.",
                   icon=":material/check_circle:")

    col_bild, col_daten = st.columns([2, 3], gap="large")

    # 2. Beleg
    with col_bild:
        st.subheader("Beleg")
        gesamt, gelesen = erg["seiten_gesamt"], erg["seiten_gelesen"]
        if gelesen < gesamt:
            st.warning(f"{gesamt} Seiten – nur die ersten {gelesen} wurden gelesen (Obergrenze {MAX_SEITEN}).")
        else:
            st.caption(f"{gesamt} Seite(n) aus {len(erg['dateien'])} Datei(en): {erg['dateiname']}")
        n = len(erg["vorschau"])
        for i, bild in enumerate(erg["vorschau"], start=1):
            st.image(bild, width="stretch", caption=f"Beleg, Seite {i} von {n}")

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
             if analyse.ec_passt_zur_rechnung is not None else [])))
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

    kategorie_keys = list(KATEGORIEN.keys())
    with st.form(key=f"feedback_{erg['sha']}"):
        kat_korrekt = st.selectbox(
            "Richtige Kategorie",
            options=kategorie_keys,
            index=kategorie_keys.index(analyse.kategorie),
            format_func=_kategorie_label,
        )

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

def zeige_auswertung():
    try:
        eintraege = feedback_laden()
    except Exception as e:
        st.error(f"Feedback konnte nicht geladen werden: {e}")
        return
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
                    "Zeitpunkt": zeitpunkt_de(e.get("zeitpunkt")),
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
# Nachfragen: Mensch prüft, korrigiert und entscheidet
# ---------------------------------------------------------------------------

STATUS = {
    "offen": "Offen",
    "zurueckgestellt": "Zurückgestellt (Rückfrage läuft)",
    "freigegeben": "Freigegeben",
    "verworfen": "Verworfen",
}
ENTSCHEIDUNGEN = {
    "freigegeben": "Freigeben – Daten stimmen (ggf. korrigiert), Beleg kann weiter",
    "zurueckgestellt": "Zurückstellen – Rückfrage bei Mitarbeiter:in nötig (z. B. Rechnung fehlt)",
    "verworfen": "Verwerfen – kein gültiger Beleg oder Duplikat",
}
_MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _letzte_entscheidungen(entscheidungen: list[dict]) -> dict[str, dict]:
    """Datei-Hash -> jüngste Entscheidung (Einträge werden nie überschrieben, nur angehängt)."""
    letzte = {}
    for e in sorted(entscheidungen, key=lambda e: e.get("zeitpunkt") or ""):
        letzte[e.get("datei_sha256")] = e
    return letzte


@st.cache_data(show_spinner="Lade Beleg …", max_entries=30)
def _vorschau(pfad: str) -> list[bytes]:
    endung = "." + pfad.rsplit(".", 1)[-1].lower()
    daten = beleg_laden(pfad)
    return dateien_zu_bildern([(pfad, daten, _MIME.get(endung, "image/jpeg"))])[0]


def zeige_nachfragen():
    meldung = st.session_state.pop("nachfrage_meldung", None)
    if meldung:
        st.success(meldung, icon=":material/check_circle:")
    try:
        nachfragen = nachfragen_laden()
        letzte = _letzte_entscheidungen(entscheidungen_laden())
    except Exception as e:
        st.error(f"Nachfragen konnten nicht geladen werden: {e}")
        return
    if not nachfragen:
        st.success("Keine Nachfragen. Unklare Belege landen hier automatisch nach der Analyse.")
        return

    def status(n):
        return letzte.get(n.get("datei_sha256"), {}).get("status", "offen")

    anzahl = {s: sum(1 for n in nachfragen if status(n) == s) for s in STATUS}
    for spalte, (s, label) in zip(st.columns(4), STATUS.items()):
        spalte.metric(label.split(" (")[0], anzahl[s])

    ansichten = {
        "offen": f"Offen ({anzahl['offen']})",
        "zurueckgestellt": f"Zurückgestellt ({anzahl['zurueckgestellt']})",
        "erledigt": "Erledigt",
        "alle": "Alle",
    }
    ansicht = st.radio("Anzeigen", list(ansichten), format_func=ansichten.get, horizontal=True,
                       key="nachfragen_ansicht")
    passend = {
        "offen": lambda n: status(n) == "offen",
        "zurueckgestellt": lambda n: status(n) == "zurueckgestellt",
        "erledigt": lambda n: status(n) in ("freigegeben", "verworfen"),
        "alle": lambda n: True,
    }[ansicht]
    liste = [n for n in reversed(nachfragen) if passend(n)]  # neueste zuerst
    if not liste:
        st.info("In dieser Ansicht gibt es nichts zu tun.")
        return

    gewaehlt = st.selectbox(
        f"Nachfrage auswählen ({len(liste)})",
        liste,
        format_func=lambda n: (
            f"{zeitpunkt_de(n.get('zeitpunkt'))} · {n.get('dateiname')} · {_kategorie_label(n.get('kategorie'))}"
            f" · {STATUS[status(n)]}"
        ),
        key="nachfrage_auswahl",
    )
    st.divider()
    _nachfrage_bearbeiten(gewaehlt, letzte.get(gewaehlt.get("datei_sha256")))

    with st.expander("Alle Nachfragen als Tabelle"):
        st.dataframe(
            [{
                "Zeitpunkt": zeitpunkt_de(n.get("zeitpunkt")),
                "Status": STATUS[status(n)],
                "Datei": n.get("dateiname"),
                "Kategorie (KI)": _kategorie_label(n.get("kategorie")),
                "Gründe": " | ".join(n.get("gruende", [])),
                "Hochgeladen von": n.get("benutzer_email") or "",
                "Entschieden von": letzte.get(n.get("datei_sha256"), {}).get("bearbeiter") or "",
            } for n in reversed(nachfragen)],
            hide_index=True,
            width="stretch",
        )


def _nachfrage_bearbeiten(n: dict, letzte: dict | None):
    sha = n.get("datei_sha256") or "ohne_hash"
    analyse = BelegAnalyse.model_validate(n.get("analyse") or {})

    st.error(
        "**Warum nachgefragt wird**\n\n" + "\n".join(f"- {g}" for g in n.get("gruende", [])),
        icon=":material/help:",
    )
    if letzte:
        st.info(
            f"**Letzte Entscheidung:** {STATUS.get(letzte.get('status'), letzte.get('status'))} – "
            f"{zeitpunkt_de(letzte.get('zeitpunkt'))}, {letzte.get('bearbeiter') or 'unbekannt'}"
            + (f"\n\n{letzte['kommentar']}" if letzte.get("kommentar") else ""),
            icon=":material/history:",
        )

    col_bild, col_form = st.columns([2, 3], gap="large")
    with col_bild:
        st.subheader("Beleg")
        pfade = n.get("beleg_pfade") or ([n["beleg_pfad"]] if n.get("beleg_pfad") else [])
        for pfad in pfade:
            try:
                bilder = _vorschau(pfad)
            except Exception as e:
                st.warning(f"{pfad} konnte nicht geladen werden: {e}")
                continue
            for i, bild in enumerate(bilder, start=1):
                st.image(bild, width="stretch", caption=f"{pfad}, Seite {i} von {len(bilder)}")

    with col_form:
        st.subheader("Prüfen und entscheiden")
        st.caption(
            "Die Felder zeigen, was die KI gelesen hat. Bitte mit dem Beleg vergleichen und nur Falsches "
            "korrigieren. Beträge wie 1.234,50 – Datum als TT.MM.JJJJ."
        )
        if analyse.korrekturen:
            st.info("**Vom Code umgedeutet**\n\n" + "\n".join(f"- {k}" for k in analyse.korrekturen),
                    icon=":material/calculate:")
        kategorie_keys = list(KATEGORIEN.keys())
        with st.form(key=f"nachfrage_form_{sha}"):
            kat = st.selectbox(
                "Belegart",
                kategorie_keys,
                index=kategorie_keys.index(analyse.kategorie) if analyse.kategorie in kategorie_keys else 0,
                format_func=_kategorie_label,
            )
            korrekturen = _felder_eingabe(analyse, f"nf_{sha}")
            entscheidung = st.radio(
                "Entscheidung", list(ENTSCHEIDUNGEN), format_func=ENTSCHEIDUNGEN.get, index=None,
            )
            kommentar = st.text_area(
                "Begründung / Notiz",
                help="Pflicht beim Zurückstellen und Verwerfen – z. B. wen du was gefragt hast.",
                placeholder="z. B. Steuersatz 0 % ist korrekt: Gutschein-Einlösung",
            )
            abgeschickt = st.form_submit_button("Entscheidung speichern", type="primary")

    if not abgeschickt:
        return
    if entscheidung is None:
        st.error("Bitte eine Entscheidung auswählen.")
        return
    if entscheidung != "freigegeben" and not kommentar.strip():
        st.error("Bitte kurz begründen, warum zurückgestellt bzw. verworfen wird.")
        return
    felder, ungueltig = _felder_auswerten(analyse, korrekturen)
    if ungueltig:
        st.error("Bitte prüfe diese Eingaben, sie konnten nicht gelesen werden: " + ", ".join(ungueltig))
        return

    bearbeiter = aktueller_benutzer()
    try:
        entscheidung_speichern({
            "datei_sha256": sha,
            "dateiname": n.get("dateiname"),
            "status": entscheidung,
            "kategorie": kat,
            "daten": {k: f["korrekt"] for k, f in felder.items()},  # geprüfte Werte für den nächsten Schritt
            "korrigierte_felder": [k for k, f in felder.items() if not f["richtig"]],
            "kommentar": kommentar.strip() or None,
            "bearbeiter": bearbeiter,
            "gruende": n.get("gruende", []),
        })
        if entscheidung == "freigegeben":
            # Freigabe = von einem Menschen geprüft -> zählt auch in der Auswertung (Trefferquote der KI)
            feedback_speichern({
                "quelle": "nachfrage",
                "datei_sha256": sha,
                "dateiname": n.get("dateiname"),
                "beleg_pfad": n.get("beleg_pfad"),
                "beleg_pfade": n.get("beleg_pfade"),
                "modell": n.get("modell"),
                "prompt_version": n.get("prompt_version"),
                "analyse": n.get("analyse"),
                "kategorie": {"modell": analyse.kategorie, "korrekt": kat, "richtig": analyse.kategorie == kat},
                "felder": felder,
                "kommentar": kommentar.strip() or None,
                "bearbeiter": bearbeiter,
                "benutzer_email": bearbeiter,
            })
    except Exception as e:
        st.error(f"Speichern fehlgeschlagen – bitte erneut versuchen: {e}")
        return

    korrigiert = sum(1 for f in felder.values() if not f["richtig"]) + (analyse.kategorie != kat)
    st.session_state["nachfrage_meldung"] = (
        f"„{n.get('dateiname')}“: {STATUS[entscheidung]}"
        + (f" – {korrigiert} Angabe(n) korrigiert." if korrigiert else ".")
    )
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
    st.caption(f"{len(belege)} Beispielbeleg(e) im Ordner „{ordnername}“"
               + (f" – getestet werden die ersten {MAX_TESTBELEGE}." if len(belege) > MAX_TESTBELEGE else "."))

    upload_ok = st.session_state.pop(f"{praefix}_upload_ok", None)
    if upload_ok:
        st.success(upload_ok, icon=":material/check_circle:")
    neue = st.file_uploader("Beispielbelege hinzufügen", type=["pdf", "jpg", "jpeg", "png"],
                            accept_multiple_files=True, key=f"{praefix}_testupload")
    if neue and st.button(f"{len(neue)} Datei(en) in „{ordnername}“ hochladen", icon=":material/upload:"):
        try:
            for datei in neue:
                beispielbeleg_hochladen(ordnername, datei.name, datei.getvalue(), datei.type)
        except Exception as e:
            st.error(f"Hochladen fehlgeschlagen: {e}")
        else:
            st.session_state[f"{praefix}_upload_ok"] = f"{len(neue)} Beispielbeleg(e) in „{ordnername}“ abgelegt."
            st.rerun(scope="fragment")  # nur das Fenster neu aufbauen, nicht schließen

    if not belege:
        return
    if st.button("Probelauf starten", type="primary", icon=":material/science:"):
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


def _offene_nachfragen() -> int | None:
    try:
        letzte = _letzte_entscheidungen(entscheidungen_laden())
        return sum(1 for n in nachfragen_laden()
                   if letzte.get(n.get("datei_sha256"), {}).get("status", "offen") == "offen")
    except Exception:
        return None


kopfzeile()
_offen = _offene_nachfragen()
tab_analyse, tab_nachfragen, tab_kategorien, tab_auswertung = st.tabs([
    ":material/upload_file: Beleg analysieren",
    ":material/help: Nachfragen" + (f" ({_offen} offen)" if _offen else ""),
    ":material/category: Kategorien",
    ":material/monitoring: Auswertung",
])

with tab_analyse:
    st.caption(
        "Lade einen oder mehrere Belege hoch. Die KI ordnet sie ein (Bewirtung, Tanken, Übernachtung, ...), "
        "liest die Daten ab, der Code rechnet nach. Unklare Belege landen automatisch unter „Nachfragen“. "
        "Danach bitte bewerten, was richtig erkannt wurde."
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

        if stapel_modus:
            if st.button(f"{min(len(uploads), MAX_STAPEL)} Belege analysieren", type="primary"):
                stapel_starten(uploads)
        elif st.button("Beleg analysieren", type="primary"):
            analyse_starten(uploads)  # zeigt den Fortschritt selbst an (st.status)

    if "fehler" in st.session_state:
        meldung, raw = st.session_state["fehler"]
        st.error(meldung)
        if raw:
            st.code(raw)

    if "stapel" in st.session_state:
        zeige_stapel(st.session_state["stapel"])
    elif "ergebnis" in st.session_state:
        zeige_ergebnis(st.session_state["ergebnis"])

with tab_nachfragen:
    zeige_nachfragen()

with tab_kategorien:
    zeige_kategorien()

with tab_auswertung:
    zeige_auswertung()

st.divider()
st.caption(
    f"Proof of Concept · Modell `{MODEL}` (liest doppelt) · Schiedsrichter `{MODEL_GROSS}` · "
    f"Prompt-Version `{PROMPT_VERSION}`"
)

