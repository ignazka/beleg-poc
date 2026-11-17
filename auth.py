"""Google-Login über Streamlits eingebautes OIDC (st.login / st.user).

Sicher per Voreinstellung ("fail closed"):
- Ohne [auth]-Secrets startet die App NICHT, es sei denn, lokal ist LOGIN_DEAKTIVIERT=1 gesetzt (.env).
- Reinkommen nur E-Mails aus [zugang].erlaubte_emails. Leere oder fehlende Liste = niemand.
"""

import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def _secrets_abschnitt(name: str) -> dict:
    try:
        return dict(st.secrets.get(name, {}))
    except FileNotFoundError:  # keine secrets.toml vorhanden
        return {}


def _login_deaktiviert() -> bool:
    """Nur für die lokale Entwicklung. Wird ausschließlich aus der Umgebung/.env gelesen, nie aus Secrets."""
    return os.getenv("LOGIN_DEAKTIVIERT") == "1"


def _erlaubte_emails() -> set[str]:
    liste = _secrets_abschnitt("zugang").get("erlaubte_emails", [])
    return {e.strip().lower() for e in liste if e.strip()}


def login_erzwingen() -> None:
    """Stoppt das Skript, solange kein freigeschalteter Benutzer angemeldet ist."""
    if _login_deaktiviert():
        st.sidebar.warning("Login deaktiviert (LOGIN_DEAKTIVIERT=1) – nur lokal verwenden!")
        return

    if not _secrets_abschnitt("auth"):
        st.error("Login ist nicht konfiguriert ([auth] fehlt in den Secrets). Die App ist gesperrt.")
        st.stop()

    if not st.user.get("is_logged_in", False):
        st.title("Beleg-Klassifizierung – PoC")
        st.write("Bitte mit deinem Google-Konto anmelden.")
        st.button("Mit Google anmelden", type="primary", on_click=st.login)
        st.markdown("[Datenschutzerklärung](?seite=datenschutz)")
        st.stop()

    email = (st.user.get("email") or "").strip().lower()
    if not st.user.get("email_verified", False) or email not in _erlaubte_emails():
        st.error(f"Das Konto {email or '(ohne E-Mail)'} ist für diese App nicht freigeschaltet.")
        st.button("Abmelden", on_click=st.logout)
        st.stop()

    with st.sidebar:
        st.write(f"Angemeldet als **{st.user.get('name') or email}**")
        st.caption(email)
        st.button("Abmelden", on_click=st.logout)


def aktueller_benutzer() -> str | None:
    """E-Mail des angemeldeten Benutzers oder None (lokal ohne Login)."""
    if not _login_deaktiviert() and st.user.get("is_logged_in", False):
        return st.user.get("email")
    return None


def darf_kategorien_bearbeiten() -> bool:
    """[zugang].kategorien_bearbeiter = ["annika@..."] -> nur diese. Ohne Liste: alle angemeldeten Benutzer."""
    liste = {e.strip().lower() for e in _secrets_abschnitt("zugang").get("kategorien_bearbeiter", []) if e.strip()}
    if not liste:
        return True
    return (aktueller_benutzer() or "").lower() in liste
