// three.js viewer is loaded lazily so the rest of the app never depends on WebGL
const PALETTE = ["#7389ad", "#d9a933", "#7e9e80", "#b3735f", "#9a9ca8", "#8c73a6", "#5f9ea0"];
const partColor = (i) => (i < 0 ? "transparent" : PALETTE[i % PALETTE.length]);

// ------------------------------------------------------------ helpers
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const md = (s) => (window.marked && window.DOMPurify ? DOMPurify.sanitize(marked.parse(s || "")) : `<pre>${esc(s)}</pre>`);
const money = (v) => (v == null ? "—" : "$" + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const dims = (v) => (v && v.length ? v.map((x) => +(+x).toFixed(1)).join(" × ") + " mm" : "—");
const clock = (t) => (t ? new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "");
const ago = (t) => {
  if (!t) return "";
  const s = Date.now() / 1000 - t;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString();
};
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
};

async function api(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {}), ...(opts.headers || {}) } };
  if (opts.body && typeof opts.body !== "string" && !(opts.body instanceof FormData)) init.body = JSON.stringify(opts.body);
  const r = await fetch(path, init);
  if (!r.ok) {
    let d = r.statusText;
    try { d = (await r.json()).detail || d; } catch { /* not json */ }
    throw new Error(d);
  }
  return r.json();
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 3200);
}

const theme = store.get("theme", "system");
if (theme !== "system") document.documentElement.dataset.theme = theme;

const OPS = [
  { op: 10, stage: "intake", name: "Intake" },
  { op: 20, stage: "review", name: "Engineering review" },
  { op: 30, stage: "plan", name: "Model plan" },
  { op: 40, stage: "build", name: "Build & test" },
  { op: 50, stage: "memo", name: "Technical memo" },
  { op: 60, stage: "machining", name: "Production plan" },
  { op: 70, stage: "release", name: "Release" },
];
const STAGE_IDX = { intake: 0, review: 1, plan: 2, build: 3, memo: 4, machining: 5, release: 6, released: 7 };
const AGENT_LABEL = { engineer: "Lead engineer", machinist: "Machinist", "sub-engineer": "Sub-engineer", system: "Workflow", user: "You" };
const PROCESS_LABEL = { fdm: "FDM printing", sla: "SLA resin", sls: "SLS nylon", cnc_mill: "CNC milling", laser_cut: "Laser cutting", injection_molding: "Injection molding" };

const S = { health: null, route: null, viewer: null, viewerKey: null, viewerEl: null };

// ------------------------------------------------------------ routing

function parseRoute() {
  const parts = (location.hash.replace(/^#\/?/, "") || "new").split("/");
  return { name: parts[0], params: parts.slice(1) };
}

async function render() {
  S.route = parseRoute();
  const { name, params } = S.route;
  $$(".nav a").forEach((a) => a.toggleAttribute("aria-current", a.dataset.nav === name || (name === "job" && a.dataset.nav === "jobs")));
  $$(".nav a[aria-current]").forEach((a) => a.setAttribute("aria-current", "page"));
  if (name !== "job" || params[1] !== "model") disposeViewer();
  const view = $("#view");
  const fn = { new: viewNew, jobs: viewJobs, job: viewJob, approvals: viewApprovals, materials: viewMaterials, processes: viewProcesses, agents: viewAgents, dev: viewDev }[name] || viewNew;
  try {
    await fn(view, ...params);
  } catch (e) {
    view.innerHTML = `<div class="sheet"><h2>That page didn't load</h2><p>${esc(e.message)}</p><a class="btn quiet" href="#/jobs">Back to jobs</a></div>`;
  }
}
window.addEventListener("hashchange", () => { render(); $("#view").focus({ preventScroll: true }); window.scrollTo(0, 0); });

function disposeViewer() {
  if (S.viewer) S.viewer.dispose();
  S.viewer = null; S.viewerKey = null; S.viewerEl = null;
}

// ------------------------------------------------------------ health / chrome

async function loadHealth() {
  try {
    S.health = await api("/api/health");
    const b = S.health.backend;
    const label = b === "openclaw" ? "Agents via OpenClaw" : b === "ollama" ? "Models via Ollama" : b === "openai" ? "Models via API" : "Offline demo agents";
    $("#backendChip").innerHTML = `<b>${esc(label)}</b>${b === "mock" ? "No model reachable. Set one up on Dev." : esc(S.health.models.engineer) + " leads"}`;
  } catch {
    $("#backendChip").innerHTML = "<b>Server unreachable</b>Start it with ./start.sh";
  }
}

async function refreshApprovals() {
  try {
    const list = await api("/api/approvals");
    const c = $("#approvalCount");
    c.hidden = list.length === 0;
    c.textContent = list.length;
    return list;
  } catch { return []; }
}
const refreshApprovalsSoon = debounce(refreshApprovals, 300);

// ------------------------------------------------------------ New job

async function viewNew(el) {
  el.innerHTML = `
  <div class="page-head"><div><h1>What do you need made?</h1>
    <p>Describe the part. The lead engineer reviews it and asks you before anything is modeled.</p></div></div>
  <form id="newJob" class="stack">
    <div class="sheet stack">
      <div><label for="prompt">Request</label>
        <textarea id="prompt" name="prompt" class="prompt-box" required placeholder="A wall bracket that holds a 2 kg Wi-Fi router in a garage, with four M5 mounting holes"></textarea></div>
      <div class="fields">
        <div><label for="dimensions">Overall dimensions <span class="hint">W × D × H</span></label>
          <input type="text" id="dimensions" name="dimensions" placeholder="120 x 80 x 100 mm"></div>
        <div><label for="tolerance">Tolerance</label>
          <select id="tolerance" name="tolerance">
            <option value="ISO 2768-m">General, ISO 2768-m</option>
            <option value="ISO 2768-f">Fine, ISO 2768-f</option>
            <option value="ISO 2768-c">Coarse, ISO 2768-c</option>
            <option value="custom">Specific value…</option>
          </select>
          <input type="text" id="tolCustom" placeholder="±0.05 mm" hidden style="margin-top:6px"></div>
        <div><label for="application">Application</label>
          <input type="text" id="application" name="application" placeholder="Garage wall mount"></div>
        <div><label for="budget">Budget <span class="hint">USD, whole order</span></label>
          <input type="number" id="budget" name="budget" min="0" step="1" placeholder="150"></div>
        <div><label for="quantity">Quantity</label>
          <input type="number" id="quantity" name="quantity" min="1" step="1" value="1"></div>
        <div><label for="max_temp_c">Max temperature <span class="hint">°C</span></label>
          <input type="number" id="max_temp_c" name="max_temp_c" placeholder="Room temperature"></div>
      </div>
      <div class="fields">
        <div><label>Load</label><div class="chips" role="radiogroup">
          ${["light", "moderate", "heavy"].map((l) => `<label class="chip"><input type="radio" name="load" value="${l}" ${l === "light" ? "checked" : ""}><span>${l[0].toUpperCase() + l.slice(1)}</span></label>`).join("")}
        </div></div>
        <div><label>Environment</label><div class="chips">
          ${[["outdoor", "Outdoors / UV"], ["water", "Wet"], ["chemical", "Chemicals"], ["food", "Food contact"]].map(([v, l]) => `<label class="chip"><input type="checkbox" name="environment" value="${v}"><span>${l}</span></label>`).join("")}
        </div></div>
      </div>
      <div><label>Processes you can use <span class="hint">leave empty for any</span></label><div class="chips">
        ${Object.entries(PROCESS_LABEL).map(([v, l]) => `<label class="chip"><input type="checkbox" name="processes_available" value="${v}"><span>${l}</span></label>`).join("")}
      </div></div>
      <div class="fields">
        <div><label for="work_notes">Anything else about where it works</label>
          <input type="text" id="work_notes" name="work_notes" placeholder="Vibration from a garage door motor nearby"></div>
        <div><label for="ref">Reference CAD <span class="hint">optional STEP or STL</span></label>
          <input type="file" id="ref" accept=".step,.stp,.stl"></div>
      </div>
    </div>
    <div class="actions"><button class="btn big" type="submit">Start job</button>
      <span class="muted small" id="newStatus"></span></div>
  </form>`;
  const tol = $("#tolerance");
  tol.addEventListener("change", () => { $("#tolCustom").hidden = tol.value !== "custom"; });
  $("#newJob").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    const btn = f.querySelector("button[type=submit]");
    btn.disabled = true;
    const status = $("#newStatus");
    try {
      const fd = new FormData(f);
      const body = {
        prompt: fd.get("prompt"), dimensions: fd.get("dimensions"),
        tolerance: tol.value === "custom" ? $("#tolCustom").value : tol.value,
        application: fd.get("application"), budget: fd.get("budget"), quantity: fd.get("quantity"),
        max_temp_c: fd.get("max_temp_c"), load: fd.get("load"), environment: fd.getAll("environment"),
        processes_available: fd.getAll("processes_available"), work_notes: fd.get("work_notes"),
      };
      const file = $("#ref").files[0];
      if (file) {
        status.textContent = "Measuring reference file…";
        const up = new FormData();
        up.append("file", file);
        body.reference = await api("/api/upload", { method: "POST", body: up });
      }
      status.textContent = "Starting…";
      const p = await api("/api/projects", { method: "POST", body });
      location.hash = `#/job/${p.id}/traveler`;
    } catch (err) {
      status.textContent = err.message;
      btn.disabled = false;
    }
  });
}

