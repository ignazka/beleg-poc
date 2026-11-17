# Prüfablauf: Wie ein Beleg gelesen, gerechnet und geprüft wird

Dieses Dokument beschreibt Schritt für Schritt, was mit einem hochgeladenen Beleg passiert, wo gerechnet
wird und wann ein Beleg zur Nachfrage an einen Menschen geht. Grundregel aus dem Leitfaden der
Buchhaltung: **Bei fehlenden oder unklaren Angaben nie raten.**

Kurzfassung:

1. Die KI **liest nur ab**. Sie rechnet nichts.
2. Der Beleg wird **zweimal gelesen**, beide Lesungen werden verglichen.
3. **Alle Rechnungen macht der Code**, auf den Cent genau.
4. Der Code **prüft gegen**: Passen die Zahlen auf dem Beleg zueinander?
5. Bei jedem Zweifel liest ein **größeres Modell** nach. Bleibt der Zweifel, geht der Beleg zur **Nachfrage**.

---

## 1. Upload und Bildaufbereitung

Datei: `extraction.py` (`dateien_zu_bildern`)

- Erlaubt: JPG, PNG, PDF. Bei mehreren Dateien fragt die App, wie sie gelesen werden sollen:
  - **Stapel** (Standard): Jede Datei ist ein eigener Beleg. Bis zu 30 Belege, 3 gleichzeitig, jeder mit
    dem vollen Ablauf. Ergebnis: Übersicht (stimmig / Nachfrage / Fehler, farbig), CSV-Download,
    jeder Beleg im Detail ansehbar und bewertbar. Unklare Belege landen automatisch unter „Nachfragen“.
  - **Ein Beleg**: Alle Dateien gehören zusammen, z. B. Rechnung + EC-Beleg oder mehrere Fotos einer
    langen Rechnung.
- PDFs werden **vollständig** gelesen, bis 20 Seiten insgesamt. Mehr Seiten → Nachfrage.
- Fotos werden automatisch gedreht (Handy-Ausrichtung) und auf höchstens 2000 Pixel verkleinert
  (ab 6 Seiten 1600 Pixel). PDF-Seiten werden mit 150 dpi in Bilder umgewandelt.

## 2. Ablesen durch die KI (doppelt)

Dateien: `pipeline.py` (`beleg_lesen`), `prompts.py`

Der Beleg wird **zweimal gleichzeitig** gelesen, mit dem Modell `qwen3-vl-30b-a3b`:

| Lesung | Bild |
|---|---|
| 1 | Original |
| 2 | Graustufen mit verstärktem Kontrast und leicht geschärft (`kontrast_variante`) |

Die zweite Lesung bekommt absichtlich ein etwas anderes Bild. So sind Lesefehler der beiden Durchläufe
voneinander unabhängiger. Ein „3“ statt „8“ passiert dann selten zweimal gleich.

**Was die KI abliest**, genau wie gedruckt, ohne zu rechnen:

- Belegart (Bewirtung, Tankbeleg, Übernachtung, Sonstiges, Unlesbar) und ob sie dabei unsicher ist
- Belegnummer, Aussteller, Adresse, Datum, Uhrzeit
- **Gesamtbetrag der Rechnung** (`brutto`): die Zeile „Summe“, „Total“, „Gesamtbetrag“, **vor** einem
  Trinkgeld. Überträge und Zwischensummen sind ausdrücklich ausgeschlossen.
- **EC-/Kartenbetrag** (`ec_betrag`): der tatsächlich gezahlte Betrag
- **MwSt.-Aufstellung** (`steuerzeilen`): eine Zeile pro Steuersatz mit Satz, Netto, MwSt. und Brutto,
  so wie gedruckt
- **Trinkgeld**: nur, wenn es auf dem Beleg steht. Nie aus einer Differenz berechnet.
- Ausland ja/nein und ob der EC-Beleg zur Rechnung passt (Datum, Uhrzeit, Adresse, Name)

