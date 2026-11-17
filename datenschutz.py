"""Öffentliche Datenschutzseite (ohne Login), erreichbar unter <app-url>/?seite=datenschutz.

Google verlangt für den OAuth-Produktionsmodus eine öffentlich erreichbare Datenschutzerklärung.
Name und Kontakt kommen aus den Secrets [datenschutz] verantwortlicher / kontakt.
VORLAGE – vor echtem Einsatz rechtlich prüfen lassen.
"""

import streamlit as st


def _angabe(key: str) -> str:
    try:
        return st.secrets.get("datenschutz", {}).get(key) or "(in den Secrets unter [datenschutz] eintragen)"
    except FileNotFoundError:
        return "(in den Secrets unter [datenschutz] eintragen)"


def ist_datenschutzseite() -> bool:
    return st.query_params.get("seite") == "datenschutz"


def zeige_datenschutz() -> None:
    st.title("Datenschutzerklärung")
    st.markdown(f"""
**Verantwortlich:** {_angabe("verantwortlicher")}
**Kontakt:** {_angabe("kontakt")}

### Zweck der Anwendung
Diese Anwendung ist ein interner Prototyp zur automatischen Auswertung von Belegen (z. B. Bewirtungsbelegen).
Sie ist nur für freigeschaltete Personen nutzbar.

### Welche Daten verarbeitet werden
- **Anmeldung mit Google:** Name, E-Mail-Adresse und Google-Konto-ID. Sie dienen ausschließlich der Prüfung,
  ob die Person zugelassen ist, und der Zuordnung von Bewertungen.
- **Hochgeladene Belege:** Bilder bzw. PDFs und die daraus ausgelesenen Angaben (z. B. Betrag, Datum, Firma).
- **Bewertungen:** Korrekturen und Kommentare der Nutzer zu den Ergebnissen.

### Wo die Daten verarbeitet und gespeichert werden
- **Hosting:** Streamlit Community Cloud (Snowflake Inc.).
- **Auswertung der Belege:** Die Belegbilder werden über OpenRouter an ein KI-Modell übermittelt. Es werden nur
  Anbieter genutzt, die die Daten nicht speichern und nicht zum Training verwenden.
- **Speicherung:** Belege und Bewertungen werden in einem nicht öffentlichen Google-Drive-Ordner des
  Verantwortlichen abgelegt. Die Anwendung greift auf diesen Ordner zu; Nutzer erhalten keinen direkten Zugriff.

Es findet keine Weitergabe an Dritte zu Werbezwecken statt.

### Speicherdauer
Die Daten werden gespeichert, solange sie für die Erprobung der Anwendung benötigt werden, und danach gelöscht.

### Ihre Rechte
Sie haben das Recht auf Auskunft, Berichtigung, Löschung, Einschränkung der Verarbeitung, Widerspruch und
Datenübertragbarkeit sowie das Recht auf Beschwerde bei einer Datenschutz-Aufsichtsbehörde.
Wenden Sie sich dazu an den oben genannten Kontakt.

Den Zugriff der Anwendung auf Ihr Google-Konto können Sie jederzeit unter
[myaccount.google.com/permissions](https://myaccount.google.com/permissions) widerrufen.
""")
