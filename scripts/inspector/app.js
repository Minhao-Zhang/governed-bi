/* Experiment inspector — frontend logic.
   Vanilla JS, no build step. Every value that comes from the run is written to the DOM
   with textContent (or as a value/attribute set programmatically), never via innerHTML,
   so SQL / questions / JSON that contain markup are displayed, not executed. */

"use strict";

const state = {
  dbPath: "",
  runs: [],
  run: null,
  capabilities: {},
  filters: { arm: "", db: "", outcome: "", verdict: "", q: "" },
  sort: "question_id",
  dir: "asc",
  offset: 0,
  limit: 100,
  total: 0,
  selected: null, // { arm, question_id }
};

/* ---------- tiny DOM helper ---------- */
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "html") throw new Error("innerHTML is not allowed");
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else if (k === "dataset") for (const [dk, dv] of Object.entries(v)) node.dataset[dk] = dv;
      else node.setAttribute(k, v);
    }
  }
  for (const c of children) {
    if (c == null) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

const $ = (id) => document.getElementById(id);

/* ---------- fetch with error surfacing ---------- */
async function api(path, params) {
  const url = new URL(path, window.location.origin);
  if (params) for (const [k, v] of Object.entries(params)) if (v !== "" && v != null) url.searchParams.set(k, v);
  const resp = await fetch(url);
  const data = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
  if (!resp.ok || data.error) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

function toast(msg, isError) {
  const box = $("global-status");
  box.textContent = msg;
  box.className = "global-status" + (isError ? " error" : "");
  box.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { box.hidden = true; }, isError ? 8000 : 3000);
}

/* ---------- formatting ---------- */
const fmt = {
  num: (v) => (v == null ? "—" : Number(v).toLocaleString()),
  dec: (v, d = 2) => (v == null ? "—" : Number(v).toFixed(d)),
  pct: (v) => (v == null ? "—" : (Number(v) * 100).toFixed(1) + "%"),
  usd: (v) => (v == null ? "—" : "$" + Number(v).toFixed(4)),
  bool: (v) => (v == null ? "—" : v ? "yes" : "no"),
  text: (v) => (v == null || v === "" ? "—" : String(v)),
};

/* ---------- list columns ---------- */
const LIST_COLS = [
  { key: "verdict", label: "", sort: "correct", render: renderVerdictCell, num: false },
  { key: "arm", label: "Arm", sort: "arm" },
  { key: "question_id", label: "Question", sort: "question_id", cls: "qid-cell" },
  { key: "db_id", label: "Schema", sort: "db_id" },
  { key: "outcome", label: "Outcome", sort: "outcome" },
  { key: "failed_stage", label: "Failed at", sort: "failed_stage", render: (r) => fmt.text(r.failed_stage) },
  { key: "pick_hit", label: "Pick", sort: "pick_hit", num: true, render: (r) => hitMark(r.pick_hit) },
  { key: "routed_hit", label: "Route", sort: "routed_hit", num: true, render: (r) => hitMark(r.routed_hit) },
  { key: "n_tool_calls_total", label: "Tools", sort: "n_tool_calls_total", num: true, render: (r) => fmt.num(r.n_tool_calls_total) },
  { key: "latency_sec", label: "Latency", sort: "latency_sec", num: true, render: (r) => (r.latency_sec == null ? "—" : fmt.dec(r.latency_sec, 1) + "s") },
  { key: "total_tokens", label: "Tokens", sort: "total_tokens", num: true, render: (r) => fmt.num(r.total_tokens) },
  { key: "cost_est_usd", label: "Cost", sort: "cost_est_usd", num: true, render: (r) => fmt.usd(r.cost_est_usd) },
];

function verdictOf(row) {
  if (row.correct === 1 || row.correct === true) return "pass";
  if (row.correct === 0 || row.correct === false) return "fail";
  return "ungraded";
}
function renderVerdictCell(row) {
  const v = verdictOf(row);
  return el("span", { class: "verdict-dot " + v, title: v, "aria-label": v, role: "img" });
}
function hitMark(v) {
  if (v == null) return "—";
  return v ? "✓" : "✗";
}

/* ---------- init ---------- */
async function init() {
  wireControls();
  try {
    const { runs, db_path } = await api("/api/runs");
    state.runs = runs;
    state.dbPath = db_path || "";
    if (db_path) { $("db-path").textContent = db_path; $("db-path").title = db_path; }
    if (!runs.length) {
      setListStatus("This database holds no runs.", false);
      return;
    }
    const sel = $("run-select");
    sel.replaceChildren(...runs.map((r) =>
      el("option", { value: r.run_dir },
        `${r.run_dir}  ·  ${r.model || "?"}  ·  ${r.n_turns} turns`)));
    await selectRun(runs[0].run_dir);
  } catch (e) {
    setListStatus("Could not load runs: " + e.message, true);
    toast(e.message, true);
  }
}

function wireControls() {
  $("run-select").addEventListener("change", (e) => selectRun(e.target.value));
  $("filter-arm").addEventListener("change", (e) => { state.filters.arm = e.target.value; state.offset = 0; loadTurns(); });
  $("filter-db").addEventListener("change", (e) => { state.filters.db = e.target.value; state.offset = 0; loadTurns(); });
  $("filter-outcome").addEventListener("change", (e) => { state.filters.outcome = e.target.value; state.offset = 0; loadTurns(); });
  $("filter-verdict").addEventListener("change", (e) => { state.filters.verdict = e.target.value; state.offset = 0; loadTurns(); });

  let searchTimer;
  $("filter-search").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.filters.q = e.target.value; state.offset = 0; loadTurns(); }, 250);
  });

  $("filter-reset").addEventListener("click", () => {
    state.filters = { arm: "", db: "", outcome: "", verdict: "", q: "" };
    $("filter-arm").value = ""; $("filter-db").value = ""; $("filter-outcome").value = "";
    $("filter-verdict").value = ""; $("filter-search").value = "";
    state.offset = 0; loadTurns();
  });

  $("page-prev").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); loadTurns(); });
  $("page-next").addEventListener("click", () => { state.offset += state.limit; loadTurns(); });

  // A deep link changed in the address bar (or via back/forward) with no reload.
  window.addEventListener("hashchange", applyDeepLink);

  const ov = $("overview-toggle");
  ov.addEventListener("click", () => {
    const body = $("overview-body");
    const open = body.hidden;
    body.hidden = !open;
    ov.textContent = open ? "Hide" : "Show";
    ov.setAttribute("aria-expanded", String(open));
  });
}