// ------------------------------------------------------------ Jobs

function statusTag(p) {
  if (p.stage === "released") return `<span class="tag pass">Released</span>`;
  if (p.stage === "aborted") return `<span class="tag idle">Stopped</span>`;
  if (p.status === "awaiting_user") return `<span class="tag hold">Needs your approval</span>`;
  if (p.status === "running") return `<span class="tag run">Working</span>`;
  return `<span class="tag idle">${esc(p.status)}</span>`;
}
const opName = (stage) => { const o = OPS.find((x) => x.stage === stage); return o ? `Op ${o.op} ${o.name}` : stage === "released" ? "Released" : "Stopped"; };

async function viewJobs(el) {
  const list = await api("/api/projects");
  el.innerHTML = `
  <div class="page-head"><div><h1>Jobs</h1><p>Every request and where it sits on its route.</p></div>
    <a class="btn" href="#/new">New job</a></div>
  <div class="sheet rows" style="padding:0">
    ${list.length ? list.map((p) => `
      <a class="row" href="#/job/${p.id}/traveler">
        <span class="row-title">${esc(p.title)}</span>${statusTag(p)}
        <span class="row-meta"><span>${esc(opName(p.stage))}</span>${p.revision ? `<span>Rev ${esc(p.revision)}</span>` : ""}
          ${p.total_cost != null ? `<span>${money(p.total_cost)}</span>` : ""}<span>Updated ${ago(p.updated)}</span></span>
      </a>`).join("") : `<div class="empty">No jobs yet. Describe a part and the agents take it from there.<br><a class="btn" href="#/new">Start a job</a></div>`}
  </div>`;
}

// ------------------------------------------------------------ Job

function routeStrip(p) {
  const cur = p.stage === "aborted" ? -1 : STAGE_IDX[p.stage] ?? 0;
  const signed = (stage) => {
    const g = [...p.gates].reverse().find((x) => x.stage === stage && x.status === "answered" && x.kind !== "error");
    return g ? `Signed off ${clock(g.answered_at)}` : "";
  };
  return `<ol class="route" aria-label="Route">${OPS.map((o, i) => {
    let cls = "", sign = "";
    if (i === 0) { cls = "done"; sign = `Recorded ${clock(p.created)}`; }
    else if (i < cur) { cls = "done"; sign = o.stage === "build" && p.build ? `${Object.keys(p.build.parts).length} parts tested` : signed(o.stage) || "Complete"; }
    else if (i === cur) {
      cls = p.status === "awaiting_user" ? "hold" : p.status === "running" ? "active" : "";
      sign = p.status === "awaiting_user" ? "Waiting for you" : p.status === "running" ? "In progress" : "";
    }
    if (p.stage === "released" && i === 6) { cls = "done"; sign = `Rev ${p.release?.revision || ""} released`; }
    return `<li class="op ${cls}"><div class="op-num">Op ${o.op}${cls === "active" ? '<span class="pulse" aria-hidden="true"></span>' : ""}</div>
      <div class="op-name">${o.name}</div><div class="op-sign">${esc(sign)}</div></li>`;
  }).join("")}</ol>`;
}

const TABS = [["traveler", "Traveler"], ["model", "Model"], ["memo", "Memo"], ["production", "Production"], ["files", "Files"], ["log", "Log"]];

async function viewJob(el, id, tab = "traveler") {
  const p = await api(`/api/projects/${id}`);
  const scroll = window.scrollY;
  const keepViewer = tab === "model" && S.viewerEl;
  el.innerHTML = `
  <div class="page-head"><div><h1>${esc(p.title)}</h1>
    <p>${statusTag(p)} ${p.revision ? `<span class="tag idle">Rev ${esc(p.revision)}</span>` : ""}
    <span class="tag idle">Revision ${p.iteration} of ${p.gates[0]?.max_iterations || S.health?.rules?.max_iterations || 3}</span></p></div>
    ${p.status === "running" ? "" : ""}</div>
  ${routeStrip(p)}
  <nav class="tabs" aria-label="Job sections">${TABS.map(([k, l]) => `<a href="#/job/${id}/${k}" ${k === tab ? 'aria-current="page"' : ""}>${l}${k === "traveler" && p.status === "awaiting_user" ? ' <span class="count">1</span>' : ""}</a>`).join("")}</nav>
  <section id="tab"></section>`;
  const t = $("#tab");
  if (tab === "traveler") tabTraveler(t, p);
  else if (tab === "model") tabModel(t, p, keepViewer);
  else if (tab === "memo") tabMemo(t, p);
  else if (tab === "production") tabProduction(t, p);
  else if (tab === "files") tabFiles(t, p);
  else if (tab === "log") await tabLog(t, p);
  window.scrollTo(0, scroll);
}

