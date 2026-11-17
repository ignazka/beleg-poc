# Beleg-Klassifizierung – PoC

Einfache Streamlit-Oberfläche: Mitarbeiter laden Belege hoch, ein Vision-LLM ordnet sie ein und
extrahiert die Daten, der Mitarbeiter gibt Feedback, was richtig erkannt wurde.

**Langfristiges Ziel:** ein Agentic RAG, das die Buchhaltung einer Firma unterstützt:
1. Belege verschiedener Arten klassifizieren (aktuell: Bewirtung)
2. Entwürfe in **sevDesk** anlegen
3. Firmenwissen (Kontierungsregeln, frühere Buchungen pro Lieferant) per RAG einbeziehen

Der aktuelle Stand ist bewusst nur **Schritt 1 als Testumgebung**: Wie gut erkennt das Modell Belege?

## Starten

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
# .env anlegen mit: OPENROUTER_API_KEY=sk-or-...
streamlit run App.py
python test_logik.py   # Tests ohne API-Aufruf
```

## Aufbau

| Datei | Zweck |
|---|---|
| `App.py` | Nur Oberfläche. Tabs „Beleg analysieren“ (Upload → Ergebnis → Feedback) und „Auswertung“ |
| `prompts.py` | System-Prompt + `PROMPT_VERSION` |
| `beleg_schema.py` | `KATEGORIEN`, `FEEDBACK_FELDER`, Pydantic-Modell `BelegAnalyse`, Parsing/Vergleich von Beträgen, Datum, Steuersätzen |
| `extraction.py` | `MODEL`, Bildaufbereitung (PDF → Bilder, EXIF-Drehung, max. 2000 px, JPEG), API-Call, JSON-Extraktion |
| `auth.py` | Google-Login über `st.login`, nur E-Mails aus `[zugang].erlaubte_emails`. Fail closed: ohne `[auth]` oder mit leerer Liste ist die App gesperrt |
| `storage.py` | Ein gemeinsamer Ordner für alle Benutzer. Mit `[google_drive]`: Drive-Ordner `belege/` + `feedback/` (eine JSON-Datei pro Bewertung). Sonst lokal `data/` (bzw. `BELEG_DATENORDNER`): `feedback.jsonl` + `belege/`, Rechte 0700/0600 |
| `drive_token_holen.py` | Einmalig lokal: Refresh-Token für den Drive-Zugriff erzeugen und Ordnerzugriff prüfen |
| `test_logik.py` | Assert-Tests für Parsing, Vergleich, JSON-Extraktion, Pydantic |

**Ablauf:** Upload → `datei_zu_bildern` → `analysieren` (OpenRouter) → `BelegAnalyse` →
Ergebnis in `st.session_state["ergebnis"]` → Feedback-Formular → `feedback_speichern`.

## Login (Google) und Ablage (Google Drive)

Secrets-Vorlage: `.streamlit/secrets.toml.example` (lokal nach `.streamlit/secrets.toml`, in Streamlit
Cloud unter Settings → Secrets). Lokal ohne Login entwickeln: `LOGIN_DEAKTIVIERT=1` in `.env`
(wirkt nur über die Umgebung, nie über Secrets).

- OAuth-Client (Typ „Webanwendung“) in der Google Cloud Console, Weiterleitungs-URIs
  `https://<app>.streamlit.app/oauth2callback` und `http://localhost:8501/oauth2callback`.
- Zusätzlich Weiterleitungs-URI `http://localhost:8080/` für `drive_token_holen.py`.
- **Ablage auf Streamlit Cloud = Google Drive**, weil das Dateisystem dort nicht dauerhaft ist (`data/` geht bei
  Neustart, Redeploy oder Schlafmodus verloren). Die App schreibt mit einem Refresh-Token des Ordner-Besitzers
  (kein Service-Account: der hat bei privaten Gmail-Konten kein Speicherkontingent). Zustimmungsbildschirm
  auf „In Produktion“, sonst läuft der Token nach 7 Tagen ab. Halb konfiguriertes oder nicht erreichbares
  Drive sperrt die App, statt still lokal zu speichern.

## Wichtige Entscheidungen

