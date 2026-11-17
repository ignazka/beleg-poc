"""Kategorien: in der App anlegen, bearbeiten, löschen – und daraus den Prompt bauen.

- Jede Änderung ist eine neue VERSION (nur anhängen, nie überschreiben) -> nachvollziehbar, wer was wann
  geändert hat; Löschen = Version mit Status "geloescht".
- Standard-Kategorien (beleg_schema.STANDARD_KATEGORIEN) sind im Code verankert (Rechenprüfungen,
  Bewirtungslogik). Ihre Beschreibung lässt sich ergänzen, löschen kann man sie nicht.
- Status "entwurf": gespeichert, aber noch NICHT im Prompt. "aktiv": fließt in jede Analyse ein.
- Jede aktive Kategorie wird zusätzlich als lesbares Google-Doc in
  "Anlage KI Assistent/<Name>/" abgelegt (Ordner wie bisher bei der Buchhaltung).
"""

import hashlib
import json
import re
import unicodedata

from beleg_schema import STANDARD_KATEGORIEN

# Beschreibungen der Standard-Kategorien, solange niemand sie in der App ergänzt hat
STANDARD_BESCHREIBUNG = {
    "restaurant_beleg": "Vom Restaurant ausgestellte Rechnung/Quittung mit Positionen und MwSt.",
    "restaurant_mit_ec_beleg": "Restaurantrechnung zusammen mit EC-/Kartenbeleg. Differenz = Trinkgeld.",
    "nur_ec_beleg": "Nur Zahlungsnachweis vom Kartenterminal, keine Rechnung.",
    "bewirtungsbeleg_intern": "Von der Firma ausgefülltes Bewirtungsformular mit Anlass und Teilnehmern.",
    "tankbeleg": "Tankstelle mit Kraftstoff (Diesel, Super, AdBlue, Laden; Liter, €/l). Benötigt: Datum, "
                 "Betrag, Steuersatz. Fahrzeug und Mitarbeiter sind nicht nötig.",
    "uebernachtung": "Hotel, Pension, Ferienwohnung mit Übernachtung(en). Mitarbeitername und Reiseanlass "
                     "sind Pflicht; Namen nie raten.",
    "sonstiger_beleg": "Alles, was keiner anderen Kategorie zugeordnet werden kann.",
    "unlesbar": "Nicht lesbar oder kein Beleg.",
}

FELDER = ("beschreibung", "erkennungsmerkmale", "abgrenzung", "pflichtangaben", "nachfragen_wenn")
FELD_LABEL = {
    "beschreibung": "Beschreibung der Buchhaltung (Freitext)",
    "erkennungsmerkmale": "Woran erkennt man die Kategorie?",
    "abgrenzung": "Abgrenzung zu anderen Kategorien",
    "pflichtangaben": "Pflichtangaben auf dem Beleg",
    "nachfragen_wenn": "Wann muss nachgefragt werden?",
}


