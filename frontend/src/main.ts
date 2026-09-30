import "./style.css";
import { api, type LogEntry, type StepState, type TaskState } from "./api";
import { clear, h } from "./dom";

const FORMATS = ["json", "csv", "markdown"] as const;
const MARKS: Record<StepState["state"], string> = { pending: "○", running: "◐", done: "✓", failed: "✗", blocked: "⛔" };

let taskId: string | null = null;
let timer: number | undefined;
let log: LogEntry[] = [];
let latest: TaskState | null = null;
let shotIndex = -1; // -1 = follow newest
let exportFormat: (typeof FORMATS)[number] = "json";
let exportCache = "";
let exportKey = "";

const app = document.getElementById("app") as HTMLElement;

// ---- static layout -------------------------------------------------------
const goal = h("textarea", { id: "goal", rows: 3, placeholder: "e.g. Extract the pricing table from the local demo website" });
const startUrl = h("input", { id: "start-url", placeholder: "optional start URL" });
const domains = h("input", { id: "domains", placeholder: "default: host of the start URL" });
const maxActions = h("input", { id: "max-actions", type: "number", value: "40", min: "1", max: "200" });
const format = h("select", { id: "format" }, ...FORMATS.map((f) => h("option", { value: f }, f)));
const formError = h("div", { class: "err", id: "form-error" });
const runBtn = h("button", { id: "run", onclick: () => void start() }, "Run task");
const cancelBtn = h("button", { id: "cancel", class: "secondary", disabled: true, onclick: () => void cancel() }, "Cancel");

const statusBadge = h("span", { class: "badge", id: "status-badge" }, "idle");
const banner = h("div", { id: "completion" });
const stepsEl = h("ol", { class: "steps", id: "steps" });
const notesEl = h("div", { class: "muted", id: "notes" });
const browserEl = h("dl", { class: "kv", id: "browser" });
const shotBox = h("div", { id: "shot-box" });
const historyBody = h("tbody");
const historyWrap = h("div", { class: "table-wrap" }, h("table", { id: "history" }, h("thead", {}, h("tr", {}, ...["Time", "Step", "Action", "Target", "Result", "Retry", "Error"].map((c) => h("th", {}, c)))), historyBody));
const extractedBox = h("div", { id: "extracted" });
const errorsBox = h("div", { id: "errors" });
const modalHost = h("div", { id: "modal-host" });

app.append(
  h("header", { class: "top" }, h("h1", {}, "Local Browser Agent"), statusBadge),
  h(
    "div",
    { class: "layout" },
    h(
      "div",
      { class: "col" },
      h("section", { class: "panel" }, h("h2", {}, "Task"),
        h("label", { for: "goal" }, "Goal"), goal,
        h("label", { for: "start-url" }, "Start URL"), startUrl,
        h("label", { for: "domains" }, "Allowed domains (comma separated)"), domains,
        h("div", { class: "row" }, h("div", {}, h("label", { for: "max-actions" }, "Max actions"), maxActions), h("div", {}, h("label", { for: "format" }, "Output format"), format)),
        h("div", { class: "row", style: "margin-top:12px" }, runBtn, cancelBtn), formError),
      h("section", { class: "panel" }, h("h2", {}, "Plan / current step"), stepsEl, notesEl),
      h("section", { class: "panel" }, h("h2", {}, "Errors"), errorsBox),
    ),
    h(
      "div",
      { class: "col" },
      banner,
      h("section", { class: "panel" }, h("h2", {}, "Browser"), browserEl),
      h("section", { class: "panel" }, h("h2", {}, "Screenshots"), shotBox),
      h("section", { class: "panel" }, h("h2", {}, "Action history"), historyWrap),
      h("section", { class: "panel" }, h("h2", {}, "Extracted data"), extractedBox),
    ),
  ),
  modalHost,
);