- **Modell:** `qwen/qwen3-vl-30b-a3b-instruct` über OpenRouter (offene Gewichte, später lokal
  betreibbar). Kosten ca. 0,0006 $ pro Beleg. Alternativen: `qwen3-vl-8b-instruct`, `qwen3-vl-235b-a22b-instruct`.
- **Datenschutz:** `extra_body={"provider": {"data_collection": "deny"}}`, damit nur Anbieter ohne
  Datenspeicherung/Training genutzt werden. `data/` enthält echte Belege → nie committen.
- **Session-State:** Streamlit führt das Skript bei jeder Eingabe neu aus. Das Ergebnis muss deshalb in
  `st.session_state` liegen, sonst verschwindet es beim ersten Klick im Feedback. Es wird verworfen,
  wenn sich der Datei-Hash ändert.
- **Feste Kategorien** (`KATEGORIEN` in `beleg_schema.py`). Unbekannte Werte vom Modell → `sonstiger_beleg`.
- **Keine doppelten Felder vom Modell:** Die Daten für den Bewirtungsbeleg leitet der Code
  ab (`BelegAnalyse.bewirtungsdaten()`).
- **Kein „Vertrauen“-Wert:** Vom LLM selbst geschätzte Werte sind nicht kalibriert.
- **JSON-Parsing:** erst `response_format=json_object` (mit Fallback ohne), dann direkt parsen,
  Codeblock oder erstes `{…}`. Danach tolerante Pydantic-Validierung.
- **Vergleich im Feedback** (`werte_gleich`): Beträge auf 0,005 genau, `0,00` = leer;
  Text ohne Groß-/Kleinschreibung und doppelte Leerzeichen; Datum in mehreren Formaten.
  Bei Steuersätzen ist das Komma ein **Trennzeichen** (`7,19` = 7 % und 19 %).
- `import pymupdf` statt `import fitz` (veraltet). In Streamlit `width="stretch"` statt
  `use_container_width` (veraltet).

## Konventionen

- Code, Bezeichner und Oberfläche auf **Deutsch**.
- **Bei jeder inhaltlichen Prompt-Änderung `PROMPT_VERSION` erhöhen**, damit die Auswertung
  Versionen vergleichen kann.
- Neue Feedback-Felder: in `FEEDBACK_FELDER` **und** in `BelegAnalyse` und im Prompt ergänzen.
- Feedback-Format nur erweitern, nicht umbenennen. Alte JSONL-Zeilen müssen lesbar bleiben.

## Stand (2026-11-17)

Erledigt:
- [x] Ergebnis bleibt im Session-State erhalten
- [x] Feste Kategorien, gültiger Prompt, robustes Parsing, Pydantic
- [x] Feedback pro Feld + Kategorie, Speicherung als JSONL, Auswertungs-Tab
- [x] Tests ohne API, `requirements.txt`, `.gitignore`

Nicht getestet: echter Upload in der laufenden App mit echten Fotos/PDFs (nur synthetischer Beleg per API).

## Nächste Schritte

1. **Rechenprüfungen im Code** (`checks.py`): Netto + MwSt. = Brutto (±0,02 €), Datum nicht in der
   Zukunft bzw. nicht zu alt, EC-Betrag minus Rechnung = Trinkgeld.
2. **Beträge pro Steuersatz** statt Summe: `[{satz, netto, steuer}]` (Speisen 7 %/19 %, Getränke 19 %).
   Wird für sevDesk sowieso gebraucht.
3. **Pflichtangaben für Bewirtung** (mit Steuerberater klären): TSE-Daten / maschinelle Erstellung,
   Name des Bewirtenden ab 250 €. **Anlass + Teilnehmer** manuell in der Oberfläche erfassen.
4. Mehrere Dateien auf einmal hochladen, HEIC-Support (Handyfotos), `st.cache_data` über Datei-Hash.
5. Weitere Belegarten (Reisekosten, Tanken, Büromaterial) → Feld `belegart`.
6. Datenmodell an **sevDesk-Voucher** ausrichten → Entwürfe per API anlegen.
7. RAG über Kontierungsregeln und frühere Buchungen. Das gesammelte Feedback ist der Testdatensatz.
