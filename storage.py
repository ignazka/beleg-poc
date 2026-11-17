"""Ablage von Belegen und Feedback – in EINEM gemeinsamen Ordner für alle Benutzer.

Google Drive (Streamlit Cloud): aktiv, sobald in den Secrets [google_drive] mit `ordner_id` und
`refresh_token` steht. Die App schreibt mit dem Konto des Ordner-Besitzers, nicht mit dem des Benutzers.

    <ordner>/belege/<sha256[:16]>.<ext>        Originalbelege (Duplikate werden nicht doppelt abgelegt)
    <ordner>/feedback/<zeitpunkt>_<sha16>.json  eine Datei pro Bewertung (Drive kann nicht anhängen)

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


@st.cache_data(ttl=300, show_spinner="Lade Feedback aus Google Drive...")
def _drive_feedback_laden() -> list[dict]:
    ordner = _unterordner_id("feedback")
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


def feedback_speichern(eintrag: dict) -> None:
    eintrag = {"zeitpunkt": datetime.now().isoformat(timespec="seconds"), **eintrag}
    zeile = json.dumps(eintrag, ensure_ascii=False)

    if _drive_config():
        sha16 = (eintrag.get("datei_sha256") or "unbekannt")[:16]
        name = f"{eintrag['zeitpunkt'].replace(':', '-')}_{sha16}.json"
        _drive_hochladen(_unterordner_id("feedback"), name, zeile.encode("utf-8"), "application/json")
        _drive_feedback_laden.clear()  # Auswertung soll den neuen Eintrag sofort sehen
        return

    ordner = _lokaler_ordner()
    _ordner_anlegen(ordner)
    datei = ordner / "feedback.jsonl"
    neu = not datei.exists()
    with datei.open("a", encoding="utf-8") as f:
        f.write(zeile + "\n")
    if neu:
        _rechte_setzen(datei)


def feedback_laden() -> list[dict]:
    if _drive_config():
        return _drive_feedback_laden()

    datei = _lokaler_ordner() / "feedback.jsonl"
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


def schon_bewertet(sha: str) -> bool:
    return any(e.get("datei_sha256") == sha for e in feedback_laden())