/* ---------- run selection + overview ---------- */
async function selectRun(runDir) {
  state.run = runDir;
  state.selected = null;
  clearDetail();
  const runRow = state.runs.find((r) => r.run_dir === runDir);

  try {
    const ov = await api("/api/overview", { run: runDir });
    state.capabilities = ov.capabilities || {};
    renderCapabilities(ov.capabilities, runRow);
    renderOverview(ov);
    populateFacets(ov.facets);
    // reset filters for the new run
    state.filters = { arm: "", db: "", outcome: "", verdict: "", q: "" };
    $("filter-search").value = "";
    state.offset = 0;
    state.sort = "question_id"; state.dir = "asc";
    await loadTurns();
    // Honour a deep link (#<arm>/<question_id>) once the run is loaded. selectTurn
    // fetches the turn directly, so it resolves even when the turn is not on the
    // current list page or filtered out of it.
    applyDeepLink();
  } catch (e) {
    setListStatus("Could not load run: " + e.message, true);
    toast(e.message, true);
  }
}

/* Select whatever turn the URL hash points at, if it is not already selected.
   Called after a run loads and on every hashchange, so a pasted/edited deep link
   opens the detail without a full reload. */
function applyDeepLink() {
  const want = parseHash();
  if (!want || !want.arm || !want.question_id) return;
  const cur = state.selected;
  if (cur && cur.arm === want.arm && cur.question_id === want.question_id) return;
  selectTurn(want.arm, want.question_id);
}

function renderCapabilities(caps, runRow) {
  const wrap = $("capabilities");
  const badges = [];
  const cap = (ok, on, off) => el("span", { class: "badge " + (ok ? "ok" : "off"), title: ok ? on : off }, ok ? on : off);
  badges.push(cap(caps.stage_events, "trajectory ✓", "no stage events"));
  badges.push(cap(caps.gold_sql, "gold SQL ✓", "no gold SQL"));
  if (runRow && runRow.quotable === 0) badges.push(el("span", { class: "badge warn", title: "not marked quotable in runs/index.jsonl" }, "not quotable"));
  wrap.replaceChildren(...badges);
}

