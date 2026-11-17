"""Ablage von Belegen und Feedback – in EINEM gemeinsamen Ordner für alle Benutzer.

Google Drive (Streamlit Cloud): aktiv, sobald in den Secrets [google_drive] mit `ordner_id` und
`refresh_token` steht. Die App schreibt mit dem Konto des Ordner-Besitzers, nicht mit dem des Benutzers.

    <ordner>/belege/<sha256[:16]>.<ext>        Originalbelege (Duplikate werden nicht doppelt abgelegt)
    <ordner>/feedback/<zeitpunkt>_<sha16>.json  eine Datei pro Bewertung (Drive kann nicht anhängen)
    <ordner>/nachfragen/<zeitpunkt>_<sha16>.json  unsichere/unstimmige Belege, die ein Mensch klären muss

Lokal (ohne [google_drive]): <Projekt>/data bzw. BELEG_DATENORDNER, mit Rechten 0700/0600.
"""

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import streamlit as st

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
_ORDNER_MIME = "application/vnd.google-apps.folder"
_MIME_NACH_ENDUNG = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
# Funktioniert für "Meine Ablage" und für geteilte Ablagen (Shared Drives)
_ALLE_ABLAGEN = {"supportsAllDrives": True}
_ALLE_ABLAGEN_LISTE = {"supportsAllDrives": True, "includeItemsFromAllDrives": True}


class AblageFehler(RuntimeError):
    pass


def _secrets_abschnitt(name: str) -> dict:
    try:
        return dict(st.secrets.get(name, {}))
    except FileNotFoundError:  # keine secrets.toml vorhanden
        return {}


def datei_hash(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def _beleg_dateiname(dateiname: str, sha: str) -> str:
    endung = Path(dateiname).suffix.lower() or ".bin"
    return f"{sha[:16]}{endung}"


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

def _drive_config() -> dict | None:
    cfg = _secrets_abschnitt("google_drive")
    if not cfg:
        return None
    auth = _secrets_abschnitt("auth")
    # Client-Daten dürfen fehlen, dann wird der OAuth-Client aus [auth] mitbenutzt
    cfg.setdefault("client_id", auth.get("client_id"))
    cfg.setdefault("client_secret", auth.get("client_secret"))
    fehlend = [k for k in ("ordner_id", "refresh_token", "client_id", "client_secret") if not cfg.get(k)]
    if fehlend:
        # Halb konfiguriert -> lieber Fehler als still lokal speichern (Daten wären auf Cloud weg)
        raise AblageFehler(f"[google_drive] unvollständig, es fehlt: {', '.join(fehlend)}")
    return cfg


def ablage_beschreibung() -> str:
    try:
        return "Google Drive" if _drive_config() else "lokal"
    except AblageFehler:
        return "falsch konfiguriert"


def ablage_pruefen() -> None:
    """Beim Start aufrufen: wirft AblageFehler, wenn Drive konfiguriert, aber nicht erreichbar ist."""
    if _drive_config():
        _drive_ordner_pruefen()


# ---------------------------------------------------------------------------
# Google Drive
# ---------------------------------------------------------------------------

@st.cache_resource
def _drive_credentials(refresh_token: str, client_id: str, client_secret: str):
    """Pro Token-Kombination gecacht -> ein neuer refresh_token in den Secrets greift ohne Neustart."""
    from google.oauth2.credentials import Credentials

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=DRIVE_SCOPES,
    )


