// English/German UI strings and the EN · DE switch, shared by the editor and the docs page.
// Static markup opts in with data-i18n="key" (text), data-i18n-placeholder / data-i18n-aria.
// Long-form pages carry one block per language: [data-lang="en"] / [data-lang="de"].

const LANGS = ["en", "de"];
const KEY = "docx-agent.lang";

const plural = (n, one, many) => (n === 1 ? one : many.replace("{n}", n));

const STRINGS = {
  en: {
    "nav.label": "Sections",
    "nav.editor": "Editor",
    "nav.docs": "How it was built",
    "lang.label": "Language",
    "docs.title": "How docx-agent was built",
    lede: "Describe a change to a Word document. Everything you didn’t ask to change stays exactly as it was.",
    "title.compose": "New edit",
    "title.working": "Editing",
    "title.question": "Question",
    "title.result": "Result",
    "title.failed": "Not saved",
    "label.document": "Document",
    "drop.choose": "Choose a .docx file",
    "drop.hint": "or drop it here · up to {mb} MB",
    "drop.change": "Change",
    "label.instruction": "What should change?",
    "instruction.placeholder":
      "e.g. Change the session date to 19 March 2026 and remove the bullet about breathing exercises.",
    "examples.label": "Example instructions",
    "example.1": "Change the date to 19 March 2026",
    "example.2": "Remove the second bullet point",
    "example.3": "Replace “Max Mustermann” with “Erika Musterfrau” everywhere",
    "label.model": "Model",
    submit: "Edit document",
    "question.title": "One question before editing",
    "label.answer": "Your answer",
    "answer.send": "Send answer",
    cancel: "Cancel",
    "result.receipt": "Saved and verified",
    "result.changes": "Changes",
    "result.download": "Download edited document",
    "result.another": "Edit another document",
    "failed.kind": "Nothing was saved",
    "failed.original": "Your original file was not modified.",
    "failed.retry": "Try again",
    "failed.restart": "Start over",
    "history.title": "Recent documents",
    "history.clear": "Clear",
    "history.empty": "Documents you edit appear here. Only the file name and your instruction are kept, never the documents.",
    "history.kept": "Only file names and instructions are kept here, never the documents.",
    "foot.local": "Runs locally. Files are edited in memory and never stored.",
    "provider.none": "No model",
    "provider.noKey": "No API key is configured. Add GEMINI_API_KEY or ANTHROPIC_API_KEY to .env, then restart docx-agent ui.",
    "file.notDocx": "Choose a Word document with the .docx extension.",
    "file.tooLarge": "This file is {size}; the limit is {mb} MB.",
    "request.failed": "Request failed ({status}).",
    "server.unreachable": "Could not reach the local server: {message}",
    "server.lost": "Lost contact with the local server. Is docx-agent ui still running?",
    "elapsed.fast": "Usually 5–20 seconds.",
    "elapsed.slow": "Busy models or long documents take a little longer.",
    "answer.empty": "Type an answer first.",
    "change.modified": "Changed",
    "change.inserted": "Added",
    "change.deleted": "Removed",
    "change.added": "added",
    "change.removed": "removed",
    "change.empty": "(empty paragraph)",
    "result.count": (v) => plural(v.n, "1 change made", "{n} changes made"),
    "result.none": "No visible changes",
    "meta.document": "Document",
    "meta.model": "Model",
    "meta.integrity": "Integrity",
    "meta.verified": "Verified",
    "meta.verifiedNote": " · nothing outside these changes was altered",
    "meta.edits": "Edits",
    "meta.operations": (v) => plural(v.n, "1 operation", "{n} operations"),
    "meta.retries": (v) => plural(v.n, "1 retry", "{n} retries"),
    "meta.removed": "Removed",
    "meta.asRequested": "{items} (as requested)",
    "meta.time": "Time",
    "meta.report": "Report",
    "meta.json": "Download JSON ",
    "failed.serviceKind": "Model service error",
    "failed.serviceTitle": "The edit could not run",
    "failed.title": "The edit was not applied",
    "history.saved": (v) => plural(v.n, "Saved · 1 change", "Saved · {n} changes"),
    "history.notSaved": "Not saved",
    "history.error": "Error",
    "time.now": "just now",
  },
  de: {
    "nav.label": "Bereiche",
    "nav.editor": "Editor",
    "nav.docs": "Wie es entstand",
    "lang.label": "Sprache",
    "docs.title": "Wie docx-agent entstand",
    lede: "Beschreiben Sie eine Änderung an einem Word-Dokument. Alles, was Sie nicht ändern wollten, bleibt genau so, wie es war.",
    "title.compose": "Neue Bearbeitung",
    "title.working": "Bearbeitung",
    "title.question": "Rückfrage",
    "title.result": "Ergebnis",
    "title.failed": "Nicht gespeichert",
    "label.document": "Dokument",
    "drop.choose": "Eine .docx-Datei wählen",
    "drop.hint": "oder hierher ziehen · bis {mb} MB",
    "drop.change": "Ändern",
    "label.instruction": "Was soll sich ändern?",
    "instruction.placeholder":
      "z. B. Ändere das Sitzungsdatum auf den 19. März 2026 und entferne den Punkt zu den Atemübungen.",
    "examples.label": "Beispielanweisungen",
    "example.1": "Ändere das Datum auf den 19. März 2026",
    "example.2": "Entferne den zweiten Aufzählungspunkt",
    "example.3": "Ersetze überall „Max Mustermann“ durch „Erika Musterfrau“",
    "label.model": "Modell",
    submit: "Dokument bearbeiten",
    "question.title": "Eine Frage vor der Bearbeitung",
    "label.answer": "Ihre Antwort",
    "answer.send": "Antwort senden",
    cancel: "Abbrechen",
    "result.receipt": "Gespeichert und geprüft",
    "result.changes": "Änderungen",
    "result.download": "Bearbeitetes Dokument herunterladen",
    "result.another": "Weiteres Dokument bearbeiten",
    "failed.kind": "Es wurde nichts gespeichert",
    "failed.original": "Ihre Originaldatei wurde nicht verändert.",
    "failed.retry": "Erneut versuchen",
    "failed.restart": "Neu beginnen",
    "history.title": "Letzte Dokumente",
    "history.clear": "Leeren",
    "history.empty": "Bearbeitete Dokumente erscheinen hier. Gespeichert werden nur Dateiname und Anweisung, nie die Dokumente.",
    "history.kept": "Hier werden nur Dateinamen und Anweisungen gespeichert, nie die Dokumente.",
    "foot.local": "Läuft lokal. Dateien werden im Speicher bearbeitet und nie abgelegt.",
    "provider.none": "Kein Modell",
    "provider.noKey": "Kein API-Schlüssel konfiguriert. GEMINI_API_KEY oder ANTHROPIC_API_KEY in .env eintragen und docx-agent ui neu starten.",
    "file.notDocx": "Wählen Sie ein Word-Dokument mit der Endung .docx.",
    "file.tooLarge": "Diese Datei ist {size} groß; das Limit liegt bei {mb} MB.",
    "request.failed": "Anfrage fehlgeschlagen ({status}).",
    "server.unreachable": "Der lokale Server ist nicht erreichbar: {message}",
    "server.lost": "Verbindung zum lokalen Server verloren. Läuft docx-agent ui noch?",
    "elapsed.fast": "Meist 5–20 Sekunden.",
    "elapsed.slow": "Ausgelastete Modelle oder lange Dokumente brauchen etwas länger.",
    "answer.empty": "Geben Sie zuerst eine Antwort ein.",
    "change.modified": "Geändert",
    "change.inserted": "Hinzugefügt",
    "change.deleted": "Entfernt",
    "change.added": "hinzugefügt",
    "change.removed": "entfernt",
    "change.empty": "(leerer Absatz)",
    "result.count": (v) => plural(v.n, "1 Änderung vorgenommen", "{n} Änderungen vorgenommen"),
    "result.none": "Keine sichtbaren Änderungen",
    "meta.document": "Dokument",
    "meta.model": "Modell",
    "meta.integrity": "Integrität",
    "meta.verified": "Geprüft",
    "meta.verifiedNote": " · außerhalb dieser Änderungen wurde nichts verändert",
    "meta.edits": "Bearbeitungen",
    "meta.operations": (v) => plural(v.n, "1 Operation", "{n} Operationen"),
    "meta.retries": (v) => plural(v.n, "1 Wiederholung", "{n} Wiederholungen"),
    "meta.removed": "Entfernt",
    "meta.asRequested": "{items} (wie gewünscht)",
    "meta.time": "Dauer",
    "meta.report": "Bericht",
    "meta.json": "JSON herunterladen ",
    "failed.serviceKind": "Fehler beim Modelldienst",
    "failed.serviceTitle": "Die Bearbeitung konnte nicht laufen",
    "failed.title": "Die Bearbeitung wurde nicht übernommen",
    "history.saved": (v) => plural(v.n, "Gespeichert · 1 Änderung", "Gespeichert · {n} Änderungen"),
    "history.notSaved": "Nicht gespeichert",
    "history.error": "Fehler",
    "time.now": "gerade eben",
  },
};

