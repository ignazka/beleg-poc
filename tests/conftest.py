"""Gemeinsame Einstellungen für alle Tests (pytest).

Aufruf aus dem Projektordner:  python -m pytest
Kein Test braucht API-Schlüssel, Google Drive oder Internet.
"""
import os
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[1]
for pfad in (WURZEL, Path(__file__).parent):  # Projekt-Module + tests/attrappe.py importierbar
    if str(pfad) not in sys.path:
        sys.path.insert(0, str(pfad))
os.chdir(WURZEL)  # App.py findet assets/ relativ zum Arbeitsordner
os.environ["LOGIN_DEAKTIVIERT"] = "1"  # nur für Tests: Login überspringen