def _drive():
    # Pro Aufruf neu bauen: der HTTP-Client ist nicht thread-sicher, Streamlit-Sessions laufen parallel.
    from googleapiclient.discovery import build

    cfg = _drive_config()
    creds = _drive_credentials(cfg["refresh_token"], cfg["client_id"], cfg["client_secret"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)


@st.cache_data(ttl=3600, show_spinner=False)
def _drive_ordner_pruefen() -> None:
    from googleapiclient.errors import HttpError

    ordner_id = _drive_config()["ordner_id"]
    try:
        meta = _drive().files().get(fileId=ordner_id, fields="mimeType, trashed", **_ALLE_ABLAGEN).execute()
    except HttpError as e:
        raise AblageFehler(f"Drive-Ordner nicht erreichbar (ordner_id / Rechte prüfen): {e.reason}") from e
    except Exception as e:  # z. B. invalid_grant = Refresh-Token abgelaufen/widerrufen
        raise AblageFehler(f"Anmeldung bei Google Drive fehlgeschlagen (refresh_token erneuern): {e}") from e
    if meta.get("mimeType") != _ORDNER_MIME or meta.get("trashed"):
        raise AblageFehler("ordner_id zeigt nicht auf einen (nicht gelöschten) Ordner.")


@st.cache_data(ttl=3600, show_spinner=False)
def _unterordner_id(name: str) -> str:
    """ID des Unterordners im konfigurierten Drive-Ordner; wird bei Bedarf angelegt."""
    eltern = _drive_config()["ordner_id"]
    svc = _drive()
    q = f"'{eltern}' in parents and name = '{name}' and mimeType = '{_ORDNER_MIME}' and trashed = false"
    treffer = svc.files().list(q=q, fields="files(id)", pageSize=1, **_ALLE_ABLAGEN_LISTE).execute().get("files", [])
    if treffer:
        return treffer[0]["id"]
    neu = svc.files().create(
        body={"name": name, "mimeType": _ORDNER_MIME, "parents": [eltern]}, fields="id", **_ALLE_ABLAGEN
    ).execute()
    return neu["id"]


def _drive_hochladen(ordner_id: str, name: str, inhalt: bytes, mime: str) -> None:
    from googleapiclient.http import MediaInMemoryUpload

    _drive().files().create(
        body={"name": name, "parents": [ordner_id]},
        media_body=MediaInMemoryUpload(inhalt, mimetype=mime),
        fields="id",
        **_ALLE_ABLAGEN,
    ).execute()


def _drive_beleg_speichern(file_bytes: bytes, name: str) -> str:
    ordner = _unterordner_id("belege")
    q = f"'{ordner}' in parents and name = '{name}' and trashed = false"
    vorhanden = _drive().files().list(q=q, fields="files(id)", pageSize=1, **_ALLE_ABLAGEN_LISTE).execute()
    if not vorhanden.get("files"):
        mime = _MIME_NACH_ENDUNG.get(Path(name).suffix, "application/octet-stream")
        _drive_hochladen(ordner, name, file_bytes, mime)
    return f"belege/{name}"


@st.cache_data(show_spinner=False)
def _drive_json_laden(file_id: str) -> dict | None:
    """Feedback-Dateien werden nie geändert -> pro Datei-ID dauerhaft cachen, nur neue werden geladen."""
    try:
        return json.loads(_drive().files().get_media(fileId=file_id, **_ALLE_ABLAGEN).execute())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


@st.cache_data(ttl=300, show_spinner="Lade Daten aus Google Drive...")
def _drive_jsons_laden(ordnername: str) -> list[dict]:
    ordner = _unterordner_id(ordnername)
    svc = _drive()
    dateien, token = [], None
    while True:
        res = svc.files().list(
            q=f"'{ordner}' in parents and trashed = false",
            fields="nextPageToken, files(id, name)",
            orderBy="name",
            pageSize=1000,
            pageToken=token,
            **_ALLE_ABLAGEN_LISTE,
        ).execute()
        dateien += res.get("files", [])
        token = res.get("nextPageToken")
        if not token:
            break
    eintraege = [_drive_json_laden(d["id"]) for d in dateien]
    return [e for e in eintraege if e is not None]


# ---------------------------------------------------------------------------
# Lokal
# ---------------------------------------------------------------------------

def _lokaler_ordner() -> Path:
    pfad = os.getenv("BELEG_DATENORDNER")  # Secrets auf oberster Ebene landen bei Streamlit auch in os.environ
    return Path(pfad).expanduser().resolve() if pfad else Path(__file__).parent.resolve() / "data"


def _ordner_anlegen(ordner: Path) -> None:
    ordner.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(ordner, 0o700)  # unter Windows wirkungslos, auf Linux wirksam
    except OSError:
        pass


def _rechte_setzen(datei: Path) -> None:
    try:
        os.chmod(datei, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Öffentliche Funktionen (Backend wird automatisch gewählt)
# ---------------------------------------------------------------------------

def beleg_speichern(file_bytes: bytes, dateiname: str, sha: str) -> str:
    """Speichert die Originaldatei unter ihrem Hash (Duplikate werden nicht doppelt abgelegt)."""
    name = _beleg_dateiname(dateiname, sha)
    if _drive_config():
        return _drive_beleg_speichern(file_bytes, name)

    beleg_dir = _lokaler_ordner() / "belege"
    _ordner_anlegen(beleg_dir.parent)
    _ordner_anlegen(beleg_dir)
    ziel = beleg_dir / name
    if not ziel.exists():
        ziel.write_bytes(file_bytes)
        _rechte_setzen(ziel)
    return f"belege/{name}"


def _eintrag_speichern(art: str, eintrag: dict) -> None:
    """art = "feedback" | "nachfragen". Drive: eine JSON-Datei pro Eintrag, lokal: <art>.jsonl."""
    eintrag = {"zeitpunkt": datetime.now().isoformat(timespec="seconds"), **eintrag}
    zeile = json.dumps(eintrag, ensure_ascii=False)

    if _drive_config():
        sha16 = (eintrag.get("datei_sha256") or "unbekannt")[:16]
        name = f"{eintrag['zeitpunkt'].replace(':', '-')}_{sha16}.json"
        _drive_hochladen(_unterordner_id(art), name, zeile.encode("utf-8"), "application/json")
        _drive_jsons_laden.clear()  # Anzeige soll den neuen Eintrag sofort sehen
        return

    ordner = _lokaler_ordner()
    _ordner_anlegen(ordner)
    datei = ordner / f"{art}.jsonl"
    neu = not datei.exists()
    with datei.open("a", encoding="utf-8") as f:
        f.write(zeile + "\n")
    if neu:
        _rechte_setzen(datei)


def _eintraege_laden(art: str) -> list[dict]:
    if _drive_config():
        return _drive_jsons_laden(art)

    datei = _lokaler_ordner() / f"{art}.jsonl"
    if not datei.exists():
        return []
    eintraege = []
    with datei.open(encoding="utf-8") as f:
        for zeile in f:
            zeile = zeile.strip()
            if zeile:
                try:
                    eintraege.append(json.loads(zeile))
                except json.JSONDecodeError:
                    continue  # defekte Zeile überspringen statt alles zu blockieren
    return eintraege


def feedback_speichern(eintrag: dict) -> None:
    _eintrag_speichern("feedback", eintrag)


def feedback_laden() -> list[dict]:
    return _eintraege_laden("feedback")


def schon_bewertet(sha: str) -> bool:
    return any(e.get("datei_sha256") == sha for e in feedback_laden())


def nachfrage_speichern(eintrag: dict) -> bool:
    """Legt einen Beleg zur Nachfrage ab. False, wenn derselbe Beleg (Hash) dort schon liegt."""
    sha = eintrag.get("datei_sha256")
    if sha and any(e.get("datei_sha256") == sha for e in nachfragen_laden()):
        return False
    _eintrag_speichern("nachfragen", eintrag)
    return True


def nachfragen_laden() -> list[dict]:
    return _eintraege_laden("nachfragen")


# Entscheidungen zu Nachfragen: nur anhängen, nie überschreiben (Protokoll: wer hat wann was entschieden).
# Der aktuelle Status einer Nachfrage ist die jüngste Entscheidung zu ihrem Datei-Hash.

def entscheidung_speichern(eintrag: dict) -> None:
    _eintrag_speichern("entscheidungen", eintrag)


def entscheidungen_laden() -> list[dict]:
    return _eintraege_laden("entscheidungen")


# Kategorien: jede Änderung ist eine neue Version (nur anhängen). Lesbare Kopie als Google-Doc
# in "Anlage KI Assistent/<Kategorie>/" – dort, wo die Buchhaltung ihre Unterlagen bisher ablegt.

ANLAGE_ORDNER = "Anlage KI Assistent"
KATEGORIE_DOKUMENT = "Kategorie-Beschreibung (aus der App)"


def kategorie_speichern(version: dict) -> None:
    _eintrag_speichern("kategorien", version)


def kategorien_laden() -> list[dict]:
    return _eintraege_laden("kategorien")


def _q(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")


def _drive_ordner(eltern: str, name: str) -> str:
    svc = _drive()
    q = f"'{eltern}' in parents and name = '{_q(name)}' and mimeType = '{_ORDNER_MIME}' and trashed = false"
    treffer = svc.files().list(q=q, fields="files(id)", pageSize=1, **_ALLE_ABLAGEN_LISTE).execute().get("files")
    if treffer:
        return treffer[0]["id"]
    return svc.files().create(body={"name": name, "mimeType": _ORDNER_MIME, "parents": [eltern]},
                              fields="id", **_ALLE_ABLAGEN).execute()["id"]


def kategorie_dokument_ablegen(kategorie_name: str, text: str) -> str:
    """Legt/aktualisiert das lesbare Dokument der Kategorie. Gibt einen Link bzw. Pfad zurück."""
    if _drive_config():
        from googleapiclient.http import MediaInMemoryUpload

        ordner = _drive_ordner(_drive_ordner(_drive_config()["ordner_id"], ANLAGE_ORDNER), kategorie_name)
        svc = _drive()
        q = f"'{ordner}' in parents and name = '{_q(KATEGORIE_DOKUMENT)}' and trashed = false"
        treffer = svc.files().list(q=q, fields="files(id)", pageSize=1, **_ALLE_ABLAGEN_LISTE).execute().get("files")
        inhalt = MediaInMemoryUpload(text.encode("utf-8"), mimetype="text/plain")
        if treffer:
            datei = svc.files().update(fileId=treffer[0]["id"], media_body=inhalt, fields="id, webViewLink",
                                       **_ALLE_ABLAGEN).execute()
        else:  # text/plain -> wird von Drive in ein Google-Doc umgewandelt
            datei = svc.files().create(
                body={"name": KATEGORIE_DOKUMENT, "mimeType": "application/vnd.google-apps.document",
                      "parents": [ordner]},
                media_body=inhalt, fields="id, webViewLink", **_ALLE_ABLAGEN,
            ).execute()
        return datei.get("webViewLink", "")

    ordner = _lokaler_ordner() / "kategorien"
    _ordner_anlegen(ordner)
    ziel = ordner / f"{kategorie_name}.txt"
    ziel.write_text(text, encoding="utf-8")
    _rechte_setzen(ziel)
    return str(ziel)


def beleg_laden(pfad: str) -> bytes:
    """Originaldatei zu einem gespeicherten Pfad 'belege/<name>' laden."""
    name = pfad.split("/")[-1]
    if _drive_config():
        return _drive_beleg_laden(name)
    return (_lokaler_ordner() / "belege" / name).read_bytes()


@st.cache_data(show_spinner=False, max_entries=50)
def _drive_beleg_laden(name: str) -> bytes:
    ordner = _unterordner_id("belege")
    treffer = _drive().files().list(
        q=f"'{ordner}' in parents and name = '{name}' and trashed = false",
        fields="files(id)", pageSize=1, **_ALLE_ABLAGEN_LISTE,
    ).execute().get("files", [])
    if not treffer:
        raise FileNotFoundError(f"Beleg {name} nicht im Ordner „belege“ gefunden.")
    return _drive().files().get_media(fileId=treffer[0]["id"], **_ALLE_ABLAGEN).execute()