Die KI bildet **keine Summen und keine Differenzen**. Das war die Ursache der ersten Fehler: Beim
Domhof-Beleg hatte sie die MwSt. falsch addiert (11,40 statt 11,35 €) und daraus einen falschen
Gesamtbetrag gebaut.

## 3. Rechnen im Code (cent-genau)

Datei: `beleg_schema.py` (`summe`, `differenz`, `mwst_aus_netto`, `mwst_aus_brutto`, `BelegAnalyse._ableiten`)

Alle Beträge werden als **Dezimalzahlen** gerechnet, nicht als Computer-Fließkommazahlen. Gerundet wird
kaufmännisch auf den Cent. So entstehen keine Rundungsfehler wie 0,1 + 0,2 = 0,30000000000000004.

| Wert | Formel |
|---|---|
| MwSt. gesamt | Summe der MwSt. aller Steuerzeilen |
| Netto gesamt | Summe der Netto-Beträge aller Steuerzeilen (fehlt Netto: Brutto − MwSt. der Zeile) |
| Trinkgeld | EC-Betrag − Rechnungsbetrag (nur wenn größer als 0) |
| Gesamtbetrag inkl. Trinkgeld | EC-Betrag, sonst Rechnungsbetrag + aufgedrucktes Trinkgeld |
| MwSt. aus Netto | Netto × Satz / 100, z. B. 23,45 × 19 % = 4,46 |
| MwSt. aus Brutto | Brutto × Satz / (100 + Satz), z. B. 20,00 × 19/119 = 3,19 |

### Typische Ablesefehler, die der Code selbst auflöst

Datei: `beleg_schema.py` (`_summen_abgleichen`)

Zwei Fehler kamen bei den Beispielbelegen mehrfach vor. Der Code löst sie auf, **aber nur, wenn das
Ergebnis danach auf den Cent zum gedruckten Gesamtbetrag passt**. Sonst bleibt alles, wie es abgelesen
wurde, und die Rechenprüfung schlägt an (→ Nachfrage). Jede Umdeutung wird in der App angezeigt
(„Vom Code umgedeutet“) und gespeichert.

**a) Brutto-Spalte als Netto gelesen.** Kassenbons drucken pro Steuersatz mal Netto, mal Brutto.

> Abgelesen: 19 %, „Netto“ 20,00 €, MwSt. 3,19 €.
> Als Netto passt das nicht: 20,00 × 19 % = 3,80 €.
> Als Brutto passt es: 20,00 × 19/119 = 3,19 €. → Netto ist 16,81 €.
> Übernommen, weil alle Zeilen danach zusammen genau den Gesamtbetrag 26,50 € ergeben.

**b) Gesamtbetrag inklusive Trinkgeld gelesen.**

> Beleg: „Summe 66,80“, „Trinkgeld 3,20“, „Kreditkarte 70,00“. Abgelesen als Gesamtbetrag: 70,00 €.
> Steuerzeilen: 52,70 + 14,10 = 66,80 €. Plus aufgedrucktes Trinkgeld 3,20 € = 70,00 €.
> → Rechnungsbetrag ist 66,80 €, Trinkgeld 3,20 €.
> Ohne aufgedrucktes Trinkgeld wird **nicht** geraten. Dann geht der Beleg zur Nachfrage.

## 4. Rechenprüfungen (Gegenprobe)

Datei: `checks.py` (`pruefen`)

Ein falsch gelesener Betrag passt fast nie zu den anderen Zahlen auf dem Beleg. Genau das wird geprüft:

| Prüfung | Erlaubte Abweichung | Fängt ab |
|---|---|---|
| Summe der Steuerzeilen = Gesamtbetrag | 2 Cent | falsch gelesener Gesamtbetrag, Übertrag/Zwischensumme statt Summe, vergessene Zeile |
| MwSt. jeder Zeile = Netto × Satz | 3 Cent | falsch gelesene Ziffer in Netto oder MwSt. |
| Netto + MwSt. = Brutto je Zeile | 2 Cent | falsch gelesene Ziffer |
| Jeder Steuersatz nur einmal | – | doppelt oder erfunden gelesene Zeile |
| EC-Betrag ≥ Rechnung | 2 Cent | falscher EC-Beleg, Teilzahlung |
| Trinkgeld ≤ 30 % der Rechnung | – | EC-Beleg gehört zu einer anderen Rechnung |
| Datum nicht in der Zukunft | – | falsch gelesenes Datum |