function reqList(p) {
  const r = p.requirements, wc = r.work_conditions || {};
  const rows = [
    ["Request", esc(p.prompt)],
    ["Dimensions", esc(r.dimensions || "Not given")],
    ["Tolerance", `${esc(r.tolerance)} (±${r.tolerance_mm} mm)`],
    ["Application", esc(r.application || "—")],
    ["Budget", r.budget ? money(r.budget) : "—"],
    ["Quantity", r.quantity],
    ["Conditions", esc([wc.max_temp_c ? `${wc.max_temp_c} °C max` : "room temperature", `${wc.load} load`, ...(wc.environment || [])].join(", "))],
    ["Processes", esc((r.processes_available || []).map((x) => PROCESS_LABEL[x] || x).join(", ") || "Any")],
  ];
  if (r.reference) rows.push(["Reference", `${esc(r.reference.name)}, ${dims(r.reference.bbox_mm)}`]);
  return `<dl class="kv">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
}

function agentsOnJob(p) {
  const h = S.health?.models || {};
  const subs = p.build ? Object.entries(p.build.subassemblies) : [];
  const parts = p.build?.parts || {};
  return `<table><tbody>
    <tr><td><b>Lead engineer</b><br><span class="muted small">${esc(h.engineer || "")}</span></td><td>${["review", "plan", "build", "memo"].includes(p.stage) ? (p.status === "running" ? '<span class="tag run">Working</span>' : p.status === "awaiting_user" ? '<span class="tag hold">Waiting for you</span>' : "") : STAGE_IDX[p.stage] > 4 ? '<span class="tag pass">Handed off</span>' : ""}</td></tr>
    ${subs.map(([sid, s]) => {
      const mine = Object.values(parts).filter((x) => x.sub === sid);
      const done = mine.filter((x) => x.status === "passed").length;
      const tag = s.status === "done" ? "pass" : s.status === "blocked" ? "fail" : "run";
      return `<tr><td><b>${esc(s.agent)}</b><br><span class="muted small">${esc(sid)} sub-assembly, ${esc(s.model)}</span></td>
        <td><span class="tag ${tag}">${done}/${mine.length} parts</span></td></tr>`;
    }).join("")}
    <tr><td><b>Machinist</b><br><span class="muted small">${esc(h.machinist || "")}</span></td><td>${p.stage === "machining" ? (p.status === "running" ? '<span class="tag run">Quoting</span>' : '<span class="tag hold">Waiting for you</span>') : p.stage === "released" ? '<span class="tag pass">Released</span>' : '<span class="tag idle">Not started</span>'}</td></tr>
  </tbody></table>`;
}

function tabTraveler(t, p) {
  const open = p.gates.filter((g) => g.status === "open");
  const closed = p.gates.filter((g) => g.status === "answered");
  t.innerHTML = `
    <div class="stack">
      ${open.map((g) => `<div data-gate="${g.id}"></div>`).join("")}
      ${p.stage === "released" ? `<div class="sheet"><h2>Rev ${esc(p.release.revision)} is released</h2>
        <p>${esc(p.release.strategy)}: ${money(p.release.total_cost)}, about ${p.release.lead_days} days. Files are hashed in the manifest so this revision never changes.</p>
        <div class="actions"><a class="btn" href="/api/files/${p.id}/${p.release.zip}">Download rev ${esc(p.release.revision)} package</a><a class="btn quiet" href="#/job/${p.id}/files">See all files</a></div></div>` : ""}
      ${p.status === "running" && !open.length ? `<div class="sheet"><h3>${esc(opName(p.stage))} is in progress</h3><p class="muted">The agents stop here and ask the moment they need a decision from you.</p></div>` : ""}
      <div class="grid2">
        <div class="sheet"><h2 style="margin-bottom:12px">Requirements</h2>${reqList(p)}</div>
        <div class="sheet"><h2 style="margin-bottom:8px">Agents on this job</h2>${agentsOnJob(p)}</div>
      </div>
      <div class="sheet"><h2 style="margin-bottom:8px">Activity</h2><ul class="feed" id="feed"><li><span></span><span class="muted">Loading…</span></li></ul>
        <p class="small" style="margin-top:10px"><a href="#/job/${p.id}/log">Full log and model calls</a></p></div>
      ${closed.length ? `<details class="sheet"><summary>Sign-offs (${closed.length})</summary>
        <table style="margin-top:10px"><tbody>${closed.map((g) => `<tr><td>Op ${g.op}</td><td>${esc(g.title)}</td><td>${esc(g.response?.action === "revise" ? "Changes requested: " + (g.response.comments || "") : g.response?.action === "abort" ? "Stopped" : "Approved")}</td><td class="muted">${clock(g.answered_at)}</td></tr>`).join("")}</tbody></table></details>` : ""}
    </div>`;
  open.forEach((g) => gateCard($(`[data-gate="${g.id}"]`, t), p, g, () => render()));
  loadFeed(p.id, 14);
}

async function loadFeed(id, limit) {
  const feed = $("#feed");
  if (!feed) return;
  const log = await api(`/api/projects/${id}/log`);
  const rows = log.slice(-limit).reverse();
  feed.innerHTML = rows.length ? rows.map(feedRow).join("") : `<li><span></span><span class="muted">Nothing yet.</span></li>`;
}
const feedRow = (r) => `<li class="${esc(r.level)}"><time>${clock(r.t)}</time><span class="agent">${esc(AGENT_LABEL[r.agent] || r.agent)}</span><span class="msg">${esc(r.message)}</span></li>`;

// ------------------------------------------------------------ approval gates

function reviseBlock(g, label = "Request changes", placeholder = "What should change?") {
  if (!g.can_revise) return `<p class="small muted" style="margin-top:12px">Revision limit reached (${g.max_iterations} of ${g.max_iterations}). Approve or stop the job.</p>`;
  return `<details style="margin-top:12px"><summary>${label}</summary>
    <textarea name="comments" placeholder="${esc(placeholder)}" style="margin-top:8px"></textarea>
    <div class="actions"><button class="btn ghost" type="button" data-act="revise">Send for revision ${g.iteration + 1} of ${g.max_iterations}</button></div></details>`;
}

function gateCard(host, p, g, after) {
  const d = g.data || {};
  const who = `${AGENT_LABEL[g.agent] || g.agent}, Op ${g.op}`;
  let inner = "", primary = "Approve";
  if (g.kind === "clarify") {
    primary = "Send answers";
    inner = (d.questions || []).map((q, i) => `<div style="margin-top:12px"><label for="q${i}">${esc(q)}</label><textarea id="q${i}" name="a${i}" rows="2" style="min-height:56px"></textarea></div>`).join("");
  } else if (g.kind === "review_fixes") {
    primary = "Approve selected fixes";
    inner = `
      <h3 style="margin-top:14px">Drawbacks found</h3>
      <div>${(d.drawbacks || []).map((x) => `<div class="issue"><span class="sev ${esc(x.severity)}">${esc(x.severity)}</span><b>${esc(x.issue)}</b><div class="muted small">${esc(x.detail)}</div></div>`).join("") || '<p class="muted">None.</p>'}</div>
      <h3 style="margin-top:14px">Proposed fixes <span class="hint small">untick any you reject</span></h3>
      <div>${(d.fixes || []).map((f) => `<div class="fix"><input type="checkbox" id="fx_${f.id}" name="fix" value="${esc(f.id)}" checked><label for="fx_${f.id}">${esc(f.fix)}<span class="muted small"> ${f.addresses ? "for " + esc(f.addresses) : ""}</span></label></div>`).join("")}</div>
      ${(d.assumptions || []).length ? `<details style="margin-top:10px"><summary>Assumptions the engineer made</summary><ul>${d.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></details>` : ""}
      <div style="margin-top:12px"><label for="fixc">Comments for the engineer <span class="hint">optional</span></label><textarea id="fixc" name="fixcomments" rows="2" style="min-height:56px"></textarea></div>`;
  } else if (g.kind === "plan") {
    primary = d.plan?.spawn_sub_engineers ? `Approve and summon ${d.plan.sub_engineer_count} sub-engineers` : "Approve plan and build";
    const pl = d.plan;
    inner = `<div class="table-wrap" style="margin-top:12px"><table><thead><tr><th>Sub-assembly</th><th>Part</th><th>Envelope</th><th>Features</th><th>Material hint</th></tr></thead><tbody>
      ${pl.subassemblies.map((s) => s.parts.map((pt, i) => `<tr><td>${i === 0 ? `<b>${esc(s.name)}</b>` : ""}</td><td>${esc(pt.name)}${pt.quantity > 1 ? ` ×${pt.quantity}` : ""}<div class="muted small">${esc(pt.description)}</div></td><td>${dims(pt.envelope_mm)}</td><td class="small">${pt.features.map(esc).join("<br>")}</td><td class="small">${esc(pt.material_hint)}</td></tr>`).join("")).join("")}
      </tbody></table></div>
      ${pl.risks?.length ? `<details style="margin-top:8px"><summary>Risks the engineer is tracking</summary><ul>${pl.risks.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></details>` : ""}
      ${reviseBlock(g, "Request plan changes")}`;
  } else if (g.kind === "sub_question") {
    primary = "Send answers";
    inner = (d.questions || []).map((q, i) => `<div style="margin-top:12px"><label for="sq${i}">${esc(q.agent)} on ${esc(q.part)}: ${esc(q.question)}</label><textarea id="sq${i}" name="a${i}" rows="2" style="min-height:56px"></textarea></div>`).join("");
  } else if (g.kind === "build_failure") {
    primary = "Retry with this guidance";
    inner = Object.entries(d.details || {}).map(([pid, x]) => `<div class="issue"><b>${esc(x.name)}</b>
      <ul class="small">${(x.tests || []).filter((t) => !t.passed).map((t) => `<li>${esc(t.name)}: ${esc(t.detail)}</li>`).join("")}</ul>
      ${x.last_error?.length ? `<details><summary class="small">Last error</summary><pre>${esc(x.last_error.join("\n"))}</pre></details>` : ""}</div>`).join("") +
      `<div style="margin-top:12px"><label for="bg">Guidance for the sub-engineers</label><textarea id="bg" name="comments" rows="2" placeholder="Model the gusset as a simple extruded triangle; skip the fillets"></textarea></div>`;
  } else if (g.kind === "memo") {
    primary = "Sign memo and send to machinist";
    inner = `<details open style="margin-top:12px"><summary>Technical memo</summary><div class="md">${md(p.memo)}</div></details>${reviseBlock(g, "Request a revision")}`;
  } else if (g.kind === "production") {
    primary = "Approve strategy and release files";
    const study = p.manufacturing.study;
    inner = `<div class="grid3" style="margin-top:14px">${study.strategies.map((s) => strategyCard(s, d.recommended, true)).join("")}</div>
      ${(d.dfm || []).length ? `<h3 style="margin-top:14px">Machinist's DFM notes</h3><ul>${d.dfm.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      ${g.can_revise ? `<details style="margin-top:12px" ${d.send_back ? "open" : ""}><summary>Send back to the engineer</summary>
        <textarea name="comments" style="margin-top:8px">${esc(d.send_back ? d.send_back_reason : "")}</textarea>
        <div class="actions"><button class="btn ghost" type="button" data-act="revise">Send back for revision ${g.iteration + 1} of ${g.max_iterations}</button></div></details>` : reviseBlock(g)}`;
  } else if (g.kind === "error") {
    primary = "Retry this operation";
    inner = d.trace ? `<details style="margin-top:8px"><summary class="small">Details</summary><pre>${esc(d.trace)}</pre></details>` : "";
  }
  host.innerHTML = `<form class="holdtag" novalidate>
    <div class="who">${esc(who)}</div><h2>${esc(g.title)}</h2>
    ${g.body ? `<div class="md">${md(g.body)}</div>` : ""}
    ${inner}
    <div class="actions"><button class="btn" type="submit">${esc(primary)}</button>
      <button class="btn danger" type="button" data-act="abort">Stop job</button></div>
  </form>`;
  const form = $("form", host);
  const send = async (action) => {
    const fd = new FormData(form);
    const body = { action };
    if (action === "approve") {
      if (g.kind === "clarify" || g.kind === "sub_question") body.answers = Object.fromEntries([...fd.entries()].filter(([k]) => /^a\d+$/.test(k)).map(([k, v]) => [k.slice(1), v]));
      if (g.kind === "review_fixes") { body.accepted = fd.getAll("fix"); body.comments = fd.get("fixcomments"); }
      if (g.kind === "build_failure") body.comments = fd.get("comments");
      if (g.kind === "production") body.strategy = fd.get("strategy");
    }
    if (action === "revise") {
      body.comments = fd.get("comments");
      if (g.kind === "production" && (d.dfm || []).length) body.extra = "Machinist DFM notes: " + d.dfm.join("; ");
    }
    if (action === "abort" && !confirm("Stop this job? It can't be resumed.")) return;
    $$("button", form).forEach((b) => (b.disabled = true));
    try {
      await api(`/api/projects/${p.id}/gates/${g.id}`, { method: "POST", body });
      toast(action === "revise" ? "Sent back for revision" : action === "abort" ? "Job stopped" : "Signed off. The agents are back at work.");
      refreshApprovals();
      after?.();
    } catch (e) {
      toast(e.message);
      $$("button", form).forEach((b) => (b.disabled = false));
    }
  };
  form.addEventListener("submit", (e) => { e.preventDefault(); send("approve"); });
  $$("[data-act]", form).forEach((b) => b.addEventListener("click", () => send(b.dataset.act)));
}