function renderOverview(ov) {
  $("overview").hidden = false;
  const run = ov.run || {};
  const meta = $("run-meta");
  const kv = (label, value) => el("span", { class: "kv" }, el("b", { text: label + ": " }), fmt.text(value));
  meta.replaceChildren(
    kv("model", run.model),
    kv("split", run.split),
    kv("mode", run.mode),
    kv("questions", run.n_questions),
    kv("turns", run.n_turns),
    kv("events", run.n_events),
    kv("completed", run.completed_at_utc || "did not finish"),
    kv("seq source", run.seq_source),
  );

  const notes = $("run-notes");
  const noteList = Array.isArray(run.notes) ? run.notes : [];
  notes.replaceChildren(...noteList.map((n) => el("div", { class: "note" }, "⚠ " + n)));

  const tbody = $("arm-table").querySelector("tbody");
  tbody.replaceChildren(...(ov.per_arm || []).map((a) =>
    el("tr", null,
      el("td", { class: "arm-name" }, a.arm),
      el("td", { class: "num" }, fmt.num(a.n_turns)),
      el("td", { class: "num" }, fmt.num(a.n_graded)),
      el("td", { class: "num" }, fmt.num(a.n_correct)),
      el("td", { class: "num ex-cell" }, fmt.pct(a.ex)),
      el("td", { class: "num" }, a.n_crashed ? `${a.n_crashed} (${fmt.pct(a.crash_rate)})` : "0"),
      el("td", { class: "num" }, fmt.num(a.n_pick_hit)),
      el("td", { class: "num" }, fmt.num(a.n_routed_hit)),
      el("td", { class: "num" }, a.avg_latency_sec == null ? "—" : a.avg_latency_sec + "s"),
      el("td", { class: "num" }, fmt.num(a.total_tokens)),
      el("td", { class: "num" }, fmt.usd(a.total_cost_usd)),
    )));
}

function populateFacets(facets) {
  const fill = (id, values, label) => {
    const sel = $(id);
    sel.replaceChildren(el("option", { value: "" }, label));
    for (const v of values || []) sel.append(el("option", { value: v }, v));
    sel.value = "";
  };
  fill("filter-arm", facets.arms, "All arms");
  fill("filter-db", facets.dbs, "All schemas");
  fill("filter-outcome", facets.outcomes, "All outcomes");
}

/* ---------- turn list ---------- */
function setListStatus(msg, isError, spinning) {
  const s = $("list-status");
  s.className = "pane-status" + (isError ? " error" : "");
  s.replaceChildren(spinning ? el("span", { class: "spinner" }) : null, document.createTextNode(msg));
}

function renderListHead() {
  const tr = el("tr");
  for (const col of LIST_COLS) {
    const active = state.sort === col.sort;
    const th = el("th", {
      class: (col.num ? "num " : "") + (col.sort ? "sortable" : ""),
      scope: "col",
      role: col.sort ? "button" : null,
      tabindex: col.sort ? "0" : null,
      "aria-sort": active ? (state.dir === "asc" ? "ascending" : "descending") : (col.sort ? "none" : null),
    }, col.label);
    if (active) th.append(el("span", { class: "sort-arrow", "aria-hidden": "true" }, state.dir === "asc" ? "▲" : "▼"));
    if (col.sort) {
      const doSort = () => {
        if (state.sort === col.sort) state.dir = state.dir === "asc" ? "desc" : "asc";
        else { state.sort = col.sort; state.dir = "asc"; }
        state.offset = 0; loadTurns();
      };
      th.addEventListener("click", doSort);
      th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); doSort(); } });
    }
    tr.append(th);
  }
  $("turn-head").replaceChildren(tr);
}

async function loadTurns() {
  renderListHead();
  setListStatus("Loading…", false, true);
  $("turn-body").replaceChildren();
  try {
    const data = await api("/api/turns", {
      run: state.run, arm: state.filters.arm, db: state.filters.db,
      outcome: state.filters.outcome, verdict: state.filters.verdict, q: state.filters.q,
      sort: state.sort, dir: state.dir, limit: state.limit, offset: state.offset,
    });
    state.total = data.total;
    renderTurnRows(data.rows);
    updatePager();
    if (!data.rows.length) setListStatus("No turns match these filters.", false);
    else $("list-status").replaceChildren();
  } catch (e) {
    setListStatus("Could not load turns: " + e.message, true);
    toast(e.message, true);
  }
}