// ---- actions ---------------------------------------------------------------
async function start(): Promise<void> {
  formError.textContent = "";
  const g = goal.value.trim();
  if (!g) { formError.textContent = "Enter a goal."; return; }
  try {
    const res = await api.start({
      goal: g,
      start_url: startUrl.value.trim() || undefined,
      allowed_domains: domains.value.split(",").map((d) => d.trim()).filter(Boolean),
      max_actions: Number(maxActions.value) || 40,
      format: format.value,
    });
    taskId = res.id; log = []; latest = null; shotIndex = -1; exportCache = ""; exportKey = "";
    poll();
  } catch (e) {
    formError.textContent = (e as Error).message;
  }
}

async function cancel(): Promise<void> {
  if (taskId) await api.cancel(taskId).catch(() => undefined);
}

async function decide(approve: boolean): Promise<void> {
  if (taskId) await api.confirm(taskId, approve).catch(() => undefined);
}

function poll(): void {
  window.clearTimeout(timer);
  const tick = async () => {
    if (!taskId) return;
    try {
      const st = await api.state(taskId, log.length);
      log = log.concat(st.log);
      latest = st;
      render(st);
      if (!st.finished) timer = window.setTimeout(() => void tick(), 400);
      else render(st);
    } catch (e) {
      formError.textContent = (e as Error).message;
    }
  };
  void tick();
}

// ---- rendering ---------------------------------------------------------------
function render(st: TaskState): void {
  const running = !st.finished;
  runBtn.disabled = running;
  cancelBtn.disabled = !running;
  statusBadge.className = `badge ${st.status}`;
  statusBadge.textContent = st.status.replace("_", " ");

  clear(banner);
  banner.className = "";
  if (st.finished && st.status !== "idle") {
    banner.className = `banner ${st.status}`;
    const r = st.result;
    banner.textContent = st.status === "completed"
      ? `Completed: ${r?.steps_completed}/${r?.steps_total} steps, ${r?.actions_used} actions in ${r?.duration_s}s`
      : `${st.status[0].toUpperCase()}${st.status.slice(1)}: ${st.error ?? "see errors"}`;
  }

  clear(stepsEl);
  for (const s of st.steps) {
    stepsEl.append(h("li", { class: `state-${s.state}`, "data-state": s.state }, h("span", { class: "mark" }, MARKS[s.state]), h("span", {}, `${s.id}. ${s.description}`, s.id === st.current_step && running ? ` — ${st.phase}` : "")));
  }
  notesEl.textContent = [st.planner ? `planner: ${st.planner}` : "", ...st.plan_notes].filter(Boolean).join(" · ");

  clear(browserEl);
  const kv: [string, string][] = [["State", st.browser.state], ["Phase", st.phase], ["Current step", st.current_step ? `${st.current_step} / ${st.steps.length}` : "-"], ["URL", st.browser.url || "-"], ["Title", st.browser.title || "-"]];
  for (const [k, v] of kv) browserEl.append(h("dt", {}, k), h("dd", {}, v));

  renderShots(st);
  renderHistory();
  renderErrors(st);
  void renderExtracted(st);
  renderModal(st);
}

function renderShots(st: TaskState): void {
  clear(shotBox);
  if (!st.screenshots.length) { shotBox.append(h("span", { class: "muted" }, "No screenshots yet.")); return; }
  const idx = shotIndex >= 0 && shotIndex < st.screenshots.length ? shotIndex : st.screenshots.length - 1;
  shotBox.append(h("img", { class: "big", id: "shot-main", alt: `screenshot ${st.screenshots[idx]}`, src: api.screenshotUrl(st.id, st.screenshots[idx]) }));
  shotBox.append(h("div", { class: "shots" }, ...st.screenshots.map((name, i) =>
    h("img", { class: i === idx ? "active" : "", alt: name, title: name, src: api.screenshotUrl(st.id, name), onclick: () => { shotIndex = i; if (latest) renderShots(latest); } }))));
}

