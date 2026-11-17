# docx-agent

Python/LangGraph-Agent für `.docx`-Bearbeitung per Anweisung: Einfügen, Ersetzen, Löschen und
Anwenden vorhandener Absatzstile bei Erhalt unberührter Inhalte und Formatierungen.

[English](README.md) · [Architektur & Integrität](docs/ARCHITECTURE.md) ·
[KI-Entwicklungsprozess](docs/DEVELOPMENT.md) · [Gemessene Evaluation](evals/baselines/2026-11-17-final.md)

## Start

Benötigt [uv](https://docs.astral.sh/uv/getting-started/installation/); `.python-version` wählt
Python 3.12. Alle Befehle im Repository-Hauptverzeichnis ausführen:

```sh
uv sync --locked
```

Unter macOS außerhalb von iCloud arbeiten: Versteckte Dateien der editierbaren Installation können
`ModuleNotFoundError` auslösen. Nach einem Umzug `.venv` neu erstellen; lokal hilft `PYTHONPATH=src:.` vor dem Befehl.

Privat bereitgestellte Review-`.env` verwenden oder `.env.example` nach `.env` kopieren und setzen:

```dotenv
GEMINI_API_KEY=your_key
DOCX_AGENT_MODEL=gemini-3.5-flash-lite
DOCX_AGENT_MAX_TURNS=5
DOCX_AGENT_MAX_RETRIES=0
```

Der Testschlüssel wird privat für sieben Tage ab dem angegebenen Übergabezeitpunkt bereitgestellt;
der Inhaber muss ihn danach widerrufen. Keine automatische Abschaltung durch die Anwendung.
Anschließend einen eigenen [Gemini-Schlüssel](https://aistudio.google.com/apikey) verwenden.
`.env` niemals committen.

```sh
mkdir -p out
uv run docx-agent edit samples/therapy_session_note/input.docx "In the goals table, replace the sleep target 8h with 7.5h. Leave everything else unchanged." -o out/edited.docx --non-interactive
uv run docx-agent verify samples/therapy_session_note/input.docx out/edited.docx
```

PowerShell: Ausgabeordner mit `New-Item -ItemType Directory -Force out` erstellen.
Erwartet: `saved: ...`, `out/edited.docx`, `out/edited.report.json` und abschließend
`OK: integrity checks passed.` Das Original bleibt erhalten. Vorhandene Ausgaben können beim
erneuten Ausführen überschrieben werden.

`edit` sendet Dokumenttext/Anweisung an Gemini. Nur synthetische Beispiele oder zur Weitergabe
freigegebene Dokumente verwenden; keine echten Patientendaten. Die obigen Einstellungen erlauben
höchstens fünf Bearbeitungsaufrufe plus eine Modellprüfung. Programmstandard: 30 Aufrufe und
zwei gemeinsame Zurücksetzungen nach Validierungs-/Bewertungsfehlern.

## Ohne Schlüssel prüfen

```sh
uv run docx-agent inspect samples/therapy_session_note/input.docx
uv run docx-agent verify samples/therapy_session_note/input.docx samples/therapy_session_note/output.docx
make check
```

`inspect`, `verify` und Tests führen keine Modellaufrufe aus. Die [drei Beispiele](samples/README.md)
enthalten Eingabe, Anweisung, echte Gemini-Ausgabe, Diff und Integritäts-/Herkunftsberichte.
Das Layout lässt sich durch Öffnen der Word-Dateien prüfen.

`make check` = Ruff-Lint/Format + striktes Quellcode-mypy + vollständige pytest-/Coverage-Prüfung,
ebenso in CI. Letzter Lauf: **271 Tests, 99,44 % Quellcodeabdeckung** (Mindestwert 85 %).
Ohne Make:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q --cov --cov-report=term --cov-report=xml
```

## Schnittstelle und Verhalten

```text
docx-agent inspect FILE.docx
docx-agent edit INPUT.docx "instruction" -o OUTPUT.docx
docx-agent verify BEFORE.docx AFTER.docx [--json]
```

- Pfade/Anweisungen mit Leerzeichen in Anführungszeichen setzen. Ort, alten und neuen Text nennen.
- Rückfragen pausieren den Graphen; im selben Prozess antworten. `--non-interactive` beendet mit 3.
- Validierungs-/Bewertungsfehler setzen das Dokument zurück, solange Versuche übrig sind.
  Bei Fehlern wird nichts veröffentlicht; `--force` speichert einen letzten abgelehnten Versuch
  und beendet weiterhin mit 1.
- `--dry-run` ruft das Modell auf, schreibt aber keine Dateien. `--report PATH.json` wählt den
  Berichtspfad; sonst entsteht für `result.docx` die Datei `result.report.json`.
- `--model`/`--provider` überschreiben Einstellungen. Umgebungsvariablen haben Vorrang vor `.env`.
  Weitere Optionen: `.env.example` und `uv run docx-agent edit --help`. Gemini ist live geprüft;
  optionale Anthropic-Unterstützung nicht. Keine SDK-Wiederholungen oder Anbieter-Fallbacks.
- Rückgabecodes: 0 Erfolg; 1 fehlgeschlagene Prüfung/Modellaufruf; 2 ungültige Eingabe/Konfiguration
  oder Dateifehler; 3 Rückfrage im nicht interaktiven Modus.

## Architektur und Grenzen

Expliziter `StateGraph`: Laden → Agent/Werkzeuge → Validierung → strukturierte Modellbewertung
→ Speichern, mit Rückfrage-Unterbrechungen und begrenzten Zurücksetzungen. Das Dokument liegt
im Laufzeitkontext; Nachrichten werden nur angehängt. Werkzeuge erlauben Dokumentoperationen,
keine Shell-/Codeausführung.

Eigene `zipfile`/lxml-Operationen serialisieren nur den Haupt-XML-Teil neu. Unberührte
Paketbestandteile bleiben **nach Dekompression** bytegleich; unberührte Blöcke werden per
kanonischem XML verglichen. Keine Bytegleichheit des gesamten ZIPs. python-docx erstellt
Fixtures/öffnet Testausgaben, speichert aber keine Agentenausgabe.
[Prüfungen, Entscheidungen und Implementierung](docs/ARCHITECTURE.md).

- Nur Haupttext, Tabellenzellentext und vorhandene Absatzstile. Keine Bearbeitung von Kopf-/
  Fußzeilen, Fußnoten, Kommentaren, Textfeldern oder Linkzielen; keine Tabellen-/Bildeinfügung,
  absatzübergreifenden Textspannen oder automatische Inhaltsverzeichnisse. Geschützte Objekte,
  Felder und Änderungs-Runs werden abgelehnt, nicht stillschweigend entfernt.
- Eigenständiges `verify` beanstandet beabsichtigte Überschriftenstiländerungen. Die Sitzung
  prüft diese mit genauen Block-Berechtigungen; programmatisch gibt es `style_changes`.
- Mehrdeutigkeitsbehandlung und Modellbewertung garantieren kein korrektes Verständnis.
  Die Prüfung hat heuristische Zuordnungs-/Formatgrenzen; CI prüft keine gerenderten Seiten.
- Checkpoints gelten nur im selben Prozess. Snapshot-/Baumkopierkosten passen zu Fallstudiengrößen.

## Evaluation

`make eval` zeigt nur den Plan. Ausdrücklicher Live-Aufruf:

```sh
uv run python -m evals.run --live --max-calls 36 --retry-unavailable
```

Sechs synthetische Fälle; hartes gemeinsames Aufruflimit; optional eine 503-Wiederholung je Fall
innerhalb dieses Limits. GitHub-Evals nur per `workflow_dispatch`.
[Runner-Details](evals/README.md). Gemessen: erste Baseline **5/6**, danach Prompt-Korrektur für den Mehrdeutigkeitsfall. Mit dem
finalen Prompt **bestanden alle 6/6 Fälle** (45 Gemini-Aufrufe, fallweise ausgeführt, da der
Free Tier 15 Anfragen/Minute erlaubt; jeder Fehlschlag war ein Provider-429/503). Jeder Fall
bestand einmal: keine gemessene Erfolgsquote. [Finaler Nachweis](evals/baselines/2026-11-17-final.md) ·
[erste Baseline](evals/baselines/2026-11-17.md).