function renderTurnRows(rows) {
  const body = $("turn-body");
  body.replaceChildren(...rows.map((r) => {
    const tr = el("tr", {
      tabindex: "0",
      dataset: { arm: r.arm, qid: r.question_id },
      "aria-label": `${r.arm} ${r.question_id} ${verdictOf(r)}`,
    });
    if (state.selected && state.selected.arm === r.arm && state.selected.question_id === r.question_id) tr.classList.add("selected");
    for (const col of LIST_COLS) {
      const content = col.render ? col.render(r) : fmt.text(r[col.key]);
      tr.append(el("td", { class: (col.num ? "num " : "") + (col.cls || "") }, content));
    }
    const open = () => selectTurn(r.arm, r.question_id);
    tr.addEventListener("click", open);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); open(); } });
    return tr;
  }));
}

function updatePager() {
  const from = state.total === 0 ? 0 : state.offset + 1;
  const to = Math.min(state.offset + state.limit, state.total);
  $("list-count").textContent = `${state.total.toLocaleString()} turn${state.total === 1 ? "" : "s"}`;
  $("page-info").textContent = state.total ? `${from.toLocaleString()}–${to.toLocaleString()}` : "0";
  $("page-prev").disabled = state.offset <= 0;
  $("page-next").disabled = to >= state.total;
}

/* ---------- detail ---------- */
function clearDetail() {
  $("detail-empty").hidden = false;
  $("detail-empty").className = "pane-status detail-empty";
  $("detail-empty").replaceChildren(el("p", null, "Select a turn to inspect its trajectory."));
  $("detail-body").hidden = true;
  $("detail-body").replaceChildren();
}

function updateHash() {
  if (state.selected) {
    const h = `#${encodeURIComponent(state.selected.arm)}/${encodeURIComponent(state.selected.question_id)}`;
    if (location.hash !== h) history.replaceState(null, "", h);
  }
}