function renderHistory(): void {
  clear(historyBody);
  for (const e of log) {
    historyBody.append(h("tr", {},
      h("td", {}, e.timestamp.slice(11, 23)), h("td", {}, e.step === null ? "" : String(e.step)), h("td", {}, e.action), h("td", {}, e.target),
      h("td", { class: `r-${e.result}` }, e.result), h("td", {}, String(e.retry_count)), h("td", { class: "err" }, e.error ?? "")));
  }
  historyWrap.scrollTop = historyWrap.scrollHeight;
}

function renderErrors(st: TaskState): void {
  clear(errorsBox);
  const errs = log.filter((e) => e.error);
  if (!errs.length && !st.error) { errorsBox.append(h("span", { class: "muted" }, "No errors.")); return; }
  for (const e of errs) errorsBox.append(h("div", { class: "err" }, `step ${e.step ?? "-"} ${e.action}: ${e.error}`));
  if (st.error) errorsBox.append(h("div", { class: "err" }, h("strong", {}, "Final: "), st.error));
}

async function renderExtracted(st: TaskState): Promise<void> {
  const names = Object.keys(st.extracted);
  if (!names.length) { clear(extractedBox); extractedBox.append(h("span", { class: "muted" }, "Nothing extracted yet.")); return; }
  const key = `${st.id}:${exportFormat}:${JSON.stringify(st.extracted).length}`;
  if (key !== exportKey) {
    exportCache = await api.exportText(st.id, exportFormat);
    exportKey = key;
  }
  clear(extractedBox);
  extractedBox.append(h("div", { class: "tabs" }, ...FORMATS.map((f) => h("button", { class: f === exportFormat ? "on secondary" : "secondary", "data-format": f, onclick: () => { exportFormat = f; exportKey = ""; if (latest) void renderExtracted(latest); } }, f)),
    h("a", { href: api.exportUrl(st.id, exportFormat), download: `extracted.${exportFormat === "markdown" ? "md" : exportFormat}` }, "Download")));
  for (const name of names) {
    const v = st.extracted[name];
    if (Array.isArray(v) && v.length && typeof v[0] === "object") {
      const cols = Array.from(new Set(v.flatMap((r) => Object.keys(r as object))));
      extractedBox.append(h("div", { class: "table-wrap" }, h("table", { id: `data-${name}` }, h("caption", {}, name), h("thead", {}, h("tr", {}, ...cols.map((c) => h("th", {}, c)))),
        h("tbody", {}, ...(v as Record<string, unknown>[]).map((r) => h("tr", {}, ...cols.map((c) => h("td", {}, String(r[c] ?? ""))))))) ));
    }
  }
  extractedBox.append(h("pre", { id: "export-text" }, exportCache));
}

function renderModal(st: TaskState): void {
  clear(modalHost);
  if (!st.pending_confirmation) return;
  modalHost.append(h("div", { class: "modal-bg" }, h("div", { class: "modal", role: "dialog", "aria-modal": "true" },
    h("h3", {}, "Confirmation required"), h("p", {}, st.pending_confirmation.description),
    h("p", { class: "muted" }, "The agent will not continue until you decide."),
    h("div", { class: "row" }, h("button", { id: "approve", onclick: () => void decide(true) }, "Approve"), h("button", { id: "deny", class: "secondary", onclick: () => void decide(false) }, "Deny")))));
}

// ---- boot --------------------------------------------------------------------
api.config().then((c) => {
  if (c.default_goal && !goal.value) goal.value = c.default_goal;
}).catch(() => undefined);
render({ id: "", goal: "", status: "idle", phase: "idle", current_step: null, finished: true, plan_notes: [], planner: null, steps: [], browser: { state: "not started", url: "", title: "" }, log: [], log_total: 0, screenshots: [], extracted: {}, error: null, pending_confirmation: null, result: null, format: "json" });
cancelBtn.disabled = true;