// Text produced by the local server (progress, locations, errors). The model's own words
// (summaries, questions, review reasons) stay in whatever language the model used.
const SERVER_DE = [
  [/^Reading the document$/, "Dokument wird gelesen"],
  [/^Editing$/, "Bearbeitung"],
  [/^Checking integrity$/, "Integritätsprüfung"],
  [/^Reviewing the result$/, "Ergebnis wird geprüft"],
  [/^Indexing paragraphs and tables$/, "Absätze und Tabellen werden indiziert"],
  [/^Planning$/, "Planung"],
  [/^1 edit applied$/, "1 Änderung angewendet"],
  [/^(\d+) edits applied$/, "$1 Änderungen angewendet"],
  [/^Starting again from the original$/, "Neustart vom Original"],
  [/^Comparing every part with the original$/, "Jeder Teil wird mit dem Original verglichen"],
  [/^Checking the result against your instruction$/, "Ergebnis wird mit Ihrer Anweisung abgeglichen"],
  [/^Preparing your file$/, "Ihre Datei wird vorbereitet"],
  [/^Continuing with your answer$/, "Weiter mit Ihrer Antwort"],
  [/^Waiting for your answer$/, "Wartet auf Ihre Antwort"],
  [/^Paragraph (\d+)$/, "Absatz $1"],
  [/^Table (\d+) · row (\d+), cell (\d+)( \(nested\))?$/, (_, t, r, c, n) => `Tabelle ${t} · Zeile ${r}, Zelle ${c}${n ? " (verschachtelt)" : ""}`],
  [/^Table$/, "Tabelle"],
  [/^Document$/, "Dokument"],
  [/^The integrity check rejected the edit, so nothing was saved\.$/, "Die Integritätsprüfung hat die Bearbeitung abgelehnt, daher wurde nichts gespeichert."],
  [/^The edit could not be completed\.$/, "Die Bearbeitung konnte nicht abgeschlossen werden."],
  [/^Agent turn limit reached\.$/, "Das Limit an Agent-Zügen wurde erreicht; es wurde nichts gespeichert."],
  [/^Editor model call failed; no output saved\.$/, "Der Aufruf des Editor-Modells ist fehlgeschlagen; es wurde nichts gespeichert."],
  [/^Editor model refused the request; no output saved\.$/, "Das Editor-Modell hat die Anfrage abgelehnt; es wurde nichts gespeichert."],
  [/^Editor returned a non-assistant message\.$/, "Der Editor lieferte eine ungültige Antwort; es wurde nichts gespeichert."],
  [/^Review model call or structured response failed; no output saved\.$/, "Der Aufruf des Review-Modells oder seine strukturierte Antwort ist fehlgeschlagen; es wurde nichts gespeichert."],
  [/^Choose a Word document \(\.docx\)\.$/, "Wählen Sie ein Word-Dokument (.docx)."],
  [/^The file is larger than (\d+) MB\.$/, "Die Datei ist größer als $1 MB."],
  [/^Describe the change you want\.$/, "Beschreiben Sie die gewünschte Änderung."],
  [/^Keep the instruction under (\d+) characters\.$/, "Die Anweisung muss unter $1 Zeichen bleiben."],
  [/^(\w+) has no API key configured\.$/, "Für $1 ist kein API-Schlüssel konfiguriert."],
  [/^This file can't be read as a Word document: (.*)$/s, "Diese Datei lässt sich nicht als Word-Dokument lesen: $1"],
  [/^This edit is no longer in memory\. Start a new one\.$/, "Diese Bearbeitung ist nicht mehr im Speicher. Starten Sie eine neue."],
  [/^Type an answer first\.$/, "Geben Sie zuerst eine Antwort ein."],
  [/^This job is not waiting for an answer\.$/, "Diese Bearbeitung wartet auf keine Antwort."],
  [/^Requests must come from the docx-agent UI\.$/, "Anfragen müssen von der docx-agent-Oberfläche kommen."],
  [/^Unexpected error: (.*)$/s, "Unerwarteter Fehler: $1"],
];

const listeners = [];
let current = initialLang();

function initialLang() {
  let saved = null;
  try { saved = localStorage.getItem(KEY); } catch { /* private mode */ }
  if (LANGS.includes(saved)) return saved;
  return (navigator.language || "").toLowerCase().startsWith("de") ? "de" : "en";
}

export function lang() { return current; }

export function t(key, vars = {}) {
  const value = STRINGS[current][key] ?? STRINGS.en[key] ?? key;
  if (typeof value === "function") return value(vars);
  return value.replace(/\{(\w+)\}/g, (match, name) => (name in vars ? String(vars[name]) : match));
}

function translateOne(text) {
  for (const [pattern, replacement] of SERVER_DE) {
    if (pattern.test(text)) return text.replace(pattern, replacement);
  }
  return text;
}

/** Translate a message from the local server; a trailing " Cause: ..." is kept verbatim. */
export function tServer(text) {
  if (current === "en" || !text) return text;
  const [head, ...cause] = String(text).split(" Cause: ");
  const translated = translateOne(head);
  return cause.length ? `${translated} Ursache: ${cause.join(" Cause: ")}` : translated;
}

export function onLangChange(fn) { listeners.push(fn); }

function applyStatic() {
  document.documentElement.lang = current;
  for (const node of document.querySelectorAll("[data-i18n]")) node.textContent = t(node.dataset.i18n);
  for (const node of document.querySelectorAll("[data-i18n-placeholder]")) node.placeholder = t(node.dataset.i18nPlaceholder);
  for (const node of document.querySelectorAll("[data-i18n-aria]")) node.setAttribute("aria-label", t(node.dataset.i18nAria));
  for (const node of document.querySelectorAll("[data-lang]")) node.hidden = node.dataset.lang !== current;
  if (document.body.dataset.titleKey) document.title = t(document.body.dataset.titleKey);
  for (const button of document.querySelectorAll("[data-set-lang]")) {
    button.setAttribute("aria-pressed", String(button.dataset.setLang === current));
  }
}

export function setLang(next) {
  if (!LANGS.includes(next) || next === current) return;
  current = next;
  try { localStorage.setItem(KEY, next); } catch { /* private mode */ }
  applyStatic();
  for (const fn of listeners) fn(current);
}

for (const button of document.querySelectorAll("[data-set-lang]")) {
  button.addEventListener("click", () => setLang(button.dataset.setLang));
}
applyStatic();
