"""Einmalig lokal ausführen: holt einen Refresh-Token, mit dem die App in den Drive-Ordner schreibt.

Voraussetzung: .streamlit/secrets.toml enthält [auth] client_id + client_secret und [google_drive] ordner_id,
und beim OAuth-Client ist die Weiterleitungs-URI http://localhost:8080/ eingetragen.

    python drive_token_holen.py

Im Browser mit dem Google-Konto anmelden, dem der Drive-Ordner gehört oder das dort Bearbeiter ist.
Der Token wird direkt in .streamlit/secrets.toml unter [google_drive] eingetragen (nicht angezeigt).
Für Streamlit Cloud danach den [google_drive]-Abschnitt aus der Datei in die Cloud-Secrets kopieren.
"""

import re
import tomllib
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]  # identisch zu storage.DRIVE_SCOPES
SECRETS = Path(__file__).parent / ".streamlit" / "secrets.toml"


def _token_eintragen(token: str) -> None:
    """Ersetzt refresh_token im Abschnitt [google_drive] bzw. ergänzt ihn dort."""
    text = SECRETS.read_text(encoding="utf-8")
    abschnitt = re.search(r"(?ms)^\[google_drive\][^\n]*\n(.*?)(?=^\[|\Z)", text)
    if not abschnitt:
        text = text.rstrip("\n") + f'\n\n[google_drive]\nrefresh_token = "{token}"\n'
    else:
        inhalt = abschnitt.group(1)
        zeile = f'refresh_token = "{token}"'
        if re.search(r"(?m)^\s*refresh_token\s*=.*$", inhalt):
            neu = re.sub(r"(?m)^\s*refresh_token\s*=.*$", lambda _: zeile, inhalt, count=1)
        else:
            neu = zeile + "\n" + inhalt
        text = text[: abschnitt.start(1)] + neu + text[abschnitt.end(1):]
    SECRETS.write_text(text, encoding="utf-8")
    # Gegenprobe: Datei muss weiterhin gültiges TOML sein und den neuen Token enthalten
    assert tomllib.loads(text)["google_drive"]["refresh_token"] == token


def main():
    secrets = tomllib.loads(SECRETS.read_text(encoding="utf-8"))
    auth = secrets["auth"]
    ordner_id = secrets.get("google_drive", {}).get("ordner_id")

    flow = InstalledAppFlow.from_client_config(
        {
            "web": {
                "client_id": auth["client_id"],
                "client_secret": auth["client_secret"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=DRIVE_SCOPES,
    )
    print("Browser öffnet sich – mit dem Konto anmelden, das im Drive-Ordner Bearbeiter ist ...")
    # prompt=consent erzwingt, dass Google wirklich einen Refresh-Token ausgibt
    creds = flow.run_local_server(port=8080, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        raise SystemExit("Google hat keinen Refresh-Token geliefert. Bitte erneut ausführen.")

    if ordner_id:
        meta = build("drive", "v3", credentials=creds).files().get(
            fileId=ordner_id, fields="name, driveId, capabilities(canAddChildren)", supportsAllDrives=True
        ).execute()
        art = "geteilte Ablage (Shared Drive)" if meta.get("driveId") else "„Meine Ablage“ des Besitzers"
        print(f"\nOrdner „{meta['name']}“ gefunden ({art}).")
        if meta.get("capabilities", {}).get("canAddChildren"):
            print("OK: Schreibrechte vorhanden.")
        else:
            print("ACHTUNG: Keine Schreibrechte – im Ordner als Bearbeiter freigeben lassen, sonst kann die App nicht speichern.")
    else:
        print("\nHinweis: [google_drive] ordner_id fehlt noch in der secrets.toml – Zugriff nicht geprüft.")

    _token_eintragen(creds.refresh_token)
    print(f"\nrefresh_token wurde in {SECRETS} eingetragen. App im Browser neu laden.")


if __name__ == "__main__":
    main()
