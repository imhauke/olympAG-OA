// docx-agent local UI. Vanilla JS, no build step. All user, document and model text is set with
// textContent (never innerHTML), so file names or model output cannot inject markup.

const $ = (id) => document.getElementById(id);
const HEADER = { "X-Docx-Agent": "1" };
const POLL_MS = 650;
const VIEWS = ["compose", "working", "question", "result", "failed"];
const TITLES = { compose: "New edit", working: "Editing", question: "Question", result: "Result", failed: "Not saved" };

const state = {
  config: null,
  provider: null,
  file: null,
  job: null,
  pollTimer: null,
  clockTimer: null,
  pollFailures: 0,
};

// ------------------------------------------------------------------ helpers

const storage = {
  get(area, key) { try { return window[area].getItem(key); } catch { return null; } },
  set(area, key, value) { try { window[area].setItem(key, value); } catch { /* private mode */ } },
  del(area, key) { try { window[area].removeItem(key); } catch { /* private mode */ } },
};

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...HEADER, ...(options.headers || {}) } });
  if (!response.ok) {
    let detail = "";
    try { detail = (await response.json()).detail; } catch { /* not JSON */ }
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join(" ");
    const error = new Error(typeof detail === "string" && detail ? detail : `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "glyph");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function plural(n, word) { return `${n} ${word}${n === 1 ? "" : "s"}`; }

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function showError(id, message) {
  const node = $(id);
  node.textContent = message || "";
  node.hidden = !message;
}

function providerLabel(id) {
  const provider = state.config?.providers.find((p) => p.id === id);
  return provider ? provider.label : id;
}

// ------------------------------------------------------------------ views

function show(view, focusId) {
  for (const name of VIEWS) $(`view-${name}`).hidden = name !== view;
  $("window-title").textContent = TITLES[view];
  if (focusId) requestAnimationFrame(() => $(focusId)?.focus({ preventScroll: true }));
}

// ------------------------------------------------------------------ compose

function renderProviders() {
  const box = $("providers");
  box.replaceChildren();
  const providers = state.config.providers;
  if (!providers.length) {
    box.classList.add("single");
    box.textContent = "No model";
    $("model-id").textContent = "";
    showError("submit-error", "No API key is configured. Add GEMINI_API_KEY or ANTHROPIC_API_KEY to .env, then restart docx-agent ui.");
    updateSubmit();
    return;
  }
  const saved = storage.get("localStorage", "docx-agent.provider");
  state.provider = providers.some((p) => p.id === saved) ? saved : state.config.default;
  if (providers.length === 1) {
    box.classList.add("single");
    box.textContent = providers[0].label;
  } else {
    const indicator = el("span", "indicator");
    box.append(indicator);
    for (const provider of providers) {
      const button = el("button", "", provider.label);
      button.type = "button";
      button.setAttribute("role", "radio");
      button.dataset.provider = provider.id;
      button.addEventListener("click", () => selectProvider(provider.id));
      box.append(button);
    }
    box.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      const ids = providers.map((p) => p.id);
      const step = event.key === "ArrowRight" ? 1 : -1;
      const next = ids[(ids.indexOf(state.provider) + step + ids.length) % ids.length];
      selectProvider(next);
      box.querySelector(`[data-provider="${next}"]`).focus();
      event.preventDefault();
    });
  }
  selectProvider(state.provider, false);
}

function selectProvider(id, remember = true) {
  state.provider = id;
  if (remember) storage.set("localStorage", "docx-agent.provider", id);
  const provider = state.config.providers.find((p) => p.id === id);
  $("model-id").textContent = provider ? provider.model : "";
  const box = $("providers");
  for (const button of box.querySelectorAll("button")) {
    const active = button.dataset.provider === id;
    button.setAttribute("aria-checked", String(active));
    button.tabIndex = active ? 0 : -1;
  }
  moveIndicator();
}

function moveIndicator() {
  const box = $("providers");
  const active = box.querySelector('button[aria-checked="true"]');
  const indicator = box.querySelector(".indicator");
  if (!active || !indicator) return;
  indicator.style.width = `${active.offsetWidth}px`;
  indicator.style.transform = `translateX(${active.offsetLeft}px)`;
}

function setFile(file) {
  showError("file-error", "");
  if (!file) {
    state.file = null;
  } else if (!/\.docx$/i.test(file.name)) {
    showError("file-error", "Choose a Word document with the .docx extension.");
    return;
  } else if (file.size > state.config.maxUploadMb * 1024 * 1024) {
    showError("file-error", `This file is ${formatSize(file.size)}; the limit is ${state.config.maxUploadMb} MB.`);
    return;
  } else {
    state.file = file;
  }
  const hasFile = Boolean(state.file);
  $("drop").querySelector(".drop-empty").hidden = hasFile;
  $("drop").querySelector(".drop-file").hidden = !hasFile;
  if (hasFile) {
    $("file-name").textContent = state.file.name;
    $("file-size").textContent = formatSize(state.file.size);
  }
  updateSubmit();
}

function updateSubmit() {
  const ready = Boolean(state.file && $("instruction").value.trim() && state.provider);
  $("submit").disabled = !ready;
}

function bindCompose() {
  const drop = $("drop");
  const input = $("file");
  input.addEventListener("change", () => setFile(input.files[0] || null));
  for (const type of ["dragenter", "dragover"]) {
    drop.addEventListener(type, (event) => { event.preventDefault(); drop.classList.add("is-over"); });
  }
  for (const type of ["dragleave", "drop"]) {
    drop.addEventListener(type, (event) => { event.preventDefault(); drop.classList.remove("is-over"); });
  }
  drop.addEventListener("drop", (event) => setFile(event.dataTransfer.files[0] || null));
  // A file dropped outside the zone must not navigate away from the app.
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => event.preventDefault());

  const instruction = $("instruction");
  instruction.addEventListener("input", updateSubmit);
  instruction.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      if (!$("submit").disabled) $("view-compose").requestSubmit();
    }
  });
  for (const example of document.querySelectorAll(".example")) {
    example.addEventListener("click", () => {
      instruction.value = example.textContent;
      updateSubmit();
      instruction.focus();
    });
  }
  $("view-compose").addEventListener("submit", (event) => { event.preventDefault(); startJob(); });
  window.addEventListener("resize", moveIndicator);
}

async function startJob() {
  const submit = $("submit");
  showError("submit-error", "");
  submit.classList.add("is-busy");
  const form = new FormData();
  form.append("file", state.file);
  form.append("instruction", $("instruction").value.trim());
  form.append("provider", state.provider);
  try {
    const job = await api("/api/jobs", { method: "POST", body: form });
    track(job);
  } catch (error) {
    showError("submit-error", error.message);
  } finally {
    submit.classList.remove("is-busy");
  }
}

// ------------------------------------------------------------------ job tracking

function track(job) {
  state.job = job;
  state.pollFailures = 0;
  storage.set("sessionStorage", "docx-agent.job", job.id);
  render(job);
}

function stopTimers() {
  clearTimeout(state.pollTimer);
  clearInterval(state.clockTimer);
  state.pollTimer = state.clockTimer = null;
}

function schedulePoll() {
  clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(poll, POLL_MS);
}

async function poll() {
  if (!state.job) return;
  try {
    const job = await api(`/api/jobs/${state.job.id}`);
    state.pollFailures = 0;
    state.job = job;
    render(job);
  } catch (error) {
    if (error.status === 404 || ++state.pollFailures > 8) {
      stopTimers();
      showFailure({ status: "error", message: error.status === 404 ? error.message : "Lost contact with the local server. Is docx-agent ui still running?" });
      return;
    }
    schedulePoll();
  }
}

function render(job) {
  if (job.status === "running") {
    renderWorking(job);
    schedulePoll();
  } else if (job.status === "question") {
    stopTimers();
    renderQuestion(job);
  } else {
    stopTimers();
    storage.del("sessionStorage", "docx-agent.job");
    if (job.status === "done") renderResult(job);
    else showFailure(job);
    loadHistory();
  }
}

function renderWorking(job) {
  const firstPaint = $("view-working").hidden;
  $("working-title").textContent = job.filename;
  $("working-instruction").textContent = job.instruction;
  const list = $("steps");
  list.replaceChildren();
  job.steps.forEach((label, index) => {
    const item = el("li", "step");
    const status = index < job.step ? "is-done" : index === job.step ? "is-current" : "is-pending";
    item.classList.add(status);
    const mark = el("span", "step-mark");
    if (status === "is-done") mark.append(icon("check"));
    item.append(mark, el("span", "step-label", label));
    if (status === "is-current" && job.detail) item.append(el("span", "step-detail", job.detail));
    list.append(item);
  });
  if (firstPaint) {
    show("working", "working-title");
    state.startedAt = Date.now() - (job.seconds || 0) * 1000;
    clearInterval(state.clockTimer);
    const tick = () => {
      const seconds = Math.max(0, Math.round((Date.now() - state.startedAt) / 1000));
      const hint = seconds < 25 ? "Usually 5–20 seconds." : "Busy models or long documents take a little longer.";
      $("elapsed").textContent = `${seconds} s · ${hint} ${providerLabel(job.provider)} · ${job.model}`;
    };
    tick();
    state.clockTimer = setInterval(tick, 500);
  }
}

function renderQuestion(job) {
  $("question-text").textContent = job.question || "";
  $("answer").value = "";
  showError("answer-error", "");
  show("question", "answer");
}

async function sendAnswer(event) {
  event.preventDefault();
  const text = $("answer").value.trim();
  if (!text) { showError("answer-error", "Type an answer first."); return; }
  const button = $("answer-submit");
  button.classList.add("is-busy");
  try {
    const job = await api(`/api/jobs/${state.job.id}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answer: text }),
    });
    $("view-working").hidden = true; // force a fresh working view (timer keeps running)
    track(job);
  } catch (error) {
    showError("answer-error", error.message);
  } finally {
    button.classList.remove("is-busy");
  }
}