def schluessel_aus_name(name: str) -> str:
    """'Reisekosten Bahn & Flug' -> 'reisekosten_bahn_flug'"""
    s = unicodedata.normalize("NFKD", name.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
                              .replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue").replace("ß", "ss"))
    s = re.sub(r"[^a-z0-9]+", "_", s.encode("ascii", "ignore").decode().lower()).strip("_")
    return s[:40] or "kategorie"


def aktueller_stand(versionen: list[dict]) -> dict[str, dict]:
    """Alle Versionen -> {schluessel: jüngste Version}. Standard-Kategorien sind immer dabei."""
    stand = {
        key: {"schluessel": key, "name": label, "standard": True, "status": "aktiv", "version": 0,
              "beschreibung": STANDARD_BESCHREIBUNG.get(key, "")}
        for key, label in STANDARD_KATEGORIEN.items()
    }
    for v in sorted(versionen, key=lambda v: (v.get("zeitpunkt") or "", v.get("version") or 0)):
        key = v.get("schluessel")
        if not key:
            continue
        basis = stand.get(key, {})
        stand[key] = {**basis, **v, "standard": key in STANDARD_KATEGORIEN}
        if key in STANDARD_KATEGORIEN:  # Standard: Name und Status bleiben fest
            stand[key]["name"] = STANDARD_KATEGORIEN[key]
            stand[key]["status"] = "aktiv"
    return stand


def sichtbar(stand: dict[str, dict]) -> dict[str, dict]:
    return {k: v for k, v in stand.items() if v.get("status") != "geloescht"}


def aktiv(stand: dict[str, dict]) -> dict[str, dict]:
    return {k: v for k, v in stand.items() if v.get("status") == "aktiv"}


def labels(stand: dict[str, dict]) -> dict[str, str]:
    """{schluessel: Name} der aktiven Kategorien – für Validierung und Anzeige."""
    return {k: v["name"] for k, v in aktiv(stand).items()}


def _als_text(wert) -> str:
    if isinstance(wert, list):
        return "; ".join(str(x) for x in wert if str(x).strip())
    return str(wert or "").strip()


def prompt_abschnitt(stand: dict[str, dict]) -> str:
    """Kategorie-Wissen für den System-Prompt (nur aktive Kategorien)."""
    bloecke = []
    for key, k in aktiv(stand).items():
        zeilen = [f'- "{key}": {k["name"]}']
        for feld in FELDER:
            text = _als_text(k.get(feld))
            if text:
                zeilen.append(f"    {FELD_LABEL[feld]}: {text}")
        bloecke.append("\n".join(zeilen))
    return "\n".join(bloecke)


def wissens_hash(stand: dict[str, dict]) -> str:
    """Kurzer Fingerabdruck des Kategorie-Wissens -> Teil der Prompt-Version (Auswertung vorher/nachher)."""
    roh = json.dumps({k: {f: _als_text(v.get(f)) for f in ("name",) + FELDER} for k, v in aktiv(stand).items()},
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(roh.encode()).hexdigest()[:6]


def als_dokument(k: dict) -> str:
    """Lesbarer Text für das Google-Doc im Drive-Ordner der Kategorie."""
    teile = [k["name"], "", f"Schlüssel: {k['schluessel']} · Status: {k.get('status')} · "
             f"Version {k.get('version')} · zuletzt geändert von {k.get('bearbeiter') or '–'}", ""]
    for feld in FELDER:
        text = _als_text(k.get(feld))
        if text:
            teile += [FELD_LABEL[feld], text, ""]
    teile.append("Diese Datei wird von der Beleg-App erzeugt. Änderungen bitte in der App vornehmen "
                 "(Tab „Kategorien“), sonst werden sie beim nächsten Speichern überschrieben.")
    return "\n".join(teile)


# ---------------------------------------------------------------------------
# KI-Assistent: Freitext der Buchhaltung -> klare Regeln + Rückfragen
# ---------------------------------------------------------------------------

ASSISTENT_PROMPT = """Du hilfst der Buchhaltung, eine Belegkategorie für eine Beleg-KI präzise zu beschreiben.
Du bekommst den Freitext der Buchhaltung und die übrigen Kategorien. Formuliere daraus klare Regeln.

REGELN
- Nur aus dem Freitext und allgemein bekanntem Wissen über Belege ableiten. Keine Firmenregeln erfinden.
- Was im Freitext unklar oder widersprüchlich ist oder sich mit einer anderen Kategorie überschneidet:
  NICHT entscheiden, sondern als Rückfrage an die Buchhaltung formulieren.
- Rückfragen betreffen die REGEL, nicht einzelne Belege. Gut: "Gehört eine Hotelrechnung zu Reisekosten oder
  zu Übernachtung?" Schlecht: "Ist auf diesem Beleg ein Name lesbar?"
- Pflichtangaben: nur Angaben, die auf dem Beleg selbst stehen müssen (keine Arbeitsschritte wie "Konto").
- Kurz und konkret, Deutsch, Stichpunkte durch "; " getrennt.

Antworte NUR mit JSON:
{"erkennungsmerkmale": "...", "abgrenzung": "...", "pflichtangaben": ["..."], "nachfragen_wenn": "...",
 "rueckfragen": ["..."]}"""


def assistent_nachricht(name: str, freitext: str, andere: dict[str, dict]) -> str:
    uebrige = "\n".join(f"- {k['name']}: {_als_text(k.get('beschreibung'))[:200]}"
                        for k in andere.values())
    return f"Kategorie: {name}\n\nFreitext der Buchhaltung:\n{freitext}\n\nÜbrige Kategorien:\n{uebrige}"