/* Deep link: #<arm>/<question_id> selects that turn once turns are loaded. */
function parseHash() {
  const h = decodeURIComponent(location.hash.replace(/^#/, ""));
  const slash = h.indexOf("/");
  if (slash < 1) return null;
  return { arm: h.slice(0, slash), question_id: h.slice(slash + 1) };
}

async function selectTurn(arm, questionId) {
  state.selected = { arm, question_id: questionId };
  updateHash();
  for (const tr of $("turn-body").children) {
    tr.classList.toggle("selected", tr.dataset.arm === arm && tr.dataset.qid === questionId);
  }
  $("detail-empty").hidden = false;
  $("detail-empty").className = "pane-status detail-empty";
  $("detail-empty").replaceChildren(el("span", { class: "spinner" }), document.createTextNode("Loading turn…"));
  $("detail-body").hidden = true;
  try {
    const data = await api("/api/turn", { run: state.run, arm, question_id: questionId });
    renderDetail(data);
  } catch (e) {
    $("detail-empty").className = "pane-status error";
    $("detail-empty").replaceChildren(el("p", null, "Could not load turn: " + e.message));
    toast(e.message, true);
  }
}

function section(title, count) {
  const h = el("h3", null, title);
  if (count != null) h.append(el("span", { class: "count" }, ` (${count})`));
  return el("div", { class: "detail-section" }, h);
}

function codeBlock(text, emptyLabel) {
  if (text == null || text === "") return el("div", { class: "code-block empty" }, emptyLabel || "not recorded");
  return el("pre", { class: "code-block" }, String(text));
}

function metaCell(k, v, dim) {
  return el("div", { class: "meta-cell" },
    el("span", { class: "k" }, k),
    el("span", { class: "v" + (dim ? " dim" : "") }, v));
}

function renderDetail(data) {
  const d = data.detail || {};
  const x = data.extras || {};
  const body = $("detail-body");
  const v = verdictOf(d);
  const frag = document.createDocumentFragment();

  /* header */
  const title = el("div", { class: "detail-title" },
    el("span", { class: "qid" }, d.question_id),
    el("span", { class: "db" }, "· " + fmt.text(d.db_id)),
    el("span", { class: "badge " + v }, v),
    el("span", { class: "badge" }, fmt.text(d.outcome)),
  );
  const siblings = el("div", { class: "sibling-arms", role: "group", "aria-label": "arms" },
    ...(data.siblings || []).map((s) => {
      const sv = verdictOf(s);
      const b = el("button", {
        class: "sibling-arm" + (s.arm === d.arm ? " current" : ""),
        type: "button", title: `${s.arm}: ${sv} / ${fmt.text(s.outcome)}`,
        "aria-current": s.arm === d.arm ? "true" : null,
      }, el("span", { class: "verdict-dot " + sv, "aria-hidden": "true" }), s.arm);
      if (s.arm !== d.arm) b.addEventListener("click", () => selectTurn(s.arm, d.question_id));
      return b;
    }));
  frag.append(el("div", { class: "detail-header" }, title, siblings));

  /* meta grid */
  const grid = el("div", { class: "meta-grid" },
    metaCell("Arm", d.arm),
    metaCell("Difficulty", fmt.text(d.difficulty)),
    metaCell("Tier", fmt.text(d.tier)),
    metaCell("Semantic assurance", fmt.text(d.semantic_assurance)),
    metaCell("Failed stage", fmt.text(d.failed_stage)),
    metaCell("Refused by", fmt.text(d.refused_by)),
    metaCell("Pick hit", hitMark(d.pick_hit)),
    metaCell("Routed hit", hitMark(d.routed_hit)),
    metaCell("Schema pick", fmt.text(d.schema_pick)),
    metaCell("Gold schema rank", fmt.text(d.gold_schema_rank)),
    metaCell("Rows pred/gold", `${fmt.text(d.pred_nrows)} / ${fmt.text(d.gold_nrows)}`),
    metaCell("nrows match", hitMark(d.nrows_match)),
    metaCell("Attempts", fmt.text(d.attempts)),
    metaCell("Tool calls", fmt.text(d.n_tool_calls_total)),
    metaCell("Latency", d.latency_sec == null ? "—" : fmt.dec(d.latency_sec, 2) + "s"),
    metaCell("Tokens (in/out)", `${fmt.num(d.input_tokens)} / ${fmt.num(d.output_tokens)}`),
    metaCell("Total tokens", fmt.num(d.total_tokens)),
    metaCell("Cost", fmt.usd(d.cost_est_usd)),
    metaCell("Context chars", fmt.num(d.context_chars)),
    metaCell("Notes injected", fmt.text(d.n_notes_injected)),
  );
  frag.append(grid);

  /* error, if any */
  if (d.error) {
    const s = section("Error");
    s.append(el("div", { class: "meta-cell" }, el("span", { class: "k" }, fmt.text(d.error_type) + (d.failed_layer ? " · " + d.failed_layer : ""))));
    s.append(codeBlock(d.error));
    frag.append(s);
  }

  /* question / evidence */
  const qs = section("Question");
  qs.append(el("div", { class: "prose" }, fmt.text(d.question)));
  if (d.evidence) qs.append(el("div", { class: "evidence" }, "Evidence: " + d.evidence));
  frag.append(qs);

  /* gold + generated SQL */
  const gold = section("Gold SQL");
  gold.append(codeBlock(d.gold_sql, data.detail.gold_sql === null ? "not recorded (no questions.jsonl)" : "empty"));
  frag.append(gold);

  const gen = section("Generated SQL");
  gen.append(codeBlock(d.generated_sql, "no SQL produced"));
  frag.append(gen);

  /* trajectory — governance ledger */
  const ledger = Array.isArray(x.governance_ledger) ? x.governance_ledger : [];
  const traj = section("Tool trajectory", ledger.length);
  if (!ledger.length) {
    traj.append(el("div", { class: "pane-status" }, "No tool calls recorded for this turn."));
  } else {
    traj.append(el("div", { class: "trajectory" }, ...ledger.map((step, i) => renderTrajStep(step, i))));
  }
  frag.append(traj);

  /* tool histogram */
  const hist = x.n_tool_calls && typeof x.n_tool_calls === "object" ? x.n_tool_calls : null;
  if (hist && Object.keys(hist).length) {
    const s = section("Tool call counts");
    const entries = Object.entries(hist).sort((a, b) => b[1] - a[1]);
    const max = Math.max(...entries.map(([, n]) => n), 1);
    s.append(el("div", { class: "tool-hist" }, ...entries.map(([name, n]) =>
      el("div", { class: "tool-row" },
        el("span", { class: "name" }, name),
        el("div", { class: "tool-bar-track" }, el("div", { class: "tool-bar", style: `width:${(n / max) * 100}%` })),
        el("span", { class: "n" }, String(n))))));
    frag.append(s);
  }

  /* rail timeline (stage events) */
  const events = data.events || [];
  const tl = section("Stage timeline", events.length);
  if (!events.length) {
    tl.append(el("div", { class: "pane-status" }, state.capabilities.stage_events ? "No stage events for this turn." : "This run has no stage events."));
  } else {
    const maxMs = Math.max(...events.map((e) => e.ms || 0), 1);
    tl.append(el("div", { class: "timeline" }, ...events.map((e) => renderTimelineRow(e, maxMs))));
  }
  frag.append(tl);

  /* context lists */
  const lic = Array.isArray(x.licensed_tables) ? x.licensed_tables : [];
  const used = Array.isArray(x.tables_used) ? x.tables_used : [];
  if (lic.length || used.length) {
    const s = section("Tables");
    if (used.length) { s.append(el("div", { class: "meta-cell" }, el("span", { class: "k" }, "used by generated SQL"))); s.append(el("div", { class: "chips" }, ...used.map((t) => el("span", { class: "chip" }, t)))); }
    if (lic.length) { s.append(el("div", { class: "meta-cell", style: "margin-top:8px" }, el("span", { class: "k" }, `licensed (${lic.length})`))); s.append(el("div", { class: "chips" }, ...lic.map((t) => el("span", { class: "chip" }, t)))); }
    frag.append(s);
  }

  /* raw json */
  const raw = el("details", { class: "raw" },
    el("summary", null, "Raw generation row"),
    codeBlock(JSON.stringify(data.raw, null, 2)));
  frag.append(raw);

  body.replaceChildren(frag);
  $("detail-empty").hidden = true;
  body.hidden = false;
  body.scrollTop = 0;
}

function renderTrajStep(step, i) {
  const verdict = step.verdict || (step.allowed ? "pass" : null);
  const vcls = verdict === "pass" ? "pass" : verdict === "refused" || verdict === "blocked" || verdict === "fail" ? "fail" : "";
  const head = el("div", { class: "traj-step-head" },
    el("span", { class: "idx" }, "#" + (i + 1)),
    el("span", { class: "action" }, fmt.text(step.action)),
    verdict ? el("span", { class: "badge " + vcls }, verdict) : null,
    el("span", { class: "meta" },
      step.layer ? el("span", null, "layer: " + step.layer) : null,
      step.row_count != null ? el("span", null, "rows: " + fmt.num(step.row_count)) : null,
    ),
  );
  const wrap = el("div", { class: "traj-step" }, head);
  if (step.sql) wrap.append(el("pre", { class: "code-block" }, step.sql));
  else if (step.query) wrap.append(el("div", { class: "traj-detail" }, "query: " + step.query));
  // any other keys worth showing
  const shown = new Set(["action", "verdict", "layer", "row_count", "sql", "query", "allowed"]);
  const rest = Object.entries(step).filter(([k, v]) => !shown.has(k) && v != null && v !== "");
  if (rest.length) wrap.append(el("div", { class: "traj-detail" }, rest.map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join("  ·  ")));
  return wrap;
}

function renderTimelineRow(e, maxMs) {
  const statusCls = e.status === "ok" ? "ok" : e.status === "skipped" ? "warn" : "fail";
  const detailStr = e.detail && Object.keys(e.detail).length ? JSON.stringify(e.detail) : "";
  const row = el("div", { class: "tl-row" },
    el("span", { class: "seq" }, String(e.seq)),
    el("span", { class: "stage" }, fmt.text(e.stage)),
    el("span", { class: "status" }, el("span", { class: "badge " + statusCls }, fmt.text(e.status))),
    el("span", { class: "ms" }, e.ms == null ? "—" : fmt.dec(e.ms, 1) + " ms"),
    el("div", { class: "tl-bar-wrap" }, el("div", { class: "tl-bar", style: `width:${((e.ms || 0) / maxMs) * 100}%` })),
  );
  if (detailStr) row.append(el("div", { class: "tl-detail" }, detailStr));
  return row;
}

document.addEventListener("DOMContentLoaded", init);