function strategyCard(s, recommended, pickable, selected) {
  const status = !s.feasible ? `<span class="tag fail">Can't make ${s.unproducible_parts.length} part(s)</span>` : s.within_budget ? `<span class="tag pass">Within budget</span>` : `<span class="tag warn">Over budget</span>`;
  const tagName = pickable ? "label" : "div";
  return `<${tagName} class="strategy ${pickable ? "pick" : ""}">
    ${pickable ? `<input type="radio" name="strategy" value="${s.id}" ${s.id === recommended ? "checked" : ""} ${s.feasible ? "" : "disabled"} aria-label="${esc(s.label)}">` : ""}
    <h3>${esc(s.label)}</h3>
    ${s.id === recommended ? `<div class="rec">Machinist's pick</div>` : ""}${selected === s.id ? `<div class="rec">Selected</div>` : ""}
    <div class="price">${money(s.total_cost)}</div>
    <div class="small muted">${s.lead_days} days lead time</div>
    <div style="margin-top:8px">${status}</div>
    <ul>${s.assignments.map((a) => `<li>${esc(a.part_id)}: ${esc(a.process_name)}, ${esc(a.material_name)} ×${a.quantity} at ${money(a.unit_cost)}</li>`).join("")}</ul>
  </${tagName}>`;
}

// ------------------------------------------------------------ Model tab

function tabModel(t, p, keepViewer) {
  const b = p.build;
  if (!b) { t.innerHTML = `<div class="sheet empty">The 3D model appears once the plan is approved and the sub-engineers start building.</div>`; disposeViewer(); return; }
  const placed = !!b.assembly?.files?.step;
  const list = [];
  p.plan.subassemblies.forEach((s) => s.parts.forEach((pt) => {
    const st = b.parts[pt.id];
    if (st?.status === "passed") list.push({ id: pt.id, url: `/api/files/${p.id}/${b.dir}/${placed ? "placed" : "parts"}/${pt.id}.stl` });
  }));
  const key = list.map((x) => x.url).join("|");
  const allParts = p.plan.subassemblies.flatMap((s) => s.parts.map((pt) => ({ s, pt, st: b.parts[pt.id] })));
  t.innerHTML = `
    <div class="stack">
      <div id="viewerSlot"></div>
      <div class="sheet table-wrap"><h2 style="margin-bottom:8px">Parts in rev ${esc(b.revision)}</h2>
        <table><thead><tr><th></th><th>Part</th><th>Sub-assembly</th><th>Built by</th><th>Size</th><th class="num">Volume</th><th class="num">Attempts</th><th>Tests</th></tr></thead><tbody>
        ${allParts.map(({ s, pt, st }, i) => {
          const passedN = (st.tests || []).filter((x) => x.passed).length;
          const tag = st.status === "passed" ? "pass" : st.status === "failed" ? "fail" : st.status === "question" ? "hold" : "run";
          return `<tr data-part="${pt.id}"><td><span style="display:inline-block;width:12px;height:12px;border-radius:2px;background:${partColor(list.findIndex((x) => x.id === pt.id))}"></span></td>
            <td><b>${esc(pt.name)}</b>
              <details class="small"><summary>Tests and code</summary>
                <ul>${(st.tests || []).map((x) => `<li><span class="tag ${x.passed ? "pass" : x.severity === "warn" ? "warn" : "fail"}">${x.passed ? "pass" : x.severity}</span> ${esc(x.name)}: ${esc(x.detail)}</li>`).join("")}</ul>
                ${st.notes ? `<p>${esc(st.notes)}</p>` : ""}${st.code ? `<pre>${esc(st.code)}</pre>` : ""}</details></td>
            <td>${esc(s.name)}</td><td>${esc(b.subassemblies[s.id]?.agent || "")}</td><td>${dims(st.metrics?.bbox_mm)}</td>
            <td class="num">${st.metrics ? (st.metrics.volume_mm3 / 1000).toFixed(1) + " cm³" : "—"}</td><td class="num">${st.attempts}</td>
            <td><span class="tag ${tag}">${st.status === "passed" ? `${passedN}/${st.tests.length} pass` : esc(st.status)}</span></td></tr>`;
        }).join("")}</tbody></table></div>
      ${b.assembly ? `<div class="sheet"><h2 style="margin-bottom:8px">Assembly tests</h2><ul class="feed">${b.assembly.tests.map((x) => `<li class="${x.passed ? "pass" : "fail"}"><span class="tag ${x.passed ? "pass" : "fail"}">${x.passed ? "pass" : "fail"}</span><span class="agent">${esc(x.name)}</span><span class="msg">${esc(x.detail)}</span></li>`).join("")}</ul></div>` : ""}
    </div>`;
  const slot = $("#viewerSlot");
  if (keepViewer && S.viewerKey === key && S.viewerEl) {
    slot.replaceWith(S.viewerEl);
  } else {
    disposeViewer();
    const v = document.createElement("div");
    v.className = "viewer";
    v.innerHTML = `<div class="legend">${list.length ? (placed ? "Assembled" : "Parts shown in their own frames until assembly") + ". Drag to orbit, scroll to zoom." : "No parts have passed testing yet."}</div>`;
    slot.replaceWith(v);
    if (list.length) {
      S.viewerKey = key;
      S.viewerEl = v;
      import("./viewer.js").then(({ mountViewer }) => {
        if (S.viewerEl === v) S.viewer = mountViewer(v, list);
      }).catch(() => { v.querySelector(".legend").textContent = "3D preview needs WebGL; download the STEP or STL from Files."; });
    }
  }
  $$("tr[data-part]", t).forEach((tr) => {
    tr.addEventListener("mouseenter", () => S.viewer?.highlight(tr.dataset.part));
    tr.addEventListener("mouseleave", () => S.viewer?.highlight(null));
  });
}

// ------------------------------------------------------------ Memo / Production / Files / Log

function tabMemo(t, p) {
  t.innerHTML = p.memo
    ? `<div class="sheet md">${md(p.memo)}</div>`
    : `<div class="sheet empty">The lead engineer writes the memo after every part and the assembly pass testing.</div>`;
}

function tabProduction(t, p) {
  const m = p.manufacturing;
  if (!m) { t.innerHTML = `<div class="sheet empty">The machinist quotes production after you sign the technical memo.</div>`; return; }
  const rv = m.review;
  t.innerHTML = `<div class="stack">
    <div class="sheet"><h2 style="margin-bottom:8px">Machinist's recommendation</h2><p>${esc(rv.rationale)}</p>
      ${rv.process_notes?.length ? `<h3 style="margin-top:12px">Shop notes</h3><ul>${rv.process_notes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      <p class="small muted">${m.slicer ? "FDM times come from PrusaSlicer." : "PrusaSlicer not found; FDM times are volume estimates."}</p></div>
    <div class="grid3">${m.study.strategies.map((s) => strategyCard(s, rv.recommended_strategy, false, m.selected)).join("")}</div>
    <div class="sheet"><h2 style="margin-bottom:8px">Every option per part</h2>
      ${Object.entries(m.study.options).map(([pid, o]) => `<details><summary>${esc(pid)} <span class="muted small">(${o.total_qty} needed, ${o.top.length} options shown)</span></summary>
        <div class="table-wrap"><table><thead><tr><th>Process</th><th>Material</th><th class="num">Unit</th><th class="num">Material</th><th class="num">Machine</th><th class="num">Hours</th><th class="num">Mass</th><th>Notes</th></tr></thead><tbody>
        ${o.top.map((a) => `<tr><td>${esc(a.process_name)}</td><td>${esc(a.material_name)}</td><td class="num">${money(a.unit_cost)}</td><td class="num">${money(a.breakdown.material)}</td><td class="num">${money(a.breakdown.machine)}</td><td class="num">${a.machine_hours.toFixed(2)}</td><td class="num">${a.mass_g} g</td><td class="small">${esc(a.notes.join("; "))}</td></tr>`).join("")}
        </tbody></table></div>
        ${Object.keys(o.blocked_reasons).length ? `<p class="small muted" style="margin-top:6px">Ruled out: ${Object.entries(o.blocked_reasons).map(([k, v]) => `${esc(k)} (${esc(v)})`).join("; ")}</p>` : ""}
      </details>`).join("")}
      <ul class="small muted" style="margin-top:12px">${m.study.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></div>
  </div>`;
}

function tabFiles(t, p) {
  const b = p.build;
  if (!b) { t.innerHTML = `<div class="sheet empty">Files appear as soon as the first part passes testing.</div>`; return; }
  const f = (path, label) => `<a href="/api/files/${p.id}/${path}">${esc(label)}</a>`;
  const onshapeBtn = b.assembly?.files?.step ? (p.onshape ? `<a class="btn quiet" href="${esc(p.onshape.url)}" target="_blank" rel="noopener">Open in Onshape</a>`
    : S.health?.onshape ? `<button class="btn quiet" id="toOnshape">Send to Onshape</button>` : `<a class="btn quiet" href="#/dev">Connect Onshape</a>`) : "";
  let html = "";
  if (p.release) {
    const man = p.release.manifest.files;
    html += `<div class="sheet"><h2>Released rev ${esc(p.release.revision)}</h2><p class="muted">Stable files: each one is hashed (SHA-256) in manifest.json.</p>
      <div class="actions"><a class="btn" href="/api/files/${p.id}/${p.release.zip}">Download package (.zip)</a>${onshapeBtn}</div>
      <div class="table-wrap" style="margin-top:14px"><table><thead><tr><th>File</th><th class="num">Size</th><th>SHA-256</th></tr></thead><tbody>
      ${Object.entries(man).map(([name, x]) => `<tr><td>${f(`${p.release.dir}/${name}`, name)}</td><td class="num">${(x.bytes / 1024).toFixed(1)} KB</td><td class="small muted">${x.sha256.slice(0, 16)}…</td></tr>`).join("")}
      <tr><td>${f(`${p.release.dir}/manifest.json`, "manifest.json")}</td><td></td><td></td></tr></tbody></table></div></div>`;
  }
  html += `<div class="sheet" style="margin-top:16px"><h2>Working files, rev ${esc(b.revision)}</h2>
    ${!p.release ? `<div class="actions">${onshapeBtn}</div>` : ""}
    <div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>Part</th><th>STEP</th><th>STL</th><th>CadQuery source</th></tr></thead><tbody>
    ${Object.entries(b.parts).map(([pid, st]) => `<tr><td>${esc(pid)}</td><td>${st.files.step ? f(st.files.step, "STEP") : "—"}</td><td>${st.files.stl ? f(st.files.stl, "STL") : "—"}</td><td>${st.files.code ? f(st.files.code, ".py") : "—"}</td></tr>`).join("")}
    ${b.assembly?.files?.step ? `<tr><td><b>Assembly</b></td><td>${f(b.assembly.files.step, "STEP")}</td><td></td><td></td></tr>` : ""}
    </tbody></table></div></div>`;
  t.innerHTML = html;
  $("#toOnshape")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    try { const r = await api(`/api/projects/${p.id}/onshape`, { method: "POST" }); toast("Sent to Onshape"); window.open(r.url, "_blank"); render(); }
    catch (err) { toast(err.message); e.target.disabled = false; }
  });
}