**Warum ein paar Cent Toleranz?** Viele Kassen runden die MwSt. pro Position und nicht auf die Summe.
Dadurch weicht die Summe der Steuern um 1 bis 2 Cent vom „idealen“ Wert ab, obwohl der Beleg korrekt ist.
Alles über dieser Toleranz gilt als Ablesefehler. Die Toleranzen stehen an einer Stelle:
`TOLERANZ_SUMME` und `TOLERANZ_ZEILE` in `beleg_schema.py`.

## 5. Vergleich der beiden Lesungen

Datei: `checks.py` (`abweichungen`)

Verglichen werden: Belegart, Belegnummer, Aussteller, Datum, Gesamtbetrag, EC-Betrag und die
MwSt.-Aufstellung. Groß-/Kleinschreibung, Leer- und Satzzeichen zählen nicht. Beim Namen gilt
„Aral“ = „ARAL Tankstelle Speyer“ (der eine Name ist im anderen enthalten).

## 6. Schiedsrichter: größeres Modell bei Zweifel

Datei: `pipeline.py`

Der Schiedsrichter liest, wenn

- die beiden Lesungen in mindestens einem Feld abweichen, **oder**
- eine Prüfung fehlschlägt bzw. ein anderer Nachfragegrund vorliegt.

Er liest in **höherer Auflösung** (2800 Pixel, PDFs mit 220 dpi), damit kleine Kassenbon-Schrift besser
lesbar ist. **Sein Ergebnis gilt.** Ein abweichendes Feld gilt als geklärt, wenn der Schiedsrichter einer
der beiden ersten Lesungen zustimmt. Danach laufen alle Prüfungen erneut.

| Umgebung | Schiedsrichter |
|---|---|
| Jetzt (Cloud, OpenRouter) | `qwen3-vl-235b-a22b` |
| Später lokal (RTX 3090, 24 GB) | dasselbe 30B-Modell in höherer Auflösung (`SCHIEDSRICHTER_MODELL` setzen); das 235B-Modell passt nicht in 24 GB, und auf einer Karte ist immer nur ein Modell geladen |

## 7. Wann ein Beleg zur Nachfrage geht

Datei: `checks.py` (`nachfragen_gruende`)

Der Beleg wird **sofort** im Drive-Ordner `nachfragen/` abgelegt (Original in `belege/`), auch ohne
Feedback. Gründe:

- unlesbar
- Belegart unsicher (mit Begründung der KI) oder keiner Kategorie zuzuordnen
- nur EC-Beleg, keine Rechnung
- **Auslandsbeleg** (abweichende Handhabe laut Buchhaltung)
- EC-Beleg passt nicht zur Rechnung (Datum, Uhrzeit, Adresse, Name)
- kein Steuersatz ausgewiesen, oder ein anderer als 7 % / 19 %
- Gesamtbetrag oder Datum fehlt
- eine Rechenprüfung fehlgeschlagen (nach den Umdeutungen aus Abschnitt 3)
- die beiden Lesungen weichen ab und auch der Schiedsrichter klärt es nicht
- mehr als 20 Seiten
- **kein passender Kalendertermin** zu einer Bewirtung oder Übernachtung (siehe Abschnitt 7a), oder der
  Kalender ist nicht erreichbar

## 7a. Abgleich mit dem Firmenkalender

Datei: `kalender.py` (Regeln), `storage.py` (`kalender_abgleichen`)

Zu jeder **Bewirtung** und **Übernachtung** muss es einen passenden Termin im Google-Kalender der Firma geben.