// ------------------------------------------------------------------ results

function editedName(filename) {
  return `${filename.replace(/\.docx$/i, "") || "document"}.edited.docx`;
}

function metaRow(term, content) {
  const row = el("div");
  const dd = el("dd");
  if (typeof content === "string") dd.textContent = content;
  else dd.append(...content);
  row.append(el("dt", "", term), dd);
  return row;
}

function trimEqual(text, isFirst, isLast) {
  const limit = 140;
  if (text.length <= limit) return text;
  if (isFirst) return `… ${text.slice(-70)}`;
  if (isLast) return `${text.slice(0, 70)} …`;
  return `${text.slice(0, 60)} … ${text.slice(-60)}`;
}

function renderChange(change) {
  const item = el("li", "change");
  const head = el("div", "change-head");
  const kinds = { modified: "Changed", inserted: "Added", deleted: "Removed" };
  head.append(el("span", "change-where", change.location), el("span", "change-kind", kinds[change.kind] || change.kind));
  const text = el("p", "change-text");
  const segments = change.segments;
  segments.forEach((segment, index) => {
    if (segment.kind === "equal") {
      text.append(document.createTextNode(trimEqual(segment.text, index === 0, index === segments.length - 1)));
    } else {
      const tag = segment.kind === "insert" ? "ins" : "del";
      const mark = el(tag, "", segment.text);
      mark.setAttribute("aria-label", `${segment.kind === "insert" ? "added" : "removed"}: ${segment.text}`);
      text.append(mark);
    }
  });
  if (!segments.length) text.append(el("span", "gap", "(empty paragraph)"));
  item.append(head, text);
  return item;
}

