"""The local view's single page. No external assets, no framework, no fonts.

Design notes, because they are decisions rather than taste:

*Document-first review.* Flagged names are highlighted inside the document
text, not listed in a sidebar. Deciding whether "Warren" is a person, a place,
or the company needs the sentence around it. A list of detected strings makes
that judgement impossible and quietly encourages clicking "confirm" on
everything, which defeats the point of asking.

*The dial is always on screen, and so are the facts.* The facts panel sits
beside the reading and does not change when the dial moves. The invariant is
not something the user has to take on faith — it is something they watch hold.

*Nothing here looks like a network.* No sync language, no cloud imagery, no
progress spinner implying upload. The one genuine egress is announced plainly
at the moment it happens, and nowhere else.
"""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>__TITLE__</title>
<style>
  :root {
    --paper: #faf9f7; --ink: #1a1c1e; --muted: #5f6469; --line: #e2ded8;
    --panel: #ffffff; --accent: #3d5a80; --accent-soft: #eaf0f7;
    --pending: #b26a00; --pending-soft: #fdf3e2;
    --confirmed: #2f6b4f; --confirmed-soft: #e8f2ec;
    --warn: #8a4b2a; --warn-soft: #fbeee7;
    --radius: 8px;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --paper: #16181a; --ink: #e8e6e3; --muted: #9aa0a6; --line: #2c2f33;
      --panel: #1d2023; --accent: #8bb0d9; --accent-soft: #1e2833;
      --pending: #e0a35a; --pending-soft: #2a2118;
      --confirmed: #7fc0a0; --confirmed-soft: #18241d;
      --warn: #d99a75; --warn-soft: #2a1e18;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--paper); color: var(--ink);
    font: 15px/1.6 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  header {
    border-bottom: 1px solid var(--line); padding: 14px 22px;
    display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
  }
  header h1 { font-size: 15px; font-weight: 600; margin: 0; letter-spacing: .01em; }
  header .local { color: var(--muted); font-size: 13px; }
  header nav { margin-left: auto; display: flex; gap: 6px; }
  main { max-width: 1180px; margin: 0 auto; padding: 26px 22px 80px; }
  h2 { font-size: 17px; font-weight: 600; margin: 0 0 6px; }
  p.lede { color: var(--muted); margin: 0 0 20px; max-width: 66ch; }
  section.card {
    background: var(--panel); border: 1px solid var(--line);
    border-radius: var(--radius); padding: 20px 22px; margin-bottom: 18px;
  }
  button {
    font: inherit; font-size: 14px; padding: 8px 14px; border-radius: 6px;
    border: 1px solid var(--line); background: var(--panel); color: var(--ink);
    cursor: pointer;
  }
  button:hover:not(:disabled) { border-color: var(--accent); }
  button:disabled { opacity: .45; cursor: default; }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  @media (prefers-color-scheme: dark) { button.primary { color: #10151b; } }
  button.ghost { background: transparent; }
  button.small { padding: 4px 9px; font-size: 13px; }
  input, select, textarea {
    font: inherit; font-size: 14px; padding: 8px 10px; border-radius: 6px;
    border: 1px solid var(--line); background: var(--paper); color: var(--ink);
    width: 100%;
  }
  textarea { min-height: 150px; resize: vertical; }
  label { display: block; font-size: 13px; color: var(--muted); margin: 0 0 4px; }
  .field { margin-bottom: 14px; }
  .row { display: flex; gap: 14px; flex-wrap: wrap; }
  .row > * { flex: 1 1 220px; }
  .actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 6px; }
  .hidden { display: none !important; }
  .muted { color: var(--muted); }
  .small { font-size: 13px; }
  .notice {
    border-left: 3px solid var(--accent); background: var(--accent-soft);
    padding: 12px 16px; border-radius: 0 6px 6px 0; margin-bottom: 16px;
  }
  .notice.warn { border-left-color: var(--warn); background: var(--warn-soft); }
  .notice.error { border-left-color: #b3261e; background: var(--warn-soft); }

  /* -- the document, and the flags inside it -- */
  .doc {
    font: 16px/1.85 ui-serif, Georgia, "Times New Roman", serif;
    white-space: pre-wrap; word-wrap: break-word;
    max-height: 58vh; overflow-y: auto; padding: 20px 22px;
    background: var(--paper); border: 1px solid var(--line); border-radius: var(--radius);
  }
  mark.flag {
    background: var(--pending-soft); border-bottom: 2px solid var(--pending);
    color: inherit; padding: 1px 2px; border-radius: 3px; cursor: pointer;
  }
  mark.flag.confirmed {
    background: var(--confirmed-soft); border-bottom-color: var(--confirmed);
  }
  mark.flag.rejected { background: transparent; border-bottom: 1px dotted var(--line); }
  mark.flag.active { outline: 2px solid var(--accent); outline-offset: 1px; }

  .review-grid { display: grid; grid-template-columns: 1fr 330px; gap: 18px; }
  @media (max-width: 900px) { .review-grid { grid-template-columns: 1fr; } }
  .side {
    background: var(--panel); border: 1px solid var(--line);
    border-radius: var(--radius); padding: 16px 18px; align-self: start;
    position: sticky; top: 16px;
  }
  .entity { border-top: 1px solid var(--line); padding: 9px 0; font-size: 13px; }
  .entity:first-of-type { border-top: 0; }
  .token {
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 12px; background: var(--accent-soft); color: var(--accent);
    padding: 1px 6px; border-radius: 4px;
  }
  .counter { font-size: 26px; font-weight: 600; line-height: 1.1; }

  /* -- the dial -- */
  .dial { display: flex; gap: 0; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
  .dial button {
    flex: 1; border: 0; border-right: 1px solid var(--line); border-radius: 0;
    padding: 9px 6px; font-size: 12.5px; background: var(--panel); color: var(--muted);
  }
  .dial button:last-child { border-right: 0; }
  .dial button[aria-pressed="true"] { background: var(--accent); color: #fff; font-weight: 600; }
  @media (prefers-color-scheme: dark) { .dial button[aria-pressed="true"] { color: #10151b; } }

  .read-grid { display: grid; grid-template-columns: 360px 1fr; gap: 18px; align-items: start; }
  @media (max-width: 980px) { .read-grid { grid-template-columns: 1fr; } }
  .facts { position: sticky; top: 16px; max-height: 76vh; overflow-y: auto; }
  .fact { border-top: 1px solid var(--line); padding: 10px 0; font-size: 13.5px; }
  .fact:first-of-type { border-top: 0; }
  .fact .quote {
    color: var(--muted); font: 13px/1.6 ui-serif, Georgia, serif;
    border-left: 2px solid var(--line); padding-left: 10px; margin-top: 5px;
  }
  .tag {
    font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
    color: var(--muted); border: 1px solid var(--line); border-radius: 3px;
    padding: 0 5px; margin-left: 6px;
  }
  .reading {
    font: 16px/1.8 ui-serif, Georgia, "Times New Roman", serif;
    white-space: pre-wrap; min-height: 220px;
  }
  .role { border-top: 1px solid var(--line); padding: 12px 0; }
  .role:first-of-type { border-top: 0; }
  .role h4 { margin: 0 0 8px; font-size: 13px; }
  .duty-group { margin: 0 0 8px; }
  .duty-group .heading {
    font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted);
  }
  .duty-group.against .heading { color: var(--warn); }
  .duty { font-size: 13.5px; margin: 3px 0 0; }
  .duty .quote {
    color: var(--muted); font: 12.5px/1.55 ui-serif, Georgia, serif;
    border-left: 2px solid var(--line); padding-left: 9px; margin-top: 3px;
  }
  .disclaimer {
    margin-top: 26px; padding-top: 14px; border-top: 1px solid var(--line);
    color: var(--muted); font-size: 12.5px;
  }
  .scroll-box {
    max-height: 46vh; overflow-y: auto; white-space: pre-wrap;
    font: 14px/1.7 ui-serif, Georgia, serif;
    border: 1px solid var(--line); border-radius: var(--radius);
    padding: 18px 20px; background: var(--paper);
  }
  dialog {
    border: 1px solid var(--line); border-radius: var(--radius); padding: 24px 26px;
    max-width: 540px; background: var(--panel); color: var(--ink);
  }
  dialog::backdrop { background: rgba(0,0,0,.45); }
  .log-line {
    font: 12px/1.6 ui-monospace, Consolas, monospace; color: var(--muted);
    border-top: 1px solid var(--line); padding: 5px 0;
  }
</style>
</head>
<body>

<header>
  <h1>__TITLE__</h1>
  <span class="local">running on this computer only</span>
  <nav>
    <button class="ghost small" id="nav-settings">Settings</button>
    <button class="ghost small" id="nav-log">Your activity</button>
  </nav>
</header>

<main>
  <div id="banner"></div>

  <!-- consent ---------------------------------------------------------->
  <section class="card hidden" id="screen-consent">
    <h2>Before you start</h2>
    <p class="lede">Please read this. You will only be asked once, unless the wording changes.</p>
    <div class="scroll-box" id="consent-text"></div>
    <div class="actions" style="margin-top:16px">
      <button class="primary" id="consent-accept">I have read this and accept it</button>
    </div>
  </section>

  <!-- settings --------------------------------------------------------->
  <section class="card hidden" id="screen-settings">
    <h2>Settings</h2>
    <p class="lede">Your key stays on this computer and is used only to reach the model you choose.</p>
    <div class="row">
      <div class="field">
        <label for="provider">Model service</label>
        <select id="provider">
          <option value="anthropic">Anthropic</option>
          <option value="openai_compatible">OpenAI-compatible</option>
        </select>
      </div>
      <div class="field">
        <label for="model">Model</label>
        <input id="model" spellcheck="false">
      </div>
    </div>
    <div class="field hidden" id="base-url-field">
      <label for="base_url">Endpoint URL</label>
      <input id="base_url" spellcheck="false" placeholder="https://…/v1/chat/completions">
    </div>
    <div class="field">
      <label for="api_key">Your key <span id="key-source" class="muted"></span></label>
      <input id="api_key" type="password" autocomplete="off" placeholder="leave blank to keep the current one">
    </div>
    <div class="row">
      <div class="field">
        <label for="own_name">Your name <span class="muted">(optional)</span></label>
        <input id="own_name" spellcheck="false">
        <label style="margin-top:6px"><input type="checkbox" id="redact_own_name" style="width:auto"> also replace my name</label>
      </div>
      <div class="field">
        <label for="company_name">Organisation <span class="muted">(optional)</span></label>
        <input id="company_name" spellcheck="false">
        <label style="margin-top:6px"><input type="checkbox" id="redact_company_name" style="width:auto"> also replace the organisation name</label>
      </div>
    </div>
    <div class="actions">
      <button class="primary" id="settings-save">Save</button>
      <button id="settings-check">Check it works</button>
      <button class="ghost" id="settings-close">Close</button>
      <span id="check-result" class="small muted"></span>
    </div>
  </section>

  <!-- activity log ----------------------------------------------------->
  <section class="card hidden" id="screen-log">
    <h2>Your activity</h2>
    <p class="lede">Kept on this computer, for you. It records what you did, never what your documents say.</p>
    <div id="log-body" class="scroll-box"></div>
    <div class="actions"><button class="ghost" id="log-close">Close</button></div>
  </section>

  <!-- open ------------------------------------------------------------->
  <section class="card hidden" id="screen-open">
    <h2>Open a document</h2>
    <p class="lede">Your handbook, a letter, a policy, an email thread. It is read from where it already
      is and held in memory — no copy is made.</p>
    <div class="field">
      <label for="path">Full path to the file <span class="muted">(.pdf, .docx, .txt, .md)</span></label>
      <input id="path" spellcheck="false" placeholder="C:\Users\you\Documents\handbook.pdf">
    </div>
    <p class="small muted" style="margin:-6px 0 14px">or</p>
    <div class="field">
      <label for="paste">Paste the text</label>
      <textarea id="paste" spellcheck="false"></textarea>
    </div>
    <div class="actions"><button class="primary" id="open-go">Open</button></div>
  </section>

  <!-- review ----------------------------------------------------------->
  <section class="hidden" id="screen-review">
    <h2>Check what gets replaced</h2>
    <p class="lede">Names are replaced with role labels before any of this is sent. The detection below
      is a guess — click anything highlighted to confirm or reject it. Anything missed, select it in the
      text and add it. Nothing is sent until this is finished.</p>
    <div class="review-grid">
      <div>
        <div class="doc" id="doc-body"></div>
        <div class="actions">
          <button class="primary" id="review-seal" disabled>Replace names and read the document</button>
          <button class="ghost small" id="review-reject-all">Nothing here is a name</button>
          <button class="ghost small" id="review-restart">Open a different document</button>
        </div>
      </div>
      <aside class="side">
        <div class="counter" id="pending-count">0</div>
        <div class="small muted" style="margin-bottom:14px">still to check</div>
        <div id="reid-warnings"></div>
        <div class="small muted" style="margin-bottom:6px">People and labels</div>
        <div id="entity-list" class="small muted">None confirmed yet.</div>
        <div style="margin-top:16px">
          <label for="manual-surface">Add a name the check missed</label>
          <input id="manual-surface" placeholder="type or select it in the text">
          <select id="manual-role" style="margin-top:6px"></select>
          <button class="small" id="manual-add" style="margin-top:6px">Add</button>
        </div>
      </aside>
    </div>
  </section>

  <!-- read ------------------------------------------------------------->
  <section class="hidden" id="screen-read">
    <div style="margin-bottom:14px">
      <h2>What your document says</h2>
      <p class="lede" id="sealed-summary"></p>
    </div>
    <div class="read-grid">
      <aside class="card facts">
        <div class="small muted" style="margin-bottom:2px">The facts</div>
        <div class="small muted" style="margin-bottom:12px">These do not change when you move the dial.</div>
        <div id="facts-body"></div>
      </aside>
      <div>
        <section class="card" style="margin-bottom:14px">
          <label style="margin-bottom:8px">How this should be put to you</label>
          <div class="dial" id="dial"></div>
          <div class="small muted" style="margin-top:8px" id="dial-note"></div>
        </section>
        <section class="card">
          <div class="field">
            <label for="question">Ask something specific <span class="muted">(optional)</span></label>
            <input id="question" placeholder="e.g. how long do I have to appeal?">
          </div>
          <div class="actions">
            <button class="primary" id="read-go">Read it</button>
            <span id="read-status" class="small muted"></span>
          </div>
          <div class="reading" id="reading-body" style="margin-top:16px"></div>
          <div class="disclaimer" id="short-disclaimer"></div>
        </section>
        <section class="card" style="margin-top:14px">
          <div class="small muted" style="margin-bottom:2px">The roles around you</div>
          <div class="small muted" style="margin-bottom:12px">What each role is obliged to do — including the parts that work against you.</div>
          <div id="rolemap-body"><span class="muted small">Not mapped yet.</span></div>
          <div class="actions">
            <button id="rolemap-go">Map the roles</button>
            <span id="rolemap-status" class="small muted"></span>
          </div>
          <div class="disclaimer" id="rolemap-disclaimer"></div>
        </section>
        <div class="actions"><button class="ghost small" id="read-restart">Open a different document</button></div>
      </div>
    </div>
  </section>
</main>

<!-- the restate-and-confirm step -->
<dialog id="friction">
  <h2 id="friction-headline"></h2>
  <p id="friction-detail" class="lede" style="margin-bottom:12px"></p>
  <div class="notice warn hidden" id="friction-anchor"></div>
  <div class="actions">
    <button class="primary" id="friction-yes">I understand — use this setting</button>
    <button class="ghost" id="friction-no">Stay where I was</button>
  </div>
</dialog>

<script>
const TOKEN = "__TOKEN__";
let STATE = null;
let ACTIVE = null;          // detection index currently being decided
let PENDING_DIAL = null;    // dial position awaiting the friction confirm

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(action, body) {
  const res = await fetch("/api/" + action, {
    method: "POST",
    headers: { "content-type": "application/json", "x-session-token": TOKEN },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json();
  if (data.state) STATE = data.state;
  if (!data.ok) { banner(data.error, "error"); throw new Error(data.error); }
  banner("");
  return data;
}

async function refresh() {
  const res = await fetch("/api/state", { headers: { "x-session-token": TOKEN } });
  const data = await res.json();
  STATE = data.state;
  render();
}

function banner(message, kind) {
  $("banner").innerHTML = message
    ? `<div class="notice ${kind || ""}">${esc(message)}</div>` : "";
}

function show(id) {
  ["screen-consent", "screen-settings", "screen-log", "screen-open", "screen-review", "screen-read"]
    .forEach((s) => $(s).classList.toggle("hidden", s !== id));
}

/* ---------- rendering ---------- */

function render() {
  if (!STATE) return;
  $("short-disclaimer").textContent = STATE.short_disclaimer;

  if (STATE.placeholders.length) {
    banner("Note for whoever is testing this build: awaiting final wording for "
      + STATE.placeholders.join(", ") + ". The disclaimers you are shown are final; "
      + "what is outstanding is internal boundary text sent to the model. See DECISIONS.md item B1.", "warn");
  }

  if (!STATE.consent_granted) {
    $("consent-text").textContent = STATE.long_disclaimer;
    show("screen-consent");
    return;
  }
  if (!STATE.settings.key_present) { renderSettings(); show("screen-settings"); return; }
  if (!STATE.document) { show("screen-open"); return; }
  if (!STATE.sealed) { renderReview(); show("screen-review"); return; }
  renderRead();
  show("screen-read");
}

function renderSettings() {
  const s = STATE.settings;
  $("provider").value = s.provider;
  $("model").value = s.model;
  $("base_url").value = s.base_url;
  $("own_name").value = s.own_name;
  $("company_name").value = s.company_name;
  $("redact_own_name").checked = s.redact_own_name;
  $("redact_company_name").checked = s.redact_company_name;
  $("key-source").textContent = "— currently from: " + s.key_source;
  $("base-url-field").classList.toggle("hidden", s.provider !== "openai_compatible");
}

function renderReview() {
  const text = STATE.document.text;
  const flags = [...STATE.detections].sort((a, b) => a.start - b.start);
  let html = "", cursor = 0;
  for (const flag of flags) {
    if (flag.start < cursor) continue;
    html += esc(text.slice(cursor, flag.start));
    const decided = !flag.pending;
    const cls = flag.pending ? "flag" : (flag.entity_id ? "flag confirmed" : "flag rejected");
    const label = flag.entity_id ? ` title="${esc(flag.role || "")}"` : "";
    html += `<mark class="${cls}" data-i="${flag.index}"${label}>${esc(text.slice(flag.start, flag.end))}</mark>`;
    cursor = flag.end;
  }
  html += esc(text.slice(cursor));
  $("doc-body").innerHTML = html;
  $("doc-body").querySelectorAll("mark.flag").forEach((node) => {
    node.onclick = () => openDecision(Number(node.dataset.i));
  });

  $("pending-count").textContent = STATE.pending_count;
  $("review-seal").disabled = STATE.pending_count > 0;
  $("review-seal").textContent = STATE.pending_count > 0
    ? `${STATE.pending_count} still to check`
    : "Replace names and read the document";

  $("reid-warnings").innerHTML = STATE.reid_warnings.map(
    (w) => `<div class="notice warn small">${esc(w)}</div>`).join("");

  $("entity-list").innerHTML = STATE.entities.length
    ? STATE.entities.map((e) => `<div class="entity">
        <span class="token">${esc(e.token)}</span>
        <div class="muted" style="margin-top:3px">${esc(e.surfaces.join(", "))}</div>
      </div>`).join("")
    : `<span class="muted">None confirmed yet.</span>`;

  const roles = $("manual-role");
  if (!roles.options.length) {
    roles.innerHTML = STATE.roles.map((r) => `<option value="${r}">${r.replace(/_/g, " ").toLowerCase()}</option>`).join("");
  }
}

function openDecision(index) {
  ACTIVE = index;
  const flag = STATE.detections.find((d) => d.index === index);
  if (!flag) return;
  document.querySelectorAll("mark.flag").forEach((n) => n.classList.remove("active"));
  const node = document.querySelector(`mark.flag[data-i="${index}"]`);
  if (node) node.classList.add("active");

  const roleOptions = STATE.roles.map(
    (r) => `<option value="${r}">${r.replace(/_/g, " ").toLowerCase()}</option>`).join("");
  const side = document.querySelector(".side");
  const existing = $("decision-panel");
  if (existing) existing.remove();
  const panel = document.createElement("div");
  panel.id = "decision-panel";
  panel.innerHTML = `
    <div style="border-top:1px solid var(--line);margin-top:14px;padding-top:14px">
      <div style="font-weight:600">${esc(flag.surface)}</div>
      <div class="small muted" style="margin-bottom:10px">${esc(flag.reason)}</div>
      <label for="d-role">If this is a person, who are they to you?</label>
      <select id="d-role">${roleOptions}</select>
      <div class="small muted ${flag.suggested_role ? "" : "hidden"}" style="margin-top:4px">
        Pre-set to match the name this came from. Change it if this is someone else.
      </div>
      <label for="d-team" style="margin-top:8px">How many people hold that role? <span class="muted">(optional)</span></label>
      <input id="d-team" inputmode="numeric" placeholder="e.g. 4">
      <label style="margin-top:8px"><input type="checkbox" id="d-all" checked style="width:auto"> apply to every mention</label>
      <div class="actions">
        <button class="primary small" id="d-confirm">Replace it</button>
        <button class="small" id="d-reject">Not a name</button>
      </div>
    </div>`;
  side.appendChild(panel);
  if (flag.suggested_role) $("d-role").value = flag.suggested_role;
  $("d-confirm").onclick = async () => {
    await api("decide", {
      index, decision: "confirm", role: $("d-role").value,
      team_size: $("d-team").value, all_matching: $("d-all").checked,
    });
    panel.remove(); render();
  };
  $("d-reject").onclick = async () => {
    await api("decide", { index, decision: $("d-all").checked ? "reject_all" : "reject" });
    panel.remove(); render();
  };
}

function renderRoleMap() {
  const body = $("rolemap-body");
  const map = STATE.role_map;
  if (!map) { body.innerHTML = `<span class="muted small">Not mapped yet.</span>`; return; }
  if (!map.roles.length) {
    body.innerHTML = `<span class="muted small">No roles were established from this document.</span>`;
    return;
  }
  body.innerHTML = map.roles.map((role) => {
    const groups = Object.entries(map.direction_labels).map(([key, heading]) => {
      const duties = role.duties.filter((d) => d.direction === key);
      if (!duties.length) return "";
      return `<div class="duty-group ${key === "against_user" ? "against" : ""}">
        <div class="heading">${esc(heading)}</div>
        ${duties.map((d) => `<div class="duty">${esc(d.duty)}${
          d.certainty !== "stated" ? `<span class="tag">${esc(d.certainty)}</span>` : ""
        }<div class="quote">${esc(d.quote)}</div></div>`).join("")}
      </div>`;
    }).join("");
    const note = role.reidentifiable
      ? `<div class="notice warn small" style="margin-top:8px">${esc(role.reidentification_note)}</div>` : "";
    return `<div class="role"><h4><span class="token">${esc(role.token)}</span></h4>${groups}${note}</div>`;
  }).join("");
  $("rolemap-disclaimer").textContent = STATE.short_disclaimer;
}

function renderRead() {
  const sealed = STATE.sealed, f = STATE.facts;
  $("sealed-summary").textContent =
    `${sealed.entity_count} name${sealed.entity_count === 1 ? "" : "s"} replaced with `
    + `${sealed.tokens.join(", ") || "no labels"}. Only the replaced version was sent.`;

  $("facts-body").innerHTML = f
    ? (f.facts.map((x) => `<div class="fact">
        ${esc(x.statement)}${x.certainty !== "stated" ? `<span class="tag">${esc(x.certainty)}</span>` : ""}
        <div class="quote">${esc(x.quote)}</div>
      </div>`).join("")
      + (f.gaps.length
        ? `<div style="margin-top:16px" class="small muted">Not addressed by your document</div>`
          + f.gaps.map((g) => `<div class="fact muted">${esc(g)}</div>`).join("")
        : ""))
    : `<span class="muted">Not read yet.</span>`;

  const dial = $("dial");
  dial.innerHTML = STATE.dial.positions.map((p) =>
    `<button data-p="${p.value}" aria-pressed="${p.value === STATE.dial.current}">${esc(p.label)}</button>`
  ).join("");
  dial.querySelectorAll("button").forEach((b) => {
    b.onclick = () => moveDial(Number(b.dataset.p));
  });
  $("dial-note").textContent =
    "The facts on the left are the same at every setting. This changes how they are put to you, not what they are.";

  renderRoleMap();
}

$("rolemap-go").onclick = async () => {
  $("rolemap-go").disabled = true;
  $("rolemap-status").textContent = "Reading the roles from the replaced version…";
  try { await api("rolemap", {}); $("rolemap-status").textContent = ""; }
  catch (_) { $("rolemap-status").textContent = ""; }
  finally { $("rolemap-go").disabled = false; render(); }
};

/* ---------- the dial, with its one friction point ---------- */

async function moveDial(position) {
  const top = STATE.dial.positions[STATE.dial.positions.length - 1].value;
  if (position === top && STATE.dial.current !== top) {
    PENDING_DIAL = position;
    const info = await api("friction", {});
    $("friction-headline").textContent = info.headline;
    $("friction-detail").textContent = info.detail;
    const anchor = $("friction-anchor");
    anchor.classList.toggle("hidden", !info.anchor);
    anchor.textContent = info.anchor || "";
    $("friction").showModal();
    return;
  }
  await applyDial(position);
}

async function applyDial(position) {
  await api("settings", { ...STATE.settings, dial_position: position, api_key: undefined });
  await refresh();
}

$("friction-yes").onclick = async () => {
  $("friction").close();
  await api("friction", { accepted: true });
  if (PENDING_DIAL !== null) await applyDial(PENDING_DIAL);
  PENDING_DIAL = null;
};
$("friction-no").onclick = async () => {
  $("friction").close();
  await api("friction", { accepted: false });
  PENDING_DIAL = null;
};

/* ---------- reading (streamed) ---------- */

$("read-go").onclick = () => {
  const body = $("reading-body");
  body.textContent = "";
  $("read-status").textContent = "Sending the replaced version to the model you chose…";
  $("read-go").disabled = true;

  const params = new URLSearchParams({
    token: TOKEN,
    position: String(STATE.dial.current),
    question: $("question").value || "",
  });
  const source = new EventSource("/api/read?" + params.toString());
  source.addEventListener("delta", (e) => {
    body.textContent += JSON.parse(e.data).text;
    $("read-status").textContent = "Reading…";
  });
  source.addEventListener("done", (e) => {
    const info = JSON.parse(e.data);
    $("read-status").textContent = "Read at: " + info.label;
    $("read-go").disabled = false;
    source.close();
  });
  source.addEventListener("error", (e) => {
    let message = "The reading stopped unexpectedly.";
    try { message = JSON.parse(e.data).message; } catch (_) {}
    $("read-status").textContent = "";
    banner(message, "error");
    $("read-go").disabled = false;
    source.close();
  });
};

/* ---------- wiring ---------- */

$("consent-accept").onclick = async () => { await api("consent", {}); render(); };

$("nav-settings").onclick = () => { renderSettings(); show("screen-settings"); };
$("nav-log").onclick = async () => {
  const data = await api("log", {});
  $("log-body").innerHTML = data.entries.length
    ? data.entries.slice().reverse().map((e) =>
        `<div class="log-line">${esc(e.at)}  ${esc(e.action)}</div>`).join("")
    : `<span class="muted">Nothing recorded yet.</span>`;
  show("screen-log");
};
$("log-close").onclick = render;
$("settings-close").onclick = render;
$("provider").onchange = () =>
  $("base-url-field").classList.toggle("hidden", $("provider").value !== "openai_compatible");

$("settings-save").onclick = async () => {
  await api("settings", {
    provider: $("provider").value, model: $("model").value, base_url: $("base_url").value,
    own_name: $("own_name").value, company_name: $("company_name").value,
    redact_own_name: $("redact_own_name").checked,
    redact_company_name: $("redact_company_name").checked,
    dial_position: STATE.dial.current,
    api_key: $("api_key").value || undefined,
  });
  $("api_key").value = "";
  render();
};

$("settings-check").onclick = async () => {
  $("check-result").textContent = "Checking…";
  try {
    const data = await api("check", {});
    $("check-result").textContent = "Working — reached " + data.model;
  } catch (_) { $("check-result").textContent = ""; }
};

$("open-go").onclick = async () => {
  await api("open", { path: $("path").value, text: $("paste").value });
  $("paste").value = "";
  render();
};

$("review-reject-all").onclick = async () => { await api("reject_all_pending", {}); render(); };
$("review-restart").onclick = async () => { await api("open", { text: "" }).catch(() => {}); location.reload(); };
$("read-restart").onclick = () => location.reload();

$("manual-add").onclick = async () => {
  const surface = $("manual-surface").value || String(window.getSelection());
  if (!surface.trim()) { banner("Select the name in the document, or type it in.", "warn"); return; }
  await api("add", { surface: surface.trim(), role: $("manual-role").value });
  $("manual-surface").value = "";
  render();
};

document.addEventListener("selectionchange", () => {
  const chosen = String(window.getSelection()).trim();
  if (chosen && chosen.length < 80 && $("manual-surface")) $("manual-surface").value = chosen;
});

$("review-seal").onclick = async () => {
  $("review-seal").disabled = true;
  $("review-seal").textContent = "Replacing names, then reading the document…";
  try { await api("seal", {}); } finally { render(); }
};

refresh();
</script>
</body>
</html>
"""
