# Implementierungsplan: FoxQuiz für die Klassen 1 bis 4

## Ziel

FoxQuiz soll neben den Klassen 5 bis 12 auch die Klassen 1 bis 4 unterstützen. Die erste Ausbaustufe soll pädagogisch altersgerechte Quizze erzeugen, ohne die bestehende Quiz-Datenstruktur oder den grundsätzlichen Bedienablauf wesentlich zu verändern.

Die Nutzung für jüngere Kinder ist zunächst gemeinsam mit Eltern oder Lehrkräften vorgesehen.

## Umfang und bewusste Abgrenung

Im Umfang enthalten sind:

- Klassen 1 bis 4 in der Benutzeroberfläche und allen drei Sprachen
- altersgerechte LLM-Anweisungen für Klasse 1 bis 2 und Klasse 3 bis 4
- weiterhin genau zehn Fragen pro Quiz
- für Klasse 1–2 exakt drei, sonst weiterhin drei bis fünf Antwortmöglichkeiten pro Frage
- besondere sprachliche und didaktische Regeln für die Grundschule
- automatisierte Tests und LLM-Verhaltensevaluationen für die neuen Klassenstufen
- Aktualisierung der öffentlichen Projektbeschreibung von Klasse 5–12 auf Klasse 1–12 beziehungsweise Alter etwa 6–18

Nicht im Umfang dieser ersten Version enthalten sind:

- Text-to-Speech oder Vorlesefunktion
- bildgestützte Fragen oder Antworten
- vordefinierte Fächer- oder Themenkataloge
- eine variable Anzahl von Quizfragen
- eine grundsätzlich neue Benutzeroberfläche für selbstständige Nutzung durch Erstklässler

## Implementierungsschritte

### 1. Klassenstufen in Frontend und Übersetzungen erweitern

- [x] Im Auswahlfeld für Klasse/Schuljahr die Klassen 1 bis 4 ergänzen.
- [x] Die internen Werte konsistent als `Klasse 1`, `Klasse 2`, `Klasse 3` und `Klasse 4` an das Backend senden.
- [x] Die sichtbaren Bezeichnungen in allen unterstützten Sprachen ergänzen:
  - Deutsch: `Klasse 1` bis `Klasse 4 (Grundschule)`
  - Englisch: `Grade 1` bis `Grade 4 (Elementary School)`
  - Portugiesisch: `1º Ano` bis `4º Ano do Ensino Fundamental`
- [x] Die Auswahl logisch von Klasse 1 bis Klasse 12 sortieren.
- [x] Die bisherige Standardauswahl Klasse 5 ausdrücklich beibehalten, damit die Erweiterung das bisherige Standardverhalten nicht unbeabsichtigt verändert.
- [x] Den bestehenden strukturierten Request mit `grade`, `subject`, `topic` und `preferred_language` unverändert weiterverwenden.

Akzeptanzkriterien:

- Alle Klassen von 1 bis 12 sind in Deutsch, Englisch und Portugiesisch sichtbar.
- Ein Sprachwechsel übersetzt auch die Klassen 1 bis 4 vollständig.
- Die Auswahl einer neuen Klassenstufe wird korrekt und ohne Änderung des API-Schemas an FoxQuiz übertragen.
- Klasse 5 bleibt beim ersten Aufruf vorausgewählt.

Verifikation:

- Playwright-Test für Sichtbarkeit und Übersetzung aller neuen Optionen
- Playwright-Test, der `Klasse 1` auswählt und den übertragenen Request prüft
- Bestehende Frontendtests müssen weiterhin bestehen

### 2. LLM-Prompts und Qualitätsprüfung für Grundschulklassen erweitern

- [x] Die bisherige Zielgruppe von Alter 10–18 auf etwa 6–18 Jahre erweitern.
- [x] Die fest kodierte Einteilung Klasse 5–8 und Klasse 9–12 durch vier pädagogische Gruppen ersetzen:
  - Klasse 1–2, etwa 6–8 Jahre
  - Klasse 3–4, etwa 8–10 Jahre
  - Klasse 5–8, etwa 10–14 Jahre
  - Klasse 9–12, etwa 14–18 Jahre