function renderResult(job) {
  const result = job.result;
  const changes = result.changes || [];
  $("result-title").textContent = changes.length ? `${plural(changes.length, "change")} made` : "No visible changes";
  $("result-summary").textContent = result.summary || "";

  const meta = $("result-meta");
  meta.replaceChildren();
  meta.append(metaRow("Document", `${job.filename} → ${editedName(job.filename)}`));
  meta.append(metaRow("Model", `${providerLabel(job.provider)} · ${job.model}`));
  const verified = el("span", "verified", "Verified");
  meta.append(metaRow("Integrity", [verified, document.createTextNode(" · nothing outside these changes was altered")]));
  const ops = plural(result.edits, "operation") + (result.retries ? ` · ${plural(result.retries, "retry")}` : "");
  meta.append(metaRow("Edits", ops));
  if (result.removed?.length) meta.append(metaRow("Removed", `${result.removed.join(", ")} (as requested)`));
  meta.append(metaRow("Time", `${job.seconds} s`));
  const report = el("a", "action-link", "Download JSON ");
  report.href = `/api/jobs/${job.id}/report`;
  report.append(icon("link"));
  meta.append(metaRow("Report", [report]));

  const list = $("changes");
  list.replaceChildren(...changes.map(renderChange));
  $("download").href = `/api/jobs/${job.id}/document`;
  $("download").setAttribute("download", editedName(job.filename));
  show("result", "result-title");
}