async function tabLog(t, p) {
  const [log, trace] = await Promise.all([api(`/api/projects/${p.id}/log`), api(`/api/projects/${p.id}/trace`)]);
  t.innerHTML = `<div class="stack">
    <div class="sheet"><h2 style="margin-bottom:8px">Activity</h2><ul class="feed" id="feed">${log.slice().reverse().map(feedRow).join("")}</ul></div>
    <div class="sheet"><h2 style="margin-bottom:8px">Model calls (${trace.length})</h2>
      ${trace.slice().reverse().map((c) => `<details class="issue"><summary>${esc(c.role)} / ${esc(c.task)} <span class="muted small">${esc(c.backend)} ${esc(c.model)}, ${c.latency_s}s, ${clock(c.t)}</span>${c.error ? ' <span class="tag fail">error</span>' : ""}</summary>
        <h3 class="small" style="margin:8px 0 4px">Prompt</h3><pre>${esc(c.messages.map((m) => `[${m.role}]\n${m.content}`).join("\n\n"))}</pre>
        <h3 class="small" style="margin:8px 0 4px">Response</h3><pre>${esc(c.error || c.response)}</pre></details>`).join("") || '<p class="muted">No model calls yet.</p>'}
    </div></div>`;
}

// ------------------------------------------------------------ Approvals

