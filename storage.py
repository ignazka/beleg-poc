"""Ablage von Belegen und Feedback (JSONL) – bewusst simpel, später durch DB ersetzbar."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
BELEG_DIR = DATA_DIR / "belege"
FEEDBACK_FILE = DATA_DIR / "feedback.jsonl"


def datei_hash(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def beleg_speichern(file_bytes: bytes, dateiname: str, sha: str) -> str:
    """Speichert die Originaldatei unter ihrem Hash (Duplikate werden nicht doppelt abgelegt)."""
    BELEG_DIR.mkdir(parents=True, exist_ok=True)
    endung = Path(dateiname).suffix.lower() or ".bin"
    ziel = BELEG_DIR / f"{sha[:16]}{endung}"
    if not ziel.exists():
        ziel.write_bytes(file_bytes)
    return str(ziel.relative_to(DATA_DIR.parent))


def feedback_speichern(eintrag: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    eintrag = {"zeitpunkt": datetime.now().isoformat(timespec="seconds"), **eintrag}
    with FEEDBACK_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")


def feedback_laden() -> list[dict]:
    if not FEEDBACK_FILE.exists():
        return []
    eintraege = []
    with FEEDBACK_FILE.open(encoding="utf-8") as f:
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
