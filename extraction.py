"""Bild-Aufbereitung, Modell-Aufruf und robustes JSON-Parsing."""

import base64
import io
import json
import os
import re

import pymupdf
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image, ImageOps
from pydantic import ValidationError

from beleg_schema import BelegAnalyse
from prompts import SYSTEM_PROMPT, USER_PROMPT

load_dotenv()

# Modell (Open Weights – später lokal nutzbar)
MODEL = "qwen/qwen3-vl-30b-a3b-instruct"
MAX_PDF_SEITEN = 2
MAX_KANTE_PX = 2000  # größere Fotos werden verkleinert (spart Tokens, kaum Qualitätsverlust)

_client = None


def get_client():
    global _client
    if _client is None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY fehlt in der .env-Datei.")
        _client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key, timeout=120)
    return _client


class AnalyseFehler(Exception):
    """Fehler mit angehängter Rohantwort für die Anzeige."""

    def __init__(self, message, raw=None):
        super().__init__(message)
        self.raw = raw


def _bild_normalisieren(img: Image.Image) -> bytes:
    """EXIF-Drehung anwenden, nach RGB, auf MAX_KANTE_PX verkleinern, als JPEG kodieren."""
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((MAX_KANTE_PX, MAX_KANTE_PX))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def datei_zu_bildern(file_bytes: bytes, mime: str):
    """Liefert (liste_von_jpeg_bytes, seitenanzahl_gesamt)."""
    if mime == "application/pdf":
        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
        seiten = len(doc)
        bilder = []
        for i in range(min(seiten, MAX_PDF_SEITEN)):
            pix = doc[i].get_pixmap(dpi=150)
            bilder.append(_bild_normalisieren(Image.open(io.BytesIO(pix.tobytes("png")))))
        return bilder, seiten
    return [_bild_normalisieren(Image.open(io.BytesIO(file_bytes)))], 1


def _json_extrahieren(raw: str) -> dict:
    """Erst direkt parsen, dann Codeblock, dann erstes {...}-Objekt."""
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if block:
        try:
            return json.loads(block.group(1))
        except json.JSONDecodeError:
            pass
    start, ende = text.find("{"), text.rfind("}")
    if start != -1 and ende > start:
        return json.loads(text[start:ende + 1])
    raise json.JSONDecodeError("Kein JSON-Objekt gefunden", text, 0)


def _modell_aufrufen(content):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]
    kwargs = dict(
        model=MODEL,
        messages=messages,
        temperature=0.1,
        max_tokens=1500,
        # Keine Anbieter, die Daten speichern/zum Training nutzen (Belege enthalten Kartendaten)
        extra_body={"provider": {"data_collection": "deny"}},
    )
    client = get_client()
    try:
        return client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
    except Exception:
        # Fallback, falls der Anbieter json_object nicht unterstützt
        return client.chat.completions.create(**kwargs)


def analysieren(bilder: list[bytes]):
    """Ruft das Modell auf. Gibt (BelegAnalyse, rohantwort, usage_dict) zurück."""
    content = [{"type": "text", "text": USER_PROMPT}]
    for b in bilder:
        b64 = base64.b64encode(b).decode("utf-8")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    response = _modell_aufrufen(content)
    raw = (response.choices[0].message.content or "").strip()
    try:
        daten = _json_extrahieren(raw)
        analyse = BelegAnalyse.model_validate(daten)
    except (json.JSONDecodeError, ValidationError) as e:
        raise AnalyseFehler(f"Antwort des Modells konnte nicht gelesen werden: {e}", raw) from e

    usage = response.usage.model_dump() if response.usage else {}
    return analyse, raw, usage