async function viewApprovals(el) {
  const list = await refreshApprovals();
  el.innerHTML = `<div class="page-head"><div><h1>Approvals</h1><p>Every place an agent is holding for your decision.</p></div></div>
    <div class="stack" id="tags">${list.length ? list.map((g) => `
      <div class="holdtag" data-p="${g.project}" data-g="${g.id}">
        <div class="who">${esc(AGENT_LABEL[g.agent] || g.agent)}, Op ${g.op}, ${ago(g.created)}</div>
        <h2>${esc(g.title)}</h2><p class="muted">${esc(g.project_title)}</p>
        <div class="actions"><button class="btn" data-open>Review</button><a class="btn quiet" href="#/job/${g.project}/traveler">Open job</a></div>
      </div>`).join("") : `<div class="sheet empty">Nothing is waiting on you. Agents hold here whenever they need a decision.</div>`}</div>`;
  $$("[data-open]", el).forEach((btn) => btn.addEventListener("click", async () => {
    const tag = btn.closest(".holdtag");
    const p = await api(`/api/projects/${tag.dataset.p}`);
    const g = p.gates.find((x) => x.id === tag.dataset.g);
    const host = document.createElement("div");
    tag.replaceWith(host);
    gateCard(host, p, g, () => render());
  }));
}

// ------------------------------------------------------------ Materials

async function viewMaterials(el, filter = "all") {
  const { materials, _note } = await api("/api/data/materials");
  const yes = (v) => (v ? "Yes" : "—");
  el.innerHTML = `<div class="page-head"><div><h1>Materials</h1><p>${esc(_note)}</p></div></div>
    <div class="chips" style="margin-bottom:12px" role="tablist">${[["all", "All"], ...Object.entries(PROCESS_LABEL)].map(([k, l]) => `<a class="chip" href="#/materials/${k}"><span style="${k === filter ? "background:var(--dykem);color:var(--on-dykem);border-color:var(--dykem)" : ""}">${l}</span></a>`).join("")}</div>
    <input type="text" id="mq" placeholder="Search materials" style="max-width:320px;margin-bottom:12px" aria-label="Search materials">
    <div class="sheet table-wrap" style="padding:0"><table id="mt"><thead><tr><th>Material</th><th>Processes</th><th class="num">Density</th><th class="num">$/kg</th><th class="num">Max °C</th><th class="num">Tensile</th><th>Outdoor</th><th>Wet</th><th>Chemical</th><th>Food</th><th>Notes</th></tr></thead><tbody>
    ${materials.filter((m) => filter === "all" || m.processes.includes(filter)).map((m) => `<tr data-q="${esc((m.name + " " + m.notes + " " + m.class).toLowerCase())}"><td><b>${esc(m.name)}</b></td><td class="small">${m.processes.map((x) => PROCESS_LABEL[x] || x).join(", ")}</td>
      <td class="num">${m.density_g_cm3} g/cm³</td><td class="num">${money(m.cost_per_kg)}</td><td class="num">${m.max_temp_c}</td><td class="num">${m.tensile_mpa} MPa</td>
      <td>${yes(m.outdoor_uv)}</td><td>${yes(m.water)}</td><td>${yes(m.chemical)}</td><td>${yes(m.food_safe)}</td><td class="small muted">${esc(m.notes)}</td></tr>`).join("")}
    </tbody></table></div>`;
  $("#mq").addEventListener("input", (e) => {
    const q = e.target.value.toLowerCase();
    $$("#mt tbody tr").forEach((tr) => (tr.hidden = q && !tr.dataset.q.includes(q)));
  });
}

// ------------------------------------------------------------ Processes (+ quick quote)