| Belegart | passend, wenn … |
|---|---|
| Bewirtung | ein Termin am Belegdatum zeitlich zur Zahlung passt (beginnt höchstens 5 Std. vorher, endet frühestens 1 Std. vorher) **oder** Restaurantname bzw. Stadt in Titel, Ort oder Beschreibung steht |
| Übernachtung | ein Termin rund um das Rechnungsdatum (bis 1 Tag davor) Hotel oder Stadt nennt, **oder** genau eine mehrtägige Reise über das Datum läuft |

- Gefunden: Die App zeigt den Termin und schlägt dessen **Teilnehmer** als „bewirtete Personen“ vor.
- Nicht gefunden: **Nachfrage**, mit den Terminen im Zeitraum als Hilfe („vielleicht ist einer davon gemeint“).
- Noch nicht eingerichtet: nur ein Hinweis, keine Nachfrage.

Einrichtung: `kalender_id` unter `[kalender]` in den Secrets. Zugriff entweder über den Drive-Zugang (wenn der
Kalender für dieses Konto freigegeben ist) oder über einen eigenen Zugang mit Kalender-Leserecht:
`python drive_token_holen.py --kalender`, angemeldet mit einem Konto, das den Kalender sehen darf (z. B. Annika).

## 8. Nachfragen bearbeiten (Tab „Nachfragen“)

Datei: `App.py` (`zeige_nachfragen`, `_nachfrage_bearbeiten`), Ablage `storage.py` (`entscheidung_speichern`)

Im Moment geht es ums **Sammeln von Lernmaterial**: Jede Nachfrage wird korrigiert, begründet und
gespeichert. Eine Auswahl wie Freigeben/Zurückstellen/Verwerfen gibt es (noch) nicht.

1. Liste filtern: **Offen**, **Korrigiert** oder **Alle**. Die neuesten stehen oben.
2. Nachfrage auswählen. Oben stehen die Gründe der KI, links das Original (alle Seiten), rechts die von der
   KI gelesenen Werte als Formular.
3. Werte mit dem Beleg vergleichen und nur Falsches korrigieren. Die **Belegart** wählt man aus der Liste der
   Standard- und selbst angelegten Kategorien; noch nicht aktivierte Kategorien stehen als „(Entwurf)“ dabei,
   gelöschte nicht. (Entwürfe fließen weiterhin nicht in den KI-Prompt ein, siehe Abschnitt 9.)
4. **Begründung (Pflicht)**: Warum lag die KI falsch – oder warum war ihre Nachfrage unnötig?
5. **„Korrigieren“** speichert. Danach erscheint oben die **Gegenüberstellung**: Feld, *Vorher (KI)*,
   *Nachher (korrigiert)*; geänderte Zeilen rot, unveränderte grün. Unter „Korrigiert“ bleibt sie für jeden
   erledigten Beleg sichtbar, „Noch einmal korrigieren“ legt eine neue Korrektur an.

Jede Korrektur ist ein **eigener Eintrag** (`entscheidungen/`) mit Person, Zeitpunkt, Begründung, den
Gründen der KI und je Feld vorher/nachher. Nichts wird überschrieben (Stufe 1 des Leitfadens: Annika prüft
und dokumentiert jede Korrektur). Einträge aus der ersten Version (Freigeben/Zurückstellen/Verwerfen)
zählen als erledigt.

Jede Korrektur **zählt zusätzlich als Feedback** (mit den Gründen der KI und der Begründung). Sie geht in
die Trefferquote der Auswertung ein. Gerade die schwierigen Belege werden so zum Testmaterial und später zur
Grundlage, die Anweisungen an die KI zu verbessern.

## 9. Kategorien (Tab „Kategorien“) und wie die KI daraus lernt

Dateien: `kategorien.py`, `prompts.py` (`system_prompt`, `prompt_version`), `App.py` (`zeige_kategorien`)

- **Anlegen, bearbeiten, löschen** in der App. Standard-Kategorien (Bewirtung, Tankbeleg, Übernachtung, …)
  sind im Code verankert: Ihre Beschreibung lässt sich ergänzen, löschen kann man sie nicht.
