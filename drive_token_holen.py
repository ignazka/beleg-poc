"""Einmalig lokal ausführen: holt Google-Zugänge (Refresh-Tokens) für die App.

Voraussetzung: .streamlit/secrets.toml enthält [auth] client_id + client_secret, und beim OAuth-Client ist die
Weiterleitungs-URI http://localhost:8080/ eingetragen.

1) Drive-Zugang (Ablage der Belege):
       python drive_token_holen.py
   Im Browser mit dem Konto anmelden, das im Drive-Ordner Bearbeiter ist.
   -> [google_drive] refresh_token. Fragt zusätzlich "Kalender lesen" an: Ist der Firmenkalender für dieses
   Konto freigegeben, reicht dieser eine Zugang.

2) Eigener Kalender-Zugang (wenn der Kalender NICHT für das Drive-Konto freigegeben ist):
       python drive_token_holen.py --kalender
   Im Browser mit dem Konto anmelden, das den Firmenkalender sehen darf (z. B. Annika).
   -> [kalender] refresh_token. Dieser Zugang darf NUR Kalender lesen – kein Drive, keine Mails.

Tokens werden direkt in .streamlit/secrets.toml eingetragen (nie angezeigt). Für Streamlit Cloud danach die
Abschnitte [google_drive] bzw. [kalender] aus der Datei in die Cloud-Secrets kopieren.
"""

import re
import sys
import tomllib
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

KALENDER_LESEN = "https://www.googleapis.com/auth/calendar.readonly"
# identisch zu storage.GOOGLE_SCOPES: Drive (Ablage) + Kalender nur lesen
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive", KALENDER_LESEN]
SECRETS = Path(__file__).parent / ".streamlit" / "secrets.toml"


def _token_eintragen(token: str, abschnitt_name: str = "google_drive") -> None:
    """Ersetzt refresh_token im Abschnitt [abschnitt_name] bzw. ergänzt ihn dort."""
    text = SECRETS.read_text(encoding="utf-8")
    abschnitt = re.search(rf"(?ms)^\[{abschnitt_name}\][^\n]*\n(.*?)(?=^\[|\Z)", text)
    if not abschnitt:
        text = text.rstrip("\n") + f'\n\n[{abschnitt_name}]\nrefresh_token = "{token}"\n'
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
    assert tomllib.loads(text)[abschnitt_name]["refresh_token"] == token


def _anmelden(auth: dict, scopes: list[str], hinweis: str):
    flow = InstalledAppFlow.from_client_config(
        {
            "web": {
                "client_id": auth["client_id"],
                "client_secret": auth["client_secret"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=scopes,
    )
    print(hinweis)
    # prompt=consent erzwingt, dass Google wirklich einen Refresh-Token ausgibt
    creds = flow.run_local_server(port=8080, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        raise SystemExit("Google hat keinen Refresh-Token geliefert. Bitte erneut ausführen.")
    return creds


def _kalender_pruefen(creds, kalender_id: str | None) -> bool:
    if not kalender_id:
        print("Hinweis: [kalender] kalender_id fehlt noch in der secrets.toml – Kalender nicht geprüft.")
        return False
    try:
        cal = build("calendar", "v3", credentials=creds).calendars().get(calendarId=kalender_id).execute()
        print(f"OK: Kalender „{cal.get('summary', kalender_id)}“ lesbar.")
        return True
    except Exception as e:
        print(f"Kalender mit diesem Konto nicht lesbar ({e}).")
        return False


def main():
    secrets = tomllib.loads(SECRETS.read_text(encoding="utf-8"))
    auth = secrets["auth"]
    ordner_id = secrets.get("google_drive", {}).get("ordner_id")
    kalender_id = secrets.get("kalender", {}).get("kalender_id")

    if "--kalender" in sys.argv:
        creds = _anmelden(auth, [KALENDER_LESEN],
                          "Browser öffnet sich – mit dem Konto anmelden, das den Firmenkalender sehen darf ...")
        if not _kalender_pruefen(creds, kalender_id) and kalender_id:
            raise SystemExit("Token NICHT gespeichert: Dieses Konto kann den Kalender nicht lesen.")
        _token_eintragen(creds.refresh_token, "kalender")
        print(f"\nKalender-Token wurde in {SECRETS} unter [kalender] eingetragen. App im Browser neu laden.")
        return

    creds = _anmelden(auth, DRIVE_SCOPES,
                      "Browser öffnet sich – mit dem Konto anmelden, das im Drive-Ordner Bearbeiter ist ...")
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

    if kalender_id and not _kalender_pruefen(creds, kalender_id):
        print("-> Für den Kalender einen eigenen Zugang holen: python drive_token_holen.py --kalender "
              "(mit einem Konto, das den Kalender sehen darf), oder den Kalender für dieses Konto freigeben.")

    _token_eintragen(creds.refresh_token)
    print(f"\nrefresh_token wurde in {SECRETS} eingetragen. App im Browser neu laden.")


if __name__ == "__main__":
    main()