- [x] Für Klasse 1–2 sehr kurze, konkrete und leicht lesbare Formulierungen verlangen.
- [x] Für Klasse 3–4 einfache, aber etwas ausführlichere Fragen mit anschaulichen Beispielen erlauben.
- [x] Die bestehenden Regeln für Klasse 5–8 und Klasse 9–12 inhaltlich erhalten.
- [x] Den vorgelagerten Curriculum-Check anweisen, bei Klasse 1–4 besonders auf kognitive Angemessenheit, einfache konkrete Themen und altersgerechten Umfang zu achten.
- [x] Den LLM-as-a-Judge anweisen, die Grundschulregeln ausdrücklich mitzuprüfen.
- [x] Eine zentrale typisierte Klassenstufen-Policy für Request-Normalisierung, Prompts und Validierung einführen; die bestehenden Pydantic-Beschreibungen waren bereits klassenneutral.
- [x] Das verwendete Gemini-Modell und die bestehende Workflow-Architektur unverändert lassen.

Akzeptanzkriterien:

- Klasse 1–4 wird nicht mehr mit der bisherigen Altersangabe 10–18 oder ausschließlich den Regeln für Klasse 5–8 verarbeitet.
- Curriculum-Check, Quizgenerator und Judge erhalten konsistente Informationen über die Grundschulstufe.
- Zu schwierige oder abstrakte Themen werden bereits vor der Quizgenerierung geklärt oder abgelehnt.
- Geeignete Grundschulthemen werden nicht unnötig abgelehnt.

Verifikation:

- Unit-Tests für die Auswahl der altersabhängigen Promptregeln
- LLM-Verhaltensevaluationen für geeignete und ungeeignete Themen in Klasse 1 bis 4
- Prüfung der vollständigen Agenten-Traces, damit Curriculum-Check, Generator und Judge dieselbe Klassenstufe verwenden

### 3. Quizstruktur für Erstklässler altersgerecht halten

Die bestehende technische Quizstruktur bleibt erhalten:

- genau zehn Fragen pro Quiz
- drei bis fünf Antwortmöglichkeiten pro Frage
- genau eine richtige Antwort
- bestehendes Ausgabeformat, Fortschrittsanzeige, Feedback und Teilen bleiben unverändert

Zusätzliche Regeln:

- [x] Fragen und Antworten für Klasse 1–4 deutlich kürzer formulieren.
- [x] Für Klasse 1–2 exakt drei Antwortmöglichkeiten verlangen.
- [x] Für Klasse 3–4 weiterhin drei bis fünf Antwortmöglichkeiten erlauben.
- [x] Erklärungen für Klasse 1–2 auf ein bis zwei kurze Sätze begrenzen.
- [x] Komplizierte Distraktoren vermeiden.
- [x] Keine Verneinungsfragen wie „Welche Antwort ist nicht richtig?“ verwenden.
- [x] Doppelte Verneinungen vollständig ausschließen.
- [x] Emojis im Fragetext für Klasse 1–4 ausschließen, damit Piktogramme die Antwort nicht verraten; Titel und Erklärungen dürfen spielerisch bleiben.
- [x] Einfache und konkrete Themen bevorzugen.
- [x] Abstrakte oder mehrdeutige Themen durch den Curriculum-Check klären lassen, bevor ein Quiz generiert wird.
- [x] Den Judge dieselben Struktur- und Sprachregeln prüfen lassen.

Akzeptanzkriterien:

- Jedes Quiz enthält weiterhin genau zehn Fragen.
- Fragen für Klasse 1–2 enthalten genau drei Optionen.
- Fragen und Antworten für Klasse 1–4 sind wesentlich kürzer als für höhere Klassen.
- Erklärungen für Klasse 1–2 bestehen aus höchstens zwei kurzen Sätzen.
- Quizze für Klasse 1–4 enthalten keine Verneinungsfragen oder unnötig komplizierten Distraktoren.
- Die bestehende Frontend- und API-Struktur benötigt keine Sonderbehandlung für die neuen Klassen.

Verifikation:

- Deterministische Tests für unverändertes Quiz-Schema und Frontend-Verarbeitung
- LLM-Evaluationen für Kürze, Lesbarkeit, Optionsanzahl, konkrete Themen und das Verbot von Verneinungsfragen
- Mindestens je ein erfolgreicher Eval-Fall für Klasse 1, 2, 3 und 4

### 4. Bedienbarkeit bewusst auf begleitete Nutzung begrenzen

