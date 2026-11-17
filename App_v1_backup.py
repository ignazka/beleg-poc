import streamlit as st
import os
import base64
import json
from openai import OpenAI
from PIL import Image
import io
import fitz  # pymupdf
from dotenv import load_dotenv

load_dotenv()

# OpenRouter Client
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

# Modell (Open Weights – später lokal nutzbar)
MODEL = "qwen/qwen3-vl-30b-a3b-instruct"  # ggf. exakte ID anpassen

SYSTEM_PROMPT = """Du bist ein Assistent für die deutsche Buchhaltung und verarbeitest Belege nach internen Regeln.

WICHTIGE UNTERSCHEIDUNG:
- Restaurant-Beleg / Quittung: Wird vom Restaurant ausgestellt und gilt als Rechnung. Diesen analysierst du.
- Bewirtungsbeleg: Wird intern von der Buchhaltung erstellt. Du erstellst KEINE Bewirtungsbelege, sondern lieferst die Daten dafür.

PRÜFUNGEN (bei Abweichungen immer hinweisen):
- Beleg muss lesbar sein
- Ausgewiesener Steuersatz muss erkennbar sein (7% und/oder 19%)
- Datum muss erkennbar sein
Bei fehlenden oder unklaren Angaben: klare Warnung ausgeben.

ZU EXTRAHIERENDE INFORMATIONEN aus dem Restaurant-Beleg:
- Belegnummer / Rechnungsnummer
- Name des Restaurants
- Datum des Belegs
- Nettobetrag
- Bruttobetrag
- Mehrwertsteuer / MwSt.
- Trinkgeld / Tip (falls vorhanden)
- Adresse des Restaurants (falls sichtbar)

SONDERFÄLLE:
- Es können Restaurant-Beleg und EC-Beleg zusammen vorliegen. Prüfe Übereinstimmung von Datum, Uhrzeit, Adresse und Restaurantname. Unterschiede beim Betrag können durch Trinkgeld entstehen.
- Bei unklaren oder fehlenden Pflichtangaben: kennzeichne es explizit.

AUSGABEFORMAT:
Antworte AUSSCHLIESSLICH mit validem JSON in diesem Format:

{
  "kategorie": "Restaurant-Beleg",
  "vertrauen": 0.95,
  "lesbar": true,
  "steuersatz_erkennbar": true,
  "datum_erkennbar": true,
  "belegnummer": "string oder null",
  "restaurant_name": "string oder null",
  "datum": "YYYY-MM-DD oder null",
  "netto": 0.00 oder null,
  "brutto": 0.00 oder null,
  "mwst": 0.00 oder null,
  "trinkgeld": 0.00 oder null,
  "adresse": "string oder null",
  "hinweise": ["Liste von Warnungen oder Besonderheiten"],
  "daten_fuer_bewirtungsbeleg": {
    "belegnummer": "",
    "restaurant_name": "",
    "brutto": 0.00,
    "mwst": 0.00,
    "trinkgeld": 0.00,
    "tag_der_bewirtung": ""
  }
}
"""

def pdf_to_images(pdf_bytes, max_pages=2):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []
    for i in range(min(len(doc), max_pages)):
        page = doc[i]
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        images.append(img_bytes)
    return images

def image_to_base64(image_bytes):
    return base64.b64encode(image_bytes).decode("utf-8")

st.set_page_config(page_title="Beleg-Klassifizierung PoC", layout="wide")
st.title("Beleg-Klassifizierung – PoC")
st.caption("Lade einen Restaurant-Beleg hoch. Der Agent extrahiert die Daten für den Bewirtungsbeleg und weist auf Probleme hin.")

uploaded_file = st.file_uploader(
    "Beleg hochladen (JPG, PNG, PDF)",
    type=["jpg", "jpeg", "png", "pdf"]
)

if uploaded_file and st.button("Beleg analysieren", type="primary"):
    with st.spinner("Analysiere Beleg..."):
        file_bytes = uploaded_file.read()
        images_b64 = []

        if uploaded_file.type == "application/pdf":
            images = pdf_to_images(file_bytes)
            for img in images:
                images_b64.append(image_to_base64(img))
        else:
            images_b64.append(image_to_base64(file_bytes))

        content = [{"type": "text", "text": "Analysiere diesen Beleg und extrahiere die Daten für den Bewirtungsbeleg."}]
        for b64 in images_b64:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"}
            })

        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content}
                ],
                temperature=0.1,
                max_tokens=1500,
            )

            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw)

            # --- Anzeige ---
            col1, col2 = st.columns([1, 2])

            with col1:
                st.subheader("Hochgeladener Beleg")
                if uploaded_file.type == "application/pdf":
                    st.info("PDF (erste Seite(n) werden analysiert)")
                    img = Image.open(io.BytesIO(pdf_to_images(file_bytes)[0]))
                    st.image(img, use_container_width=True)
                else:
                    st.image(file_bytes, use_container_width=True)

            with col2:
                st.subheader("Ergebnis")

                # Kategorie + Vertrauen
                c1, c2, c3 = st.columns(3)
                c1.metric("Kategorie", result.get("kategorie", "–"))
                c2.metric("Vertrauen", f"{result.get('vertrauen', 0)*100:.0f}%")
                c3.metric("Lesbar", "Ja" if result.get("lesbar") else "Nein")

                # Pflichtprüfungen
                st.write("**Prüfungen:**")
                checks = st.columns(3)
                checks[0].write(f"Steuersatz erkennbar: {'✅' if result.get('steuersatz_erkennbar') else '❌'}")
                checks[1].write(f"Datum erkennbar: {'✅' if result.get('datum_erkennbar') else '❌'}")
                checks[2].write("")

                # Hinweise (prominent)
                hinweise = result.get("hinweise", [])
                if hinweise:
                    st.warning("**Hinweise / Probleme:**")
                    for h in hinweise:
                        st.write(f"- {h}")
                else:
                    st.success("Keine besonderen Hinweise.")

                # Daten für Bewirtungsbeleg (prominent)
                st.subheader("Daten für Bewirtungsbeleg")
                daten = result.get("daten_fuer_bewirtungsbeleg", {})
                st.dataframe({
                    "Feld": ["Belegnummer", "Restaurant", "Brutto", "MwSt.", "Trinkgeld", "Tag der Bewirtung"],
                    "Wert": [
                        daten.get("belegnummer", "–"),
                        daten.get("restaurant_name", "–"),
                        daten.get("brutto", "–"),
                        daten.get("mwst", "–"),
                        daten.get("trinkgeld", "–"),
                        daten.get("tag_der_bewirtung", "–"),
                    ]
                }, use_container_width=True, hide_index=True)

                # Weitere extrahierte Daten
                with st.expander("Weitere extrahierte Daten"):
                    st.json({
                        "Belegnummer": result.get("belegnummer"),
                        "Restaurant": result.get("restaurant_name"),
                        "Datum": result.get("datum"),
                        "Netto": result.get("netto"),
                        "Brutto": result.get("brutto"),
                        "MwSt.": result.get("mwst"),
                        "Trinkgeld": result.get("trinkgeld"),
                        "Adresse": result.get("adresse"),
                    })

        except Exception as e:
            st.error(f"Fehler bei der Analyse: {e}")
            if 'raw' in locals():
                st.code(raw)

st.divider()
st.caption("Proof of Concept. Ist hier erstmal nur zum Testen der Klassifizierung")