- **Beschreibung als Freitext**, so wie man es einem neuen Kollegen erklären würde.
- **KI-Assistent** (Schaltfläche, großes Modell): Er übersetzt den Freitext in klare Regeln
  (Erkennungsmerkmale, Abgrenzung, Pflichtangaben, wann nachfragen). Was unklar ist oder sich mit einer
  anderen Kategorie überschneidet, stellt er als **Rückfrage an die Buchhaltung**, statt zu raten. Sein
  Ergebnis ist nur ein Vorschlag. Erst „Speichern“ übernimmt ihn.
- **Status**: „Entwurf“ wird gespeichert, aber noch nicht verwendet. „Aktiv“ fließt ab sofort in jede
  Analyse ein. Entwürfe erscheinen in den Auswahllisten der Nachfragen und des Feedbacks als „(Entwurf)“,
  damit eine neu angelegte Kategorie sofort auswählbar ist – der KI-Prompt und die Prüfung ihrer Antworten
  nutzen aber nur aktive Kategorien.
- **Jede Änderung ist eine neue Version** (`kategorien/`) mit Person und Zeitpunkt. Löschen ist ebenfalls
  nur eine neue Version mit Status „gelöscht“, der Verlauf bleibt erhalten.
- **Lesbare Kopie im Drive**: Pro Kategorie ein Google-Doc „Kategorie-Beschreibung (aus der App)“ in
  `Anlage KI Assistent/<Kategorie>/`, also dort, wo die Buchhaltung ihre Unterlagen bisher ablegt.
- **Pflichtangaben**: Die KI prüft bei der gewählten Kategorie, ob die genannten Pflichtangaben auf dem
  Beleg stehen (`fehlende_pflichtangaben`). Fehlt eine, geht der Beleg zur Nachfrage.

**„Lernen“ heißt hier: Der Prompt wird angepasst, das Modell wird nicht nachtrainiert.** Die aktiven
Kategorien mit allen Regeln stehen bei jeder Analyse in den Anweisungen an die KI. Die Prompt-Version
bekommt einen Fingerabdruck des Kategorie-Wissens (z. B. `2026-11-17.5+kda009b`). So zeigt die
Auswertung, ob eine geänderte Beschreibung die Trefferquote verbessert oder verschlechtert. Ein echtes
Nachtrainieren des Modells lohnt sich erst mit einigen hundert geprüften Belegen. Diese sammeln
Feedback und Korrekturen gerade.

### Probelauf vor dem Speichern („Hilft meine Änderung?“)

Datei: `testlauf.py`, `App.py` (`_zeige_testlauf`)

Im Tab „Kategorien“ öffnet der Button **„Testen“** (unter dem Formular einer Kategorie) ein Fenster mit dem **Probelauf mit Beispielbelegen**. Kategorien wählt man ganz oben in der Auswahlliste; der Button „Neue Kategorie“ daneben legt eine neue an. Die Beispielbelege
liegen im passenden Ordner unter `Anlage KI Assistent/` (z. B. „Bewirtungsbeleg Deutschland“). Neue
Beispiele lassen sich direkt in der App hochladen.

Jeder Beispielbeleg wird zweimal komplett analysiert (doppelt lesen, Prüfungen, ggf. Schiedsrichter):

- **vorher**: mit dem gespeicherten Kategorie-Wissen
- **nachher**: mit dem Stand im Formular, auch wenn er noch nicht gespeichert ist

Ergebnis pro Variante („Bisherige Beschreibung“ / „Neue Beschreibung“): Wie viele Belege wurden als diese
Kategorie erkannt? (Bewirtungs-Unterarten zählen gegenseitig.) Wie viele müssten nachgefragt werden? Dazu
ein **Fazit in einem Satz** („Die Änderung hilft“ / „Eher nicht speichern“ / „Kein Unterschied“; richtig
erkennen zählt mehr als wenige Rückfragen) und eine Tabelle pro Beleg: grün = sicher, rot = Nachfrage,
grau = Datei nicht lesbar.
Gespeichert wird beim Testlauf **nichts**. Höchstens 15 Belege pro Lauf, 4 parallel.

## 10. Feedback und Messung