function showFailure(job) {
  const isError = job.status === "error";
  $("failed-kind").textContent = isError ? "Model service error" : "Nothing was saved";
  $("failed-title").textContent = isError ? "The edit could not run" : "The edit was not applied";
  $("failed-message").textContent = job.message || "";
  const violations = job.result?.violations || [];
  const list = $("violations");
  list.replaceChildren(...violations.map((v) => el("li", "", v)));
  list.hidden = !violations.length;
  show("failed", "failed-title");
}

function restart(keepInputs) {
  stopTimers();
  state.job = null;
  storage.del("sessionStorage", "docx-agent.job");
  $("view-working").hidden = true;
  if (!keepInputs) {
    $("instruction").value = "";
    $("file").value = "";
    setFile(null);
  }
  updateSubmit();
  show("compose", keepInputs ? "instruction" : "drop");
}

// ------------------------------------------------------------------ history

const relative = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

function ago(iso) {
  const seconds = (new Date(iso).getTime() - Date.now()) / 1000;
  const units = [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]];
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

async function loadHistory() {
  let entries = [];
  try { entries = await api("/api/history"); } catch { return; }
  const list = $("history-list");
  list.replaceChildren();
  for (const entry of entries.slice(0, 20)) {
    const item = el("li", "history-item");
    const status = el("span", "history-status");
    if (entry.status === "done") {
      status.textContent = `Saved · ${plural(entry.changes, "change")}`;
      status.classList.add("is-saved");
    } else {
      status.textContent = entry.status === "failed" ? "Not saved" : "Error";
    }
    const side = el("span", "history-side");
    const time = el("time", "history-time", ago(entry.at));
    time.dateTime = entry.at;
    time.title = new Date(entry.at).toLocaleString();
    side.append(status, time);
    item.append(el("span", "history-name", entry.filename), side, el("span", "history-instruction", entry.instruction));
    item.title = `${entry.filename} · ${providerLabel(entry.provider)} ${entry.model}`;
    list.append(item);
  }
  $("history-clear").hidden = !entries.length;
  $("history-empty").textContent = entries.length
    ? "Only file names and instructions are kept here, never the documents."
    : "Documents you edit appear here. Only the file name and your instruction are kept, never the documents.";
}

async function clearHistory() {
  try { await api("/api/history", { method: "DELETE" }); } catch { /* keep the list */ }
  loadHistory();
}

// ------------------------------------------------------------------ boot

async function resume() {
  const id = storage.get("sessionStorage", "docx-agent.job");
  if (!id) return;
  try { track(await api(`/api/jobs/${id}`)); } catch { storage.del("sessionStorage", "docx-agent.job"); }
}

async function boot() {
  bindCompose();
  $("view-question").addEventListener("submit", sendAnswer);
  $("answer").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) $("view-question").requestSubmit();
  });
  for (const button of document.querySelectorAll('[data-action="restart"]')) button.addEventListener("click", () => restart(false));
  for (const button of document.querySelectorAll('[data-action="retry"]')) button.addEventListener("click", () => restart(true));
  $("history-clear").addEventListener("click", clearHistory);
  try {
    state.config = await api("/api/config");
  } catch (error) {
    showError("submit-error", `Could not reach the local server: ${error.message}`);
    return;
  }
  $("max-mb").textContent = state.config.maxUploadMb;
  $("version").textContent = `docx-agent ${state.config.version}`;
  renderProviders();
  updateSubmit();
  loadHistory();
  resume();
  document.fonts?.ready.then(moveIndicator);
}

boot();