Die erste Version wird für die gemeinsame Nutzung mit Eltern oder Lehrkräften ausgelegt. Eine vollständig selbstständige Bedienung durch Erstklässler ist kein Ziel dieser Erweiterung.

- [x] Den bestehenden Ablauf mit Auswahl von Klasse, Schulfach und Thema beibehalten.
- [x] Keine neue Eingabemethode oder Grundschul-Sonderoberfläche entwickeln.
- [x] In README und Spezifikation klarstellen, dass jüngere Kinder FoxQuiz zunächst gemeinsam mit einer erwachsenen Begleitperson nutzen sollen.
- [x] Sicherstellen, dass diese Abgrenzung nicht als technische Einschränkung des generierten Quiz missverstanden wird: Das Quiz selbst muss trotzdem altersgerecht sein.

Akzeptanzkriterien:

- Der bestehende Bedienablauf bleibt unverändert.
- Es entstehen keine zusätzlichen UI-Komponenten für Vorlesen, Bilder oder Themenauswahl.
- Dokumentation und Spezifikation beschreiben die begleitete Nutzung für jüngere Kinder eindeutig.
- Der Implementierungsumfang bleibt auf Klassenstufen, Prompts, Qualitätskontrolle, Tests und Dokumentation begrenzt.

Verifikation:

- Manuelle Prüfung des bestehenden Ablaufs auf Desktop und Mobilgerät
- Review von README und Spezifikation
- Bestehende Browser-Smoke-Tests müssen ohne strukturelle Anpassung des Testablaufs weiter bestehen

## Empfohlene LLM-Evaluationsfälle

Mindestens folgende Fälle sollten aufgenommen werden:

1. Klasse 1, Mathematik, Zahlen bis 20
2. Klasse 1, Deutsch, einfache Buchstaben oder Wörter
3. 1º Ano, Matemática, adição até 10
4. Grade 2, English, common animals
5. Klasse 3, Sachunterricht, Jahreszeiten
6. 3º Ano, Ciências, partes de uma planta
7. Klasse 4, Mathematik, schriftliche Addition
8. Grade 4, Science, states of matter
9. Klasse 1, Mathematik, Differentialgleichungen — muss als ungeeignet abgelehnt oder mit altersgerechten Alternativen beantwortet werden
10. Klasse 1, Mathematik, „Rechnen“ — soll bei zu breitem Umfang eine kurze Klärungsfrage stellen

Die Bewertung soll insbesondere prüfen:

- fachliche Richtigkeit
- Passung zu Klasse und Thema
- kurze, verständliche Sprache
- exakt drei Optionen für Klasse 1–2
- ein bis zwei kurze Erklärungssätze für Klasse 1–2
- keine Verneinungsfragen
- keine komplizierten Distraktoren
- kein unzulässiger Themenwechsel

## Abschlussprüfung

Vor einem Release sind auszuführen:

```bash
uv run pytest tests/unit tests/integration tests/browser -m "not google_cloud"
agents-cli lint
```

Die LLM-Verhaltensevaluationen benötigen lokale Google-Credentials und müssen daher lokal ausgeführt werden. Anschließend soll mindestens ein kompletter manueller Quizdurchlauf für Klasse 1 in jeder unterstützten Sprache erfolgen. Eine Bereitstellung in Google Cloud ist nicht Bestandteil dieses Plans und benötigt weiterhin eine ausdrückliche Bestätigung.

## Implementierungsstand vom 20. August 2026

- Credential-freie Abschluss-Suite: 188 bestanden, 10 Google-Cloud-Tests bewusst abgewählt.
- `agents-cli lint`: vollständig bestanden.
- Acht erfolgreiche Klassen-1-bis-4-Evalfälle: Struktur 8/8, Mittelwert 1,0; pädagogische Qualität Mittelwert 4,875. Der einzelne 4-Punkte-Fall führte zur verbindlichen Judge-Regel für höchstens zwei Erklärungssätze; der gezielte Wiederholungslauf erreichte anschließend 5,0.
- Zwei Curriculum-Routing-Fälle: Mittelwert 5,0 für Ablehnung beziehungsweise Klärungsfrage.
- Kein Deployment durchgeführt. Die drei manuellen, sprachspezifischen Browserdurchläufe bleiben Teil der Release-Abnahme.