Datei: `App.py`

Nach jeder Analyse kann ein Mensch jedes Feld bestätigen oder korrigieren. Gespeichert werden das
Ergebnis, alle Lesungen (welches Modell was gelesen hat), die Umdeutungen und die Korrekturen.

Die **Belegart wird ausdrücklich abgefragt**: „Stimmt die Belegart „…“? Ja / Nein“, nichts ist
vorausgewählt; bei „Nein“ wählt man die richtige. Ohne Antwort lässt sich nicht speichern. (Vorher war die
KI-Kategorie vorausgewählt – wer sie nicht umstellte, bestätigte sie, auch wenn im Kommentar das Gegenteil
stand.)

Der Tab **„Auswertung“** (`zeige_auswertung`) zählt jeden bewerteten Beleg **einmal** (jüngste Bewertung
pro Datei-Hash), aus Feedback und korrigierten Nachfragen:

| Kennzahl | Bedeutung |
|---|---|
| Belegart richtig | KI-Kategorie = vom Menschen bestätigte Kategorie |
| Alle Felder richtig | kein Feld musste korrigiert werden (ein Feld gilt als richtig, wenn es beim Bewerten nicht geändert wurde) |
| Komplett richtig | beides |

Dazu Trefferquote pro Feld und pro Belegart, die Verwechslungen und ein Hinweis, wenn eine falsche
Belegart eine **neu angelegte Kategorie** betrifft (die KI kannte sie bei der Analyse evtl. noch nicht).
Filter nach Prompt-Version und Modell sind aufklappbar.

**Bewertete Belege durchsehen:** Filter „Alle / Fehlerfrei / Mit Korrekturen / Mit Kommentar“, Beleg
auswählen. Links das Original (Klick aufs Bild oder „Größer ansehen“ öffnet alle Seiten groß), rechts jedes
Feld mit „KI hat gelesen“ und „Richtig ist“ – grün = stimmte, rot = korrigiert – plus Bewerter, Quelle
(Feedback oder Nachfrage-Korrektur), Modell, Prompt-Version und Kommentar.

**Bewertung korrigieren:** Stimmt eine Bewertung nicht (z. B. Kommentar „Zimmerbuchung nicht erkannt“, aber
Belegart als richtig gezählt), legt „Bewertung korrigieren“ eine **neue** Bewertung an: Belegart-Frage,
Felder (vorbelegt mit den bisher richtigen Werten, verglichen wird immer mit der KI-Lesung) und Pflicht-
Begründung. Die jüngste Bewertung zählt; die alte bleibt erhalten (`korrektur_von`, `kommentar_vorher`).
Der Filter „Mit Kommentar“ hilft, solche Fälle zu finden.

## Was nicht abgesichert ist

- **Texte** (Belegnummer, Name, Adresse) haben keine Rechen-Gegenprobe. Abgesichert sind sie nur durch das
  Doppelt-Lesen und den Schiedsrichter.
- Liest die KI **zwei zusammenpassende Zahlen gleichzeitig falsch** (z. B. Netto und MwSt. derselben Zeile
  so, dass die Rechnung trotzdem aufgeht), fällt das nur auf, wenn die Gesamtsumme nicht mehr stimmt.
  Das ist sehr unwahrscheinlich, aber möglich.
- Fachliche Fragen (z. B. ob eine Bewirtung steuerlich anerkannt wird) prüft die App nicht.

## Ergebnis mit den 13 Beispielbelegen (Ordner „Bewirtungsbeleg Deutschland“)

| | vorher (17.11.2026, Prompt 2026-11-17.3) | jetzt (Prompt 2026-11-17.4) |
|---|---|---|
| ohne Nachfrage durchgelaufen | 5 | 9 |
| zur Nachfrage | 8 | 4 |

Die 4 verbleibenden Nachfragen: einmal Auslandsbeleg, zweimal Steuersatz 0 %, einmal passt der EC-Beleg
nicht zur Rechnung. Ob diese Einschätzungen der KI stimmen, muss die Buchhaltung bestätigen.