async function viewProcesses(el) {
  const [{ processes }, tol] = await Promise.all([api("/api/data/processes"), api("/api/data/tolerances")]);
  el.innerHTML = `<div class="page-head"><div><h1>Processes</h1><p>What the machinist can choose from, with the shop rates it uses.</p></div></div>
  <div class="stack">
    <div class="grid3">${processes.map((p) => `<div class="sheet"><h2>${esc(p.name)}</h2><p class="small muted" style="margin:4px 0 10px">${esc(p.best_for)}</p>
      <dl class="kv small"><dt>Tolerance</dt><dd>±${p.tolerance_mm} mm</dd><dt>Min wall</dt><dd>${p.min_wall_mm} mm</dd><dt>Envelope</dt><dd>${dims(p.envelope_mm)}</dd>
      <dt>Machine rate</dt><dd>${money(p.machine_rate_per_hr)}/h</dd><dt>Setup</dt><dd>${money(p.setup_cost)}</dd><dt>Labor</dt><dd>${money(p.labor_per_part)}/part</dd>
      ${p.tooling_base ? `<dt>Tooling</dt><dd>from ${money(p.tooling_base)}</dd>` : ""}<dt>Lead time</dt><dd>${p.lead_days} days</dd><dt>Finish</dt><dd>${esc(p.surface)}</dd></dl></div>`).join("")}</div>
    <div class="sheet"><h2 style="margin-bottom:6px">Quick quote</h2><p class="muted small">Cost a single part without running the agents.</p>
      <form id="qq" class="fields" style="margin-top:10px">
        <div><label for="qd">Bounding box <span class="hint">mm</span></label><input type="text" id="qd" value="100 x 60 x 20"></div>
        <div><label for="qv">Volume <span class="hint">cm³</span></label><input type="number" id="qv" value="60" step="any"></div>
        <div><label for="qn">Quantity</label><input type="number" id="qn" value="10" min="1"></div>
        <div><label for="qt">Tolerance</label><select id="qt"><option>ISO 2768-m</option><option>ISO 2768-f</option><option>ISO 2768-c</option></select></div>
        <div style="align-self:end"><button class="btn" type="submit">Quote</button></div>
      </form><div id="qqOut" class="grid3" style="margin-top:14px"></div></div>
    <div class="grid2">
      <div class="sheet table-wrap"><h2 style="margin-bottom:8px">ISO 2768-1 general tolerances</h2><table><thead><tr><th>Nominal (mm)</th><th class="num">Fine</th><th class="num">Medium</th><th class="num">Coarse</th><th class="num">Very coarse</th></tr></thead><tbody>
        ${tol.iso2768_linear.ranges.map((r) => `<tr><td>${r.over}–${r.up_to}</td>${["f", "m", "c", "v"].map((k) => `<td class="num">${r[k] == null ? "—" : "±" + r[k]}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
      <div class="sheet table-wrap"><h2 style="margin-bottom:8px">Common fits</h2><table><tbody>${tol.fits.map((f) => `<tr><td><b>${esc(f.fit)}</b></td><td class="small">${esc(f.type)}</td><td class="small">${esc(f.use)}</td></tr>`).join("")}</tbody></table></div>
    </div>
    <div class="sheet"><h2 style="margin-bottom:8px">Design rules the agents follow</h2><table><tbody>${tol.design_rules.map((r) => `<tr><td class="small">${esc(PROCESS_LABEL[r.process] || r.process)}</td><td>${esc(r.rule)}</td></tr>`).join("")}</tbody></table></div>
  </div>`;
  $("#qq").addEventListener("submit", async (e) => {
    e.preventDefault();
    const bb = ($("#qd").value.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
    if (bb.length < 3) return toast("Enter three dimensions, like 100 x 60 x 20");
    const v = Number($("#qv").value) * 1000;
    const area = 2 * (bb[0] * bb[1] + bb[1] * bb[2] + bb[0] * bb[2]) * 0.8;
    try {
      const r = await api("/api/estimate", { method: "POST", body: { bbox_mm: bb, volume_mm3: v, area_mm2: area, quantity: Number($("#qn").value), tolerance: $("#qt").value } });
      $("#qqOut").innerHTML = r.strategies.map((s) => strategyCard(s, null, false)).join("");
    } catch (err) { toast(err.message); }
  });
}

// ------------------------------------------------------------ Agents

async function viewAgents(el) {
  await loadHealth();
  const h = S.health;
  const via = h.backend === "openclaw" ? "OpenClaw agent" : h.backend === "ollama" ? "Ollama model" : h.backend === "openai" ? "API model" : "Offline demo";
  const card = (name, model, agentId, does, gates) => `<div class="sheet"><h2>${name}</h2>
    <dl class="kv small" style="margin:10px 0"><dt>Model</dt><dd>${esc(model)}</dd><dt>${via}</dt><dd>${esc(h.backend === "openclaw" ? agentId : (h.backend === "ollama" || h.backend === "openai") ? model : "deterministic responses")}</dd></dl>
    <ul>${does.map((x) => `<li>${x}</li>`).join("")}</ul><h3 style="margin-top:10px">Holds for your approval when</h3><ul>${gates.map((x) => `<li>${x}</li>`).join("")}</ul></div>`;
  el.innerHTML = `<div class="page-head"><div><h1>Agents</h1><p>One workflow, three roles. No designer stage: the lead engineer works straight from your request.</p></div></div>
  <div class="grid3">
    ${card("Lead engineer", h.models.engineer, h.openclaw_agents.engineer,
      ["Reviews the request for drawbacks and proposes fixes", "Plans the model and splits it into sub-assemblies", `Summons sub-engineers when a plan has ${h.rules.sub_engineer_threshold}+ parts`, "Assembles, checks interference, writes the technical memo"],
      ["It needs information that changes the design", "Fixes are proposed", "The plan is ready (before any sub-engineer starts)", "Parts fail testing after every retry", "The memo is ready for sign-off"])}
    ${card("Sub-engineers", h.models.sub_engineer, h.openclaw_agents.sub_engineer,
      ["Each owns one sub-assembly and models its parts in CadQuery", `Up to ${h.rules.max_parallel_sub_engineers} run in parallel`, `Every part is tested; failures are fed back for up to ${h.rules.max_build_attempts} attempts`],
      ["The part spec is contradictory or missing a dimension"])}
    ${card("Machinist", h.models.machinist, h.openclaw_agents.machinist,
      ["Costs every process and material from the shop datasets", "Filters by tolerance, temperature, load and environment", "Slices FDM parts with PrusaSlicer when installed", "Packages stable, hashed release files"],
      ["It needs shop or budget information", "A production strategy must be chosen", "It recommends sending the design back"])}
  </div>
  <div class="sheet" style="margin-top:16px"><h2 style="margin-bottom:8px">Workflow rules</h2>
    <dl class="kv"><dt>Revision limit</dt><dd>${h.rules.max_iterations} revisions per job (plan changes, memo revisions and send-backs share it)</dd>
    <dt>Build attempts</dt><dd>${h.rules.max_build_attempts} per part before the engineer asks you</dd>
    <dt>Approvals</dt><dd>No agent moves past a checkpoint without your sign-off</dd></dl>
    <p class="small" style="margin-top:10px"><a href="#/dev">Change models and limits on Dev</a></p></div>`;
}

// ------------------------------------------------------------ Dev

async function viewDev(el) {
  const [cfg] = await Promise.all([api("/api/config"), loadHealth()]);
  const h = S.health;
  const pr = h.probe;
  const field = (k, label, type = "text", hint = "") => `<div><label for="c_${k}">${label}${hint ? ` <span class="hint">${hint}</span>` : ""}</label><input type="${type}" id="c_${k}" name="${k}" value="${esc(cfg[k])}"></div>`;
  const projects = await api("/api/projects");
  el.innerHTML = `<div class="page-head"><div><h1>Dev</h1><p>Backends, models, limits and a CAD sandbox for testing agent output.</p></div></div>
  <div class="stack">
    <div class="sheet"><h2 style="margin-bottom:10px">Status</h2>
      <dl class="kv"><dt>Active backend</dt><dd><b>${esc(h.backend)}</b> (setting: ${esc(h.backend_setting)})</dd>
      <dt>OpenClaw gateway</dt><dd><span class="tag ${pr.openclaw.ok ? "pass" : "fail"}">${pr.openclaw.ok ? "reachable" : "off"}</span> ${esc(pr.openclaw.detail)}</dd>
      <dt>Ollama</dt><dd><span class="tag ${pr.ollama.ok ? (pr.ollama.missing.length ? "warn" : "pass") : "fail"}">${pr.ollama.ok ? "reachable" : "off"}</span> ${esc(pr.ollama.detail)}</dd>
      <dt>OpenAI-compatible API</dt><dd><span class="tag ${pr.openai?.ok ? "pass" : "idle"}">${pr.openai?.ok ? "reachable" : "off"}</span> ${esc(pr.openai?.detail || "")}</dd>
      <dt>PrusaSlicer</dt><dd>${h.slicer ? esc(h.slicer) : "Not found (FDM times are estimated)"}</dd>
      <dt>Onshape</dt><dd>${h.onshape ? "Keys set" : "Not connected"}</dd></dl>
      <div class="actions">${["backend", "cad", "slicer", "onshape"].map((x) => `<button class="btn quiet" data-test="${x}">Test ${x === "cad" ? "CAD engine" : x}</button>`).join("")}</div>
      <pre id="testOut" hidden style="margin-top:10px"></pre></div>

    <form class="sheet stack" id="cfg"><h2>Configuration</h2>
      <div class="fields">
        <div><label for="c_backend">Backend</label><select id="c_backend" name="backend">${["auto", "openclaw", "ollama", "openai", "mock"].map((b) => `<option ${cfg.backend === b ? "selected" : ""}>${b}</option>`).join("")}</select></div>
        ${field("openclaw_url", "OpenClaw gateway URL")}${field("openclaw_token", "Gateway token", "password")}${field("ollama_url", "Ollama URL")}
        ${field("openai_base_url", "OpenAI-compatible base URL", "text", "e.g. https://openrouter.ai/api/v1")}${field("openai_api_key", "API key", "password")}
        ${field("engineer_model", "Lead engineer model")}${field("sub_engineer_model", "Sub-engineer model")}${field("machinist_model", "Machinist model")}
        ${field("num_ctx", "Context window", "number", "tokens")}${field("temperature", "Temperature", "number")}
        ${field("max_iterations", "Revision limit", "number")}${field("max_build_attempts", "Build attempts per part", "number")}
        ${field("max_parallel_sub_engineers", "Parallel sub-engineers", "number")}${field("sub_engineer_threshold", "Summon sub-engineers at", "number", "parts")}
        ${field("cad_timeout_s", "CAD timeout", "number", "seconds")}${field("slicer_path", "PrusaSlicer path", "text", "blank = auto")}
        ${field("onshape_access_key", "Onshape access key", "password")}${field("onshape_secret_key", "Onshape secret key", "password")}
        <div><label for="themeSel">Theme</label><select id="themeSel">${["system", "light", "dark"].map((x) => `<option ${store.get("theme", "system") === x ? "selected" : ""}>${x}</option>`).join("")}</select></div>
      </div>
      <div class="actions"><button class="btn" type="submit">Save configuration</button></div></form>

    <div class="sheet stack"><h2>CAD sandbox</h2><p class="muted small">Runs code through the same screening and tests the sub-engineers face.</p>
      <textarea id="sbCode" rows="9" style="font-family:ui-monospace,Menlo,monospace;font-size:.85rem">import cadquery as cq
result = (cq.Workplane("XY").box(80, 40, 10, centered=(True, True, False))
          .faces(">Z").workplane().rarray(50, 1, 2, 1).hole(6))</textarea>
      <div class="fields"><div><label for="sbEnv">Envelope <span class="hint">mm, optional</span></label><input type="text" id="sbEnv" value="80 x 40 x 10"></div>
        <div style="align-self:end"><button class="btn" id="sbRun" type="button">Run tests</button></div></div>
      <div id="sbOut"></div></div>

    <div class="sheet"><h2 style="margin-bottom:8px">Raw job data</h2>
      <p class="small">${projects.map((p) => `<a href="/api/projects/${p.id}" target="_blank" rel="noopener">${esc(p.title)}</a>`).join(" | ") || "No jobs yet."}</p></div>
  </div>`;
  $$("[data-test]", el).forEach((b) => b.addEventListener("click", async () => {
    const out = $("#testOut");
    out.hidden = false; out.textContent = "Running…";
    try { out.textContent = JSON.stringify(await api(`/api/dev/test/${b.dataset.test}`, { method: "POST" }), null, 2); }
    catch (e) { out.textContent = e.message; }
  }));
  $("#themeSel").addEventListener("change", (e) => {
    store.set("theme", e.target.value);
    if (e.target.value === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = e.target.value;
  });
  $("#cfg").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = {};
    for (const [k, v] of fd.entries()) body[k] = typeof cfg[k] === "number" ? Number(v) : v;
    try { await api("/api/config", { method: "PUT", body }); toast("Configuration saved"); render(); loadHealth(); }
    catch (err) { toast(err.message); }
  });
  $("#sbRun").addEventListener("click", async () => {
    const env = ($("#sbEnv").value.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
    $("#sbOut").innerHTML = '<p class="muted">Running…</p>';
    try {
      const r = await api("/api/cad/run", { method: "POST", body: { code: $("#sbCode").value, envelope_mm: env.length === 3 ? env : null } });
      $("#sbOut").innerHTML = `<p><span class="tag ${r.ok ? "pass" : "fail"}">${r.ok ? "All tests pass" : "Tests failed"}</span></p>
        <ul class="feed">${r.tests.map((x) => `<li class="${x.passed ? "pass" : "fail"}"><span class="tag ${x.passed ? "pass" : x.severity === "warn" ? "warn" : "fail"}">${x.passed ? "pass" : x.severity}</span><span class="agent">${esc(x.name)}</span><span class="msg">${esc(x.detail)}</span></li>`).join("")}</ul>
        ${r.traceback ? `<pre>${esc(r.traceback)}</pre>` : ""}${r.metrics ? `<pre>${esc(JSON.stringify(r.metrics, null, 2))}</pre>` : ""}`;
    } catch (err) { $("#sbOut").innerHTML = `<p class="tag fail">${esc(err.message)}</p>`; }
  });
}

// ------------------------------------------------------------ live updates

const reloadJob = debounce(() => { if (S.route?.name === "job") render(); }, 450);
const reloadJobs = debounce(() => { if (S.route?.name === "jobs") render(); }, 600);

function connectEvents() {
  const es = new EventSource("/api/events");
  es.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    refreshApprovalsSoon();
    reloadJobs();
    if (S.route?.name !== "job" || S.route.params[0] !== ev.project) return;
    if (ev.type === "log") {
      const feed = $("#feed");
      if (feed) feed.insertAdjacentHTML("afterbegin", feedRow(ev));
      if (ev.level === "pass" && S.route.params[1] === "model") reloadJob();
    } else if (ev.type === "state" || ev.type === "gate") {
      // don't wipe a form the user is filling in
      if (document.activeElement?.closest?.(".holdtag") && ev.type === "state") return;
      if (S.route.params[1] === "model" || S.route.params[1] === "traveler" || ev.type === "gate" || !S.route.params[1]) reloadJob();
    }
  };
}

loadHealth();
refreshApprovals();
connectEvents();
render();
setInterval(loadHealth, 30000);
