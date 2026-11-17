"""Einmalig lokal ausführen: holt einen Refresh-Token, mit dem die App in den Drive-Ordner schreibt.

Voraussetzung: .streamlit/secrets.toml enthält [auth] client_id + client_secret und [google_drive] ordner_id,
und beim OAuth-Client ist die Weiterleitungs-URI http://localhost:8080/ eingetragen.

    python drive_token_holen.py

Im Browser mit dem Google-Konto anmelden, dem der Drive-Ordner gehört (oder das Bearbeiter-Rechte hat).
Den ausgegebenen refresh_token in die Secrets unter [google_drive] eintragen (lokal und in Streamlit Cloud).
"""

import tomllib
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]  # identisch zu storage.DRIVE_SCOPES
SECRETS = Path(__file__).parent / ".streamlit" / "secrets.toml"


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
    # prompt=consent erzwingt, dass Google wirklich einen Refresh-Token ausgibt
    creds = flow.run_local_server(port=8080, access_type="offline", prompt="consent")

    if ordner_id:
        meta = build("drive", "v3", credentials=creds).files().get(
            fileId=ordner_id, fields="name, mimeType", supportsAllDrives=True
        ).execute()
        print(f"\nOK: Zugriff auf Ordner „{meta['name']}“ funktioniert.")
    else:
        print("\nHinweis: [google_drive] ordner_id fehlt noch in der secrets.toml – Zugriff nicht geprüft.")

    print("\nIn die Secrets unter [google_drive] eintragen:\n")
    print(f'refresh_token = "{creds.refresh_token}"')


if __name__ == "__main__":
    main()
