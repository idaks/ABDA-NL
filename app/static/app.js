/* ================================================================
   ABDA-NL frontend
   Fetches state bundles from the FastAPI backend and renders the
   Conclusions / Facts / Assumptions / Rules panels. Toggles on
   assumptions and defeasible rules append ops to a client-held
   diff_ops list and POST /state for re-computation.
   ================================================================ */

const state = {
  scenarios: [],        // [{id, title, description}, ...]
  scenario_id: null,    // currently-loaded scenario id
  baseline: null,       // scenario object at baseline (zero ops)
  bundle: null,         // current {scenario, af} from the server
  diff_ops: [],         // ops appended since baseline
  // UI state
  conclusionFilter: 'key',
  factsFilter: 'facts',
  kbTab: 'all',
  searchQuery: '',
  compactView: true,    // when true, Facts & Rules panels render as a flat
                        // list with per-card category badges instead of
                        // category headers breaking the list
  // Stable colour assignment for category badges (populated on bundle load)
  categoryColors: {},   // category-name -> palette hex
  // Rendering helpers populated on each bundle load
  descMap: {},          // id -> NL description (facts/assumptions/props/conclusions)
  negDescMap: {},       // id -> authored NL rendering of the negated literal (if any)
  ruleIds: new Set(),   // set of rule ids (for literal rendering)
  // Chat
  chatMessages: [],     // [{role: 'user'|'assistant', content: str}, ...]
  chatPending: false,   // true while a /chat request is in flight
  labelPulseIds: new Set(),  // proposition ids whose labels changed on last recompute
  labelPulseTimer: null,
};

// Max turns the server accepts in one request (per spec). We trim client
// history to this many user+assistant pairs before POSTing so the backend
// never has to reject a too-long message list.
const CHAT_TURN_CAP = 20;


/* ── API wrappers ─────────────────────────────────────── */

async function apiListScenarios() {
  const r = await fetch('/scenarios');
  if (!r.ok) throw new Error(`GET /scenarios: ${r.status}`);
  return (await r.json()).scenarios;
}

async function apiGetConfig() {
  const r = await fetch('/config');
  if (!r.ok) throw new Error(`GET /config: ${r.status}`);
  return await r.json();  // { llm_enabled: bool }
}

async function apiPostState(scenario_id, diff_ops, signal) {
  const r = await fetch('/state', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scenario_id, diff_ops }),
    signal,
  });
  const body = await r.json();
  if (!r.ok) {
    const err = new Error(body?.errors?.[0]?.message || `POST /state: ${r.status}`);
    err.errors = body.errors || [];
    err.status = r.status;
    throw err;
  }
  return body;  // { scenario, af }
}

async function apiSaveScenario(payload) {
  const r = await fetch('/scenarios', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await r.json();
  if (!r.ok) {
    const err = new Error(body?.errors?.[0]?.message || `POST /scenarios: ${r.status}`);
    err.errors = body.errors || [];
    err.status = r.status;
    err.code = body?.errors?.[0]?.code || null;
    throw err;
  }
  return body;  // { id, title, scenario, af }
}

// Track the current in-flight POST /state so newer requests can cancel
// older ones. This closes two races: (1) load-then-toggle where the
// toggle's response arrives after the load's and writes stale state;
// (2) rapid double-toggle where the slower response lands last. An
// aborted fetch throws AbortError which the callers swallow.
let currentRequest = null;
function beginRequest() {
  if (currentRequest) currentRequest.abort();
  const ctrl = new AbortController();
  currentRequest = ctrl;
  return ctrl;
}
function isCurrent(ctrl) {
  return ctrl === currentRequest;
}
function isAbortError(e) {
  return e && (e.name === 'AbortError' || e.code === 20);
}


/* ── Bootstrap ────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', async () => {
  initResize();
  try {
    // Fetch config first so LLM-only DOM is hidden before first paint of
    // scenario content — avoids a flash of chat/save/add buttons on
    // servers that run with ABDA_ENABLE_LLM unset (the default).
    const config = await apiGetConfig();
    document.body.classList.toggle('llm-disabled', !config.llm_enabled);
    state.scenarios = await apiListScenarios();
    populateScenarioSelect();
    // Pick a reasonable default: popov_v_hayashi if present, else the first.
    const defaultId = state.scenarios.some(s => s.id === 'popov_v_hayashi')
      ? 'popov_v_hayashi'
      : state.scenarios[0]?.id;
    if (defaultId) {
      await loadScenario(defaultId);
    }
  } catch (e) {
    showGlobalError(`Failed to initialize: ${e.message}`);
  }
});

function populateScenarioSelect() {
  const sel = document.getElementById('scenario-select');
  sel.innerHTML = '';
  for (const s of state.scenarios) {
    const opt = document.createElement('option');
    opt.value = s.id;
    opt.textContent = s.title;
    sel.appendChild(opt);
  }
  // Assign rather than addEventListener: save-flow calls
  // populateScenarioSelect() on every save to refresh the list, which
  // would stack a new handler each time. `onchange =` idempotently
  // replaces whatever was there.
  sel.onchange = e => loadScenario(e.target.value);
}


/* ── State transitions ────────────────────────────────── */

async function loadScenario(id) {
  const ctrl = beginRequest();
  state.scenario_id = id;
  state.diff_ops = [];
  // Reset UI-local state that is only meaningful for the prior scenario
  // (tab selection, search query). Without this a user coming from the
  // Modified tab lands on an empty view for a pristine scenario and
  // thinks the UI is broken.
  state.conclusionFilter = 'key';
  state.factsFilter = 'facts';
  state.kbTab = 'all';
  state.searchQuery = '';
  state.chatMessages = [];
  const searchInput = document.getElementById('kb-search-input');
  if (searchInput) searchInput.value = '';
  document.querySelectorAll('.concl-filter').forEach(b => b.classList.toggle('active', b.dataset.filter === 'key'));
  document.querySelectorAll('.facts-filter').forEach(b => b.classList.toggle('active', b.dataset.filter === 'facts'));
  document.querySelectorAll('.kb-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === 'all'));

  try {
    const bundle = await apiPostState(id, [], ctrl.signal);
    if (!isCurrent(ctrl)) return;  // superseded by a newer request
    state.baseline = bundle.scenario;
    setBundle(bundle);
    indexBundle();
    document.getElementById('scenario-select').value = id;
    renderAll();
  } catch (e) {
    if (isAbortError(e) || !isCurrent(ctrl)) return;
    showGlobalError(`Failed to load scenario ${id}: ${e.message}`);
  }
}

async function resetToBaseline() {
  if (!state.scenario_id) return;
  const ctrl = beginRequest();
  state.diff_ops = [];
  state.chatMessages = [];
  try {
    const bundle = await apiPostState(state.scenario_id, [], ctrl.signal);
    if (!isCurrent(ctrl)) return;
    setBundle(bundle, { pulseLabels: true });
    indexBundle();
    renderAll();
  } catch (e) {
    if (isAbortError(e) || !isCurrent(ctrl)) return;
    showGlobalError(`Reset failed: ${e.message}`);
  }
}

async function applyOp(op) {
  return applyOps([op]);
}

// Batch multiple ops into a single round-trip. Used by the Conflicts
// view where "A stronger" / "Same" / "B stronger" can emit 1-2 set-block
// ops at once; applying them as a batch avoids two intermediate renders.
async function applyOps(ops) {
  if (!ops || ops.length === 0) return;
  const ctrl = beginRequest();
  const prevOps = state.diff_ops.slice();
  state.diff_ops = [...state.diff_ops, ...ops];
  try {
    const bundle = await apiPostState(state.scenario_id, state.diff_ops, ctrl.signal);
    if (!isCurrent(ctrl)) return;
    setBundle(bundle, { pulseLabels: true });
    indexBundle();
    renderAll();
  } catch (e) {
    if (isAbortError(e) || !isCurrent(ctrl)) return;
    state.diff_ops = prevOps;
    showGlobalError(e.message);
    renderAll();
  }
}

// --- Suspend-impact preview ------------------------------------------------
// Intercepts the active-toggle checkboxes on assumptions and defeasible
// rules. Runs the op through /state speculatively, diffs the resulting
// labels_by_proposition against the current one, and shows a modal so the
// user can see which conclusions will change label before committing.
// Apply commits by promoting the previewed bundle to state (no second
// round-trip). Cancel re-renders so the checkbox snaps back to the
// pre-click state.

let pendingSuspendImpact = null;

async function previewAndConfirmToggle(op, meta) {
  const ctrl = beginRequest();
  const prospectiveOps = [...state.diff_ops, op];
  let projected;
  try {
    projected = await apiPostState(state.scenario_id, prospectiveOps, ctrl.signal);
  } catch (e) {
    if (isAbortError(e) || !isCurrent(ctrl)) return;
    renderFacts();
    renderKB();
    showGlobalError(`Preview failed: ${e.message}`);
    return;
  }
  if (!isCurrent(ctrl)) return;

  const before = state.bundle.af.labels_by_proposition || {};
  const after = projected.af.labels_by_proposition || {};
  const diffs = computeLabelDiffs(before, after, projected.scenario);

  pendingSuspendImpact = { prospectiveOps, projected };
  document.getElementById('suspend-impact-title').textContent = meta.title;
  document.getElementById('suspend-impact-summary').innerHTML = meta.summary;
  document.getElementById('suspend-impact-list').innerHTML = renderImpactDiffs(diffs);
  document.getElementById('modal-suspend-impact').classList.add('visible');
}

// Returns an array of { id, description, before, after } for every
// proposition whose label changed between the two label maps. Sorted
// with key conclusions first, then propositions, then facts/assumptions,
// each block alphabetical within itself -- matches what the user scans
// for first in the Conclusions panel.
function computeLabelDiffs(before, after, scenario) {
  const ids = new Set([...Object.keys(before), ...Object.keys(after)]);
  const concKeys = new Set(Object.keys(scenario.conclusions || {}));
  const propKeys = new Set(Object.keys(scenario.propositions || {}));
  const out = [];
  for (const id of ids) {
    const b = before[id] || 'absent';
    const a = after[id] || 'absent';
    if (b === a) continue;
    const entry =
      scenario.conclusions?.[id] ||
      scenario.propositions?.[id] ||
      scenario.facts?.[id] ||
      scenario.assumptions?.[id];
    const description = entry?.description || id;
    const tier = concKeys.has(id) ? 0 : propKeys.has(id) ? 1 : 2;
    out.push({ id, description, before: b, after: a, tier });
  }
  out.sort((x, y) => x.tier - y.tier || x.description.localeCompare(y.description));
  return out;
}

function renderImpactDiffs(diffs) {
  if (diffs.length === 0) {
    return `<div class="suspend-impact-empty">No conclusions change label under this edit.</div>`;
  }
  const badge = (label) => {
    const text = label.charAt(0).toUpperCase() + label.slice(1);
    return `<span class="badge badge-${label}" style="font-size:.6rem">${text}</span>`;
  };
  return diffs.map(d => `<div class="suspend-impact-item">
    <span class="suspend-impact-item-text">${escapeHtml(d.description)} <span class="inline-id">[${escapeHtml(d.id)}]</span></span>
    <span class="suspend-impact-transition">${badge(d.before)} → ${badge(d.after)}</span>
  </div>`).join('');
}

function applySuspendImpact() {
  if (!pendingSuspendImpact) return;
  const { prospectiveOps, projected } = pendingSuspendImpact;
  pendingSuspendImpact = null;
  state.diff_ops = prospectiveOps;
  setBundle(projected, { pulseLabels: true });
  indexBundle();
  renderAll();
  closeModal('modal-suspend-impact');
}

function setBundle(bundle, options = {}) {
  if (options.pulseLabels && state.bundle?.af) {
    state.labelPulseIds = computeChangedLabelIds(
      state.bundle.af.labels_by_proposition || {},
      bundle.af?.labels_by_proposition || {},
    );
  } else {
    state.labelPulseIds = new Set();
  }
  state.bundle = bundle;
}

function computeChangedLabelIds(before, after) {
  const ids = new Set([...Object.keys(before || {}), ...Object.keys(after || {})]);
  const changed = new Set();
  for (const id of ids) {
    if ((before || {})[id] !== (after || {})[id]) changed.add(id);
  }
  return changed;
}

function cancelSuspendImpact() {
  pendingSuspendImpact = null;
  closeModal('modal-suspend-impact');
  renderFacts();
  renderKB();
}

function indexBundle() {
  const scn = state.bundle.scenario;
  const map = {};
  const negMap = {};
  for (const section of ['facts', 'assumptions', 'propositions', 'conclusions']) {
    for (const [id, e] of Object.entries(scn[section] || {})) {
      map[id] = e.description;
      if (e.negated_description) negMap[id] = e.negated_description;
    }
  }
  for (const [id, r] of Object.entries(scn.rules || {})) {
    if (r.negated_description) negMap[id] = r.negated_description;
  }
  state.descMap = map;
  state.negDescMap = negMap;
  state.ruleIds = new Set(Object.keys(scn.rules || {}));
  rebuildCategoryColors(scn);
}


/* ── "modified vs baseline" detection ─────────────────── */

function isAssumptionModified(id) {
  const b = state.baseline.assumptions?.[id];
  const c = state.bundle.scenario.assumptions?.[id];
  if (!b && !c) return false;
  if (!b || !c) return true;  // added or removed via future ops
  return b.active !== c.active
    || b.description !== c.description
    || b.category !== c.category
    || b.source !== c.source
    || b.block !== c.block;
}

function isFactModified(id) {
  const b = state.baseline.facts?.[id];
  const c = state.bundle.scenario.facts?.[id];
  if (!b && !c) return false;
  if (!b || !c) return true;
  return b.description !== c.description
    || b.category !== c.category
    || b.source !== c.source;
}

function isRuleModified(id) {
  const b = state.baseline.rules?.[id];
  const c = state.bundle.scenario.rules?.[id];
  if (!b && !c) return false;
  if (!b || !c) return true;  // added or removed via future ops
  return b.active !== c.active
    || b.block !== c.block
    || b.conclusion !== c.conclusion
    || b.type !== c.type
    || JSON.stringify(b.premises) !== JSON.stringify(c.premises);
}


/* ── Rendering ────────────────────────────────────────── */

function renderAll() {
  renderScenarioName();
  renderModifiedIndicator();
  renderConclusions();
  renderFacts();
  renderKB();
  renderChat();
  // If the Explain modal is open, its derivation tree was built against
  // the pre-update state bundle; refresh it now so edits made through
  // the Conflicts view (or anywhere else) flow through immediately.
  const gameModal = document.getElementById('modal-game');
  if (gameModal && gameModal.classList.contains('visible') && gameConclusionId) {
    renderArgumentPicker();
  }
}

function renderScenarioName() {
  const scn = state.bundle?.scenario;
  document.getElementById('scenario-name').textContent = scn?.title || '';
}

function renderModifiedIndicator() {
  const indicator = document.getElementById('modified-indicator');
  if (!indicator) return;
  const count = state.diff_ops.length;
  if (count === 0) {
    indicator.hidden = true;
    indicator.textContent = '';
    return;
  }
  indicator.hidden = false;
  indicator.textContent = `Modified from baseline: ${count} ${count === 1 ? 'change' : 'changes'}`;
}

function renderConclusions() {
  const list = document.getElementById('conclusions-list');
  const scn = state.bundle.scenario;
  const labels = state.bundle.af.labels_by_proposition || {};

  //   key       -> all scenario.conclusions (Explain is individually
  //                disabled on conclusions without any argument for them)
  //   accepted/rejected/undecided/absent -> conclusions ∪ propositions filtered by label
  //   all       -> conclusions ∪ propositions
  let entries = [];
  if (state.conclusionFilter === 'key') {
    entries = Object.entries(scn.conclusions || {});
  } else {
    entries = [
      ...Object.entries(scn.conclusions || {}),
      ...Object.entries(scn.propositions || {}),
    ];
    if (state.conclusionFilter !== 'all') {
      entries = entries.filter(([id]) => labels[id] === state.conclusionFilter);
    }
  }

  if (entries.length === 0) {
    list.innerHTML = `<div class="placeholder-msg">No conclusions match the ${state.conclusionFilter} filter.</div>`;
    return;
  }

  list.innerHTML = entries.map(([id, entry]) => {
    const label = labels[id] || 'absent';
    const badge = label.charAt(0).toUpperCase() + label.slice(1);
    const explainable = getCandidateRootArguments(id).length > 0;
    const title = explainable
      ? ''
      : (label === 'absent'
          ? 'No argument derives this conclusion in the current state'
          : 'No derivation is available for this conclusion');
    const explain = explainable
      ? `<button class="btn-explain" data-explain-id="${escapeAttr(id)}">Explain</button>`
      : `<button class="btn-explain" disabled title="${escapeAttr(title)}">Explain</button>`;
    const changed = state.labelPulseIds.has(id) ? ' label-changed' : '';
    return `<div class="conclusion-card${changed}">
      <div class="conclusion-status-bar status-${label}">${badge}</div>
      <span class="conclusion-label">${escapeHtml(entry.description)}</span>
      <div class="conclusion-actions">
        ${explain}
        <span class="rule-info" data-desc="${escapeAttr(entry.description)}" title="Ask about this conclusion">?</span>
      </div>
    </div>`;
  }).join('');

  for (const btn of list.querySelectorAll('button[data-explain-id]')) {
    btn.addEventListener('click', () => openExplainModal(btn.dataset.explainId));
  }
  if (state.labelPulseIds.size > 0) {
    if (state.labelPulseTimer) window.clearTimeout(state.labelPulseTimer);
    state.labelPulseTimer = window.setTimeout(() => {
      state.labelPulseIds.clear();
      state.labelPulseTimer = null;
    }, 900);
  }
}

function switchConclusionFilter(f) {
  state.conclusionFilter = f;
  document.querySelectorAll('.concl-filter').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.filter === f);
  });
  renderConclusions();
}

function renderFacts() {
  const list = document.getElementById('facts-list');
  const scn = state.bundle.scenario;

  // Build list of [id, entry, kind] per current tab.
  let items;
  if (state.factsFilter === 'facts') {
    items = Object.entries(scn.facts || {}).map(([id, e]) => [id, e, 'fact']);
  } else if (state.factsFilter === 'assumptions') {
    items = Object.entries(scn.assumptions || {}).map(([id, e]) => [id, e, 'assumption']);
  } else if (state.factsFilter === 'modified') {
    items = [
      ...Object.entries(scn.facts || {})
        .filter(([id]) => isFactModified(id))
        .map(([id, e]) => [id, e, 'fact']),
      ...Object.entries(scn.assumptions || {})
        .filter(([id]) => isAssumptionModified(id))
        .map(([id, e]) => [id, e, 'assumption']),
    ];
  } else {  // 'suspended' -- only inactive assumptions (facts can't be suspended)
    items = Object.entries(scn.assumptions || {})
      .filter(([, e]) => e.active === false)
      .map(([id, e]) => [id, e, 'assumption']);
  }

  if (items.length === 0) {
    const emptyMsg = {
      modified: 'No facts or assumptions have been modified.',
      suspended: 'No suspended assumptions in this scenario.',
    }[state.factsFilter] || `No ${state.factsFilter} in this scenario.`;
    list.innerHTML = `<div class="placeholder-msg">${emptyMsg}</div>`;
    return;
  }

  // Group by category (always — sort order is stable even in compact mode).
  const groups = {};
  for (const item of items) {
    const cat = item[1].category || 'other';
    (groups[cat] ||= []).push(item);
  }
  const sortedCats = Object.keys(groups).sort();
  if (state.compactView) {
    list.innerHTML = sortedCats
      .flatMap(cat => groups[cat].map(([id, e, kind]) => renderFactLikeCard(id, e, kind)))
      .join('');
  } else {
    list.innerHTML = sortedCats.map(cat => {
      const cards = groups[cat]
        .map(([id, e, kind]) => renderFactLikeCard(id, e, kind))
        .join('');
      return `<div class="kb-group">
        <div class="kb-group-title">${categoryBadge(cat)}</div>
        ${cards}
      </div>`;
    }).join('');
  }

  for (const cb of list.querySelectorAll('input[data-asm-id]')) {
    cb.addEventListener('change', e => {
      const id = e.target.dataset.asmId;
      const asm = state.bundle.scenario.assumptions?.[id];
      const nowActive = e.target.checked;
      const action = nowActive ? 'Unsuspend' : 'Suspend';
      const desc = asm?.description || id;
      previewAndConfirmToggle(
        { op: 'toggle-assumption', id },
        {
          title: `${action} assumption?`,
          summary: `<strong>${action}:</strong> ${escapeHtml(desc)} <span class="inline-id">[${escapeHtml(id)}]</span>`,
        },
      );
    });
  }
}

// 12-tone palette for category badges. Each entry is {bg, text, border}:
// a light tint for the background, a dark variant for text, the saturated
// hue for the outline. Hand-picked to avoid the accepted/rejected/
// undecided hues.
const CATEGORY_PALETTE = [
  { bg: '#dcedee', text: '#2a6a70', border: '#4a9aa0' }, // teal
  { bg: '#e4dcea', text: '#4a2a5a', border: '#7a5a8a' }, // plum
  { bg: '#dcecd6', text: '#2a5a20', border: '#6a9a5a' }, // sage
  { bg: '#f2d8d8', text: '#7a3a3a', border: '#c07878' }, // coral
  { bg: '#dce2e8', text: '#3a4a5a', border: '#6a8090' }, // slate
  { bg: '#ecdfd2', text: '#5a3a18', border: '#a07a5a' }, // clay
  { bg: '#d6ece4', text: '#1a6a55', border: '#5a9a85' }, // seafoam
  { bg: '#edd9e4', text: '#5a2a4a', border: '#a06a8a' }, // mauve
  { bg: '#d2e2d6', text: '#1a4a2a', border: '#4a7a5a' }, // forest
  { bg: '#ecd8d2', text: '#5a2a1a', border: '#9a5a4a' }, // rust
  { bg: '#d6dde8', text: '#2a3a5a', border: '#5a7095' }, // steel
  { bg: '#e8e8d2', text: '#4a4a1a', border: '#8a8a4a' }, // olive
];

// Assign palette slots to the union of categories that appear across facts,
// assumptions, propositions, conclusions, and rules — sorted alphabetically so
// that the same category always gets the same colour in a given scenario.
function rebuildCategoryColors(scn) {
  const cats = new Set();
  const collect = (obj) => {
    for (const v of Object.values(obj || {})) {
      if (v && v.category) cats.add(v.category);
    }
  };
  collect(scn.facts);
  collect(scn.assumptions);
  collect(scn.propositions);
  collect(scn.conclusions);
  collect(scn.rules);
  const sorted = [...cats].sort();
  const map = {};
  for (let i = 0; i < sorted.length; i++) {
    map[sorted[i]] = CATEGORY_PALETTE[i % CATEGORY_PALETTE.length];
  }
  state.categoryColors = map;
}

function categoryBadge(cat) {
  if (!cat) return '';
  const c = state.categoryColors[cat] || { bg: '#e8e8ec', text: '#5a5a6a', border: '#b0b0c0' };
  return `<span class="kb-badge" style="background:${c.bg};color:${c.text}">${escapeHtml(cat)}</span>`;
}

function toggleCompactView() {
  const cb = document.getElementById('compact-toggle');
  state.compactView = cb ? cb.checked : !state.compactView;
  renderFacts();
  renderKB();
}

function renderFactLikeCard(id, entry, kind) {
  const info = entry.source ? escapeAttr(entry.source) : 'Ask about this ' + kind;
  const desc = escapeAttr(entry.description);
  const inlineId = `<span class="inline-id">[${escapeHtml(id)}]</span>`;
  const text = `<span class="fact-text">${escapeHtml(entry.description)} ${inlineId}</span>`;
  const badge = state.compactView ? categoryBadge(entry.category) : '';
  if (kind === 'assumption') {
    const active = entry.active !== false;
    const divergent = isAssumptionModified(id);
    const cls = 'fact-card'
      + (divergent ? ' kb-divergent' : '')
      + (!active ? ' suspended' : '');
    return `<div class="${cls}">
      ${text}
      ${badge}
      <span class="rule-info" data-desc="${desc}" title="${info}">?</span>
      <input type="checkbox" ${active ? 'checked' : ''} data-asm-id="${escapeAttr(id)}" title="Active -- uncheck to deactivate this assumption">
    </div>`;
  }
  const divergent = isFactModified(id);
  const cls = 'fact-card' + (divergent ? ' kb-divergent' : '');
  return `<div class="${cls}">
    ${text}
    ${badge}
    <span class="rule-info" data-desc="${desc}" title="${info}">?</span>
  </div>`;
}

function switchFactsFilter(f) {
  state.factsFilter = f;
  document.querySelectorAll('.facts-filter').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.filter === f);
  });
  renderFacts();
}

function renderKB() {
  const root = document.getElementById('kb-content');
  const scn = state.bundle.scenario;

  // Conflicts tab takes a distinct code path: we show preference-card
  // pairs rather than the usual rule list.
  if (state.kbTab === 'conflicts') {
    renderConflicts(root);
    return;
  }

  let rules = Object.entries(scn.rules || {});

  if (state.kbTab === 'modified') {
    rules = rules.filter(([id]) => isRuleModified(id));
  } else if (state.kbTab === 'suspended') {
    rules = rules.filter(([, r]) => r.active === false);
  }
  if (state.searchQuery) {
    const q = state.searchQuery.toLowerCase();
    rules = rules.filter(([id, r]) => {
      if (id.toLowerCase().includes(q)) return true;
      const concDesc = (state.descMap[r.conclusion.replace(/^-/, '')] || '').toLowerCase();
      if (concDesc.includes(q)) return true;
      for (const p of r.premises || []) {
        const pd = (state.descMap[p.replace(/^-/, '')] || '').toLowerCase();
        if (pd.includes(q)) return true;
      }
      return false;
    });
  }

  if (rules.length === 0) {
    root.innerHTML = `<div class="placeholder-msg">No rules match.</div>`;
    return;
  }

  // Group by category (always — sort order is stable even in compact mode).
  const groups = {};
  for (const [id, rule] of rules) {
    const cat = rule.category || 'other';
    (groups[cat] ||= []).push([id, rule]);
  }
  const sortedCats = Object.keys(groups).sort();
  if (state.compactView) {
    root.innerHTML = sortedCats
      .flatMap(cat => groups[cat].map(([id, r]) => renderRuleCard(id, r)))
      .join('');
  } else {
    root.innerHTML = sortedCats.map(cat => {
      const cards = groups[cat].map(([id, r]) => renderRuleCard(id, r)).join('');
      return `<div class="kb-group">
        <div class="kb-group-title">${categoryBadge(cat)}</div>
        ${cards}
      </div>`;
    }).join('');
  }

  for (const cb of root.querySelectorAll('input.rule-active-toggle')) {
    cb.addEventListener('change', e => {
      const id = e.target.dataset.ruleId;
      const nowActive = e.target.checked;
      const action = nowActive ? 'Unsuspend' : 'Suspend';
      previewAndConfirmToggle(
        { op: 'toggle-rule', id },
        {
          title: `${action} rule?`,
          summary: `<strong>${action}:</strong> rule <span class="inline-id">[${escapeHtml(id)}]</span>`,
        },
      );
    });
  }
  for (const btn of root.querySelectorAll('button.btn-rule-modify')) {
    btn.addEventListener('click', e => {
      openEditModal('modify-rule', e.currentTarget.dataset.editRuleId);
    });
  }
}

// ----------------------------------------------------------------------
// Conflicts view: rebut pairs between defeasible rules and assumptions,
// with a three-way preference radio wired to set-block ops.
// Strict rules and facts are excluded (their blocks can't be reordered).
// Undercuts are NOT shown here because the ABDA engine ignores
// preferences on the undercut test (see ArgumentBuilder.does_attacks):
// the attack always fires as long as the undercut argument exists, so
// preference controls on undercut pairs would be cosmetic.
// ----------------------------------------------------------------------

function detectConflicts() {
  const scn = state.bundle.scenario;
  const rules = scn.rules || {};
  const assumptions = scn.assumptions || {};

  // Collect defeasible rule-like entities (defeasible rules + active
  // assumptions; active assumptions are bodyless defeasible rules
  // concluding their own id).
  const defRuleIds = new Set(
    Object.entries(rules).filter(([, r]) => r.type === 'defeasible').map(([id]) => id),
  );
  const ents = new Map();
  for (const [id, r] of Object.entries(rules)) {
    if (r.type !== 'defeasible') continue;
    ents.set(id, { id, target: 'rule', block: r.block || 1, conclusion: r.conclusion, rule: r, assumption: null });
  }
  for (const [id, a] of Object.entries(assumptions)) {
    ents.set(id, { id, target: 'assumption', block: a.block || 1, conclusion: id, rule: null, assumption: a });
  }

  const rebuts = [];
  const seenRebut = new Set();

  for (const [aid, A] of ents) {
    // Skip undercut-literal-producing entities entirely; their attack on
    // the target rule isn't preference-sensitive and they have no
    // propositional rebut pair to show.
    if (A.conclusion.startsWith('-') && defRuleIds.has(A.conclusion.slice(1))) continue;

    const negConc = A.conclusion.startsWith('-') ? A.conclusion.slice(1) : '-' + A.conclusion;
    const negBase = negConc.startsWith('-') ? negConc.slice(1) : negConc;
    if (defRuleIds.has(negBase)) continue;
    for (const [bid, B] of ents) {
      if (bid === aid) continue;
      if (B.conclusion !== negConc) continue;
      const key = [aid, bid].sort().join('|');
      if (seenRebut.has(key)) continue;
      seenRebut.add(key);
      rebuts.push({ type: 'rebut', a: A, b: B });
    }
  }
  return { rebuts };
}

function renderConflicts(root) {
  const { rebuts } = detectConflicts();
  if (rebuts.length === 0) {
    root.innerHTML = `<div class="placeholder-msg">No rebut conflicts in the current state. Every defeasible rule and assumption sits alone.</div>`;
    return;
  }

  let html = `<div class="kb-group">
    <div class="kb-group-title">Rebuts (${rebuts.length})</div>`;
  for (const c of rebuts) html += renderConflictCard(c, 'rebut');
  html += `</div>`;
  root.innerHTML = html;

  for (const input of root.querySelectorAll('input[data-conflict-op]')) {
    input.addEventListener('change', e => {
      const choice = e.target.value;
      const A = {
        id: e.target.dataset.aId,
        target: e.target.dataset.aTarget,
      };
      const B = {
        id: e.target.dataset.bId,
        target: e.target.dataset.bTarget,
      };
      applyPreferenceChoice(A, B, choice);
    });
  }
}

function renderConflictCard(conflict, kind) {
  const { a, b } = conflict;
  const ba = a.block, bb = b.block;
  const state = ba === bb ? 'same' : (ba > bb ? 'a' : 'b');
  const cardId = `conf-${kind}-${a.id}-${b.id}`;
  const arrow = '↔ rebuts';
  // Only rebuts are surfaced in the Conflicts view (see detectConflicts
  // header): undercuts aren't preference-sensitive in ABDA's engine, so
  // the radios would be cosmetic there.
  const aSide = renderConflictSide(a);
  const bSide = renderConflictSide(b);
  const dataAttrs = `data-a-id="${escapeAttr(a.id)}" data-a-target="${escapeAttr(a.target)}" data-b-id="${escapeAttr(b.id)}" data-b-target="${escapeAttr(b.target)}" data-conflict-op="${kind}"`;
  const radio = (value, label, checked) => `
    <label class="pref-option">
      <input type="radio" name="${cardId}" value="${value}" ${checked ? 'checked' : ''} ${dataAttrs}/>
      <span>${label}</span>
    </label>`;
  const aTag = `<span class="inline-id">[${escapeHtml(a.id)}]</span>`;
  const bTag = `<span class="inline-id">[${escapeHtml(b.id)}]</span>`;
  return `<div class="pref-conflict-card">
    <div class="pref-conflict-sides">
      <div class="pref-side pref-side-a ${state==='a' ? 'pref-side-stronger' : ''}">${aSide}</div>
      <div class="pref-vs">${arrow}</div>
      <div class="pref-side pref-side-b ${state==='b' ? 'pref-side-stronger' : ''}">${bSide}</div>
    </div>
    <div class="pref-control">
      ${radio('a',    `${aTag} stronger`,      state === 'a')}
      ${radio('same', 'Same strength',          state === 'same')}
      ${radio('b',    `${bTag} stronger`,      state === 'b')}
    </div>
  </div>`;
}

// Render one side of a conflict card: the rule's formal statement for
// rules, or the assumption's natural-language description for
// assumptions. Both get the id shown inline for traceability.
function renderConflictSide(ent) {
  if (ent.target === 'rule' && ent.rule) {
    return `<div class="pref-rule">${renderRuleText(ent.id, ent.rule)}</div>`;
  }
  if (ent.target === 'assumption' && ent.assumption) {
    return `<div class="pref-rule">${escapeHtml(ent.assumption.description || ent.id)} <span class="inline-id">[${escapeHtml(ent.id)}]</span></div>`;
  }
  return `<div class="pref-rule"><span class="inline-id">[${escapeHtml(ent.id)}]</span></div>`;
}

function applyPreferenceChoice(A, B, choice) {
  // Baseline-aware: target canonical blocks so the three radios always
  // land at well-defined states. If the user's choice matches what
  // baseline had, restore the baseline blocks exactly — so round-trips
  // via the Conflicts view leave the scenario bit-for-bit identical to
  // where it started (important for the Explain button's enabled state,
  // which depends on whether the supporting argument has attackers —
  // and that in turn depends on whether blocks are strictly ordered or
  // equal, not just on relative magnitudes).
  const scn = state.bundle.scenario;
  const baseline = state.baseline;
  const blockIn = (src, ent) => {
    const r = ent.target === 'rule' ? src.rules?.[ent.id] : src.assumptions?.[ent.id];
    return (r && r.block) || 1;
  };
  const bla = blockIn(baseline, A);
  const blb = blockIn(baseline, B);
  const baselineChoice = bla === blb ? 'same' : (bla > blb ? 'a' : 'b');
  const ops = [];
  const pushSet = (ent, newBlock) => {
    const cur = blockIn(scn, ent);
    if (cur !== newBlock) ops.push({ op: 'set-block', target: ent.target, id: ent.id, block: newBlock });
  };
  if (choice === baselineChoice) {
    // Revert to baseline blocks exactly.
    pushSet(A, bla);
    pushSet(B, blb);
  } else {
    // Target canonical blocks pivoted around the baseline maximum so
    // repeated clicks produce consistent states.
    const base = Math.max(bla, blb);
    if (choice === 'a') {
      pushSet(A, base + 1);
      pushSet(B, base);
    } else if (choice === 'b') {
      pushSet(A, base);
      pushSet(B, base + 1);
    } else { // 'same'
      pushSet(A, base);
      pushSet(B, base);
    }
  }
  if (ops.length > 0) applyOps(ops);
}

// ----------------------------------------------------------------------

function renderRuleCard(id, rule) {
  const divergent = isRuleModified(id);
  const inactive = rule.active === false;
  const cls = 'rule-card'
    + (divergent ? ' kb-divergent' : '')
    + (inactive ? ' suspended' : '');

  const premiseLits = (rule.premises || []).map(p => renderLiteral(p));
  const conclusionLit = renderLiteral(rule.conclusion);
  const premises = premiseLits.map(escapeHtml).join(' <span class="kw">and</span> ');
  const conclusion = escapeHtml(conclusionLit);
  const connective = rule.type === 'strict' ? 'necessarily' : 'normally';
  const body = premises
    ? `<span class="kw">If</span> ${premises} <span class="kw">then</span> <span class="kw">${connective}</span> ${conclusion}`
    : `<span class="kw">${connective}</span> ${conclusion}`;
  const plainBody = premiseLits.length
    ? `If ${premiseLits.join(' and ')} then ${connective} ${conclusionLit}`
    : `${connective} ${conclusionLit}`;

  const checkbox = rule.type === 'defeasible'
    ? `<input type="checkbox" class="rule-active-toggle" data-rule-id="${escapeAttr(id)}" ${inactive ? '' : 'checked'} title="Active -- uncheck to deactivate this rule">`
    : '';
  const info = rule.source ? escapeAttr(rule.source) : 'Ask about this rule';
  const idInline = `<span class="inline-id">[${escapeHtml(id)}]</span>`;

  const badge = state.compactView ? categoryBadge(rule.category) : '';
  const editBtn = `<button class="btn btn-small btn-rule-modify llm-only" data-edit-rule-id="${escapeAttr(id)}" title="Modify this rule via natural-language instruction">Modify</button>`;
  return `<div class="${cls}">
    <div class="rule-body">
      <div class="rule-text">${body} ${idInline}</div>
    </div>
    <div class="rule-actions">
      ${badge}
      <span class="rule-info" data-desc="${escapeAttr(plainBody)}" title="${info}">?</span>
      ${editBtn}
      ${checkbox}
    </div>
  </div>`;
}

function renderLiteral(lit) {
  const negated = lit.startsWith('-');
  const base = negated ? lit.slice(1) : lit;
  if (negated && state.negDescMap[base]) return state.negDescMap[base];
  if (state.ruleIds.has(base)) {
    return negated ? `rule ${base} does not apply` : `rule ${base} applies`;
  }
  const desc = state.descMap[base] || base;
  return negated ? `it is not the case that ${desc}` : desc;
}

function switchKBTab(tab) {
  state.kbTab = tab;
  document.querySelectorAll('.kb-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  renderKB();
}

function filterKB(q) {
  state.searchQuery = q;
  renderKB();
}


/* ── Chat ─────────────────────────────────────────────── */

async function apiPostChat(scenario_id, diff_ops, messages, signal) {
  const r = await fetch('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scenario_id, diff_ops, messages }),
    signal,
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = body?.detail || body?.errors?.[0]?.message || `POST /chat: ${r.status}`;
    const err = new Error(msg);
    err.status = r.status;
    throw err;
  }
  return body;
}

function renderChat() {
  const container = document.getElementById('chat-messages');
  if (!container) return;
  if (state.chatMessages.length === 0 && !state.chatPending) {
    container.innerHTML = `<div class="chat-empty">Ask a question about this scenario. Try clicking a <span class="rule-info-demo">?</span> next to any item to start.</div>`;
    return;
  }
  const parts = state.chatMessages.map(m => {
    if (m.role === 'user') {
      return `<div class="chat-msg chat-msg-user"><div class="chat-bubble">${escapeHtml(m.content)}</div></div>`;
    }
    // Assistant: render markdown, then sanitize. If either library is missing
    // (CDN unreachable), fall back to escaped plain text with line-breaks.
    let html;
    if (typeof window.marked !== 'undefined' && typeof window.DOMPurify !== 'undefined') {
      html = window.DOMPurify.sanitize(
        window.marked.parse(m.content, { breaks: true, gfm: true })
      );
    } else {
      html = escapeHtml(m.content).replace(/\n/g, '<br>');
    }
    return `<div class="chat-msg chat-msg-assistant"><div class="chat-bubble">${html}</div></div>`;
  });
  if (state.chatPending) {
    parts.push(
      `<div class="chat-msg chat-msg-assistant chat-msg-loading">` +
      `<div class="chat-bubble"><span class="chat-dot"></span><span class="chat-dot"></span><span class="chat-dot"></span></div></div>`
    );
  }
  container.innerHTML = parts.join('');
  container.scrollTop = container.scrollHeight;
}

async function sendChatMessage(prefilledText) {
  if (state.chatPending) return;
  const input = document.getElementById('chat-input');
  let text;
  if (typeof prefilledText === 'string') {
    text = prefilledText.trim();
  } else {
    text = (input?.value || '').trim();
  }
  if (!text) return;
  if (input) input.value = '';

  state.chatMessages.push({ role: 'user', content: text });
  state.chatPending = true;
  renderChat();

  // Trim history to the last CHAT_TURN_CAP messages before POSTing. The
  // backend enforces its own cap; this just keeps the wire small.
  const messages = state.chatMessages.slice(-CHAT_TURN_CAP);

  try {
    const resp = await apiPostChat(state.scenario_id, state.diff_ops, messages);
    state.chatMessages.push({ role: 'assistant', content: resp.message });
  } catch (e) {
    state.chatMessages.push({
      role: 'assistant',
      content: `_Chat error: ${e.message}_`,
    });
  } finally {
    state.chatPending = false;
    renderChat();
  }
}

// Delegated click handler: any `.rule-info[data-desc]` anywhere in the left
// panel pre-fills a chat question using the item's NL description and
// auto-submits. Silent no-op when LLM mode is disabled (body.llm-disabled
// already hides the right panel, but the `?` spans remain clickable if a
// user somehow reaches them — a no-op is safer than a failed POST).
document.addEventListener('click', (e) => {
  const target = e.target.closest('.rule-info');
  if (!target) return;
  if (document.body.classList.contains('llm-disabled')) return;
  const desc = target.dataset.desc;
  if (!desc) return;
  sendChatMessage(`Can you explain "${desc}"?`);
});


/* ── Error surface ────────────────────────────────────── */

function showGlobalError(msg) {
  // Minimal: prepend a banner at the top of the left panel.
  const left = document.getElementById('left-panel');
  let banner = document.getElementById('error-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'error-banner';
    banner.className = 'error-banner';
    banner.addEventListener('click', () => banner.remove());
    left.prepend(banner);
  }
  banner.textContent = `${msg} (click to dismiss)`;
}


/* ── Utility ──────────────────────────────────────────── */

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeAttr(s) {
  // Attributes only need quote escaping; keep & intact for entity round-tripping.
  return String(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}


/* ── Resize handles ──────────────────────────────────────── */

function initResize() {
  setupColResize('resize-handle', 'left-panel', 30, 85);
  setupColResizeInner('v-resize-top', 'conclusions-panel', 'top-section', 25, 75);
  setupRowResize('h-resize-left', 'top-section', 'left-panel', 15, 75);
  setupRowResizeFromBottom('h-resize-chat', 'chat-input-area', 'right-panel', 10, 50);
}

function setupColResize(handleId, panelId, minPct, maxPct) {
  const handle = document.getElementById(handleId);
  const panel = document.getElementById(panelId);
  if (!handle || !panel) return;
  let dragging = false;
  handle.addEventListener('mousedown', e => {
    dragging = true;
    handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const container = panel.parentElement;
    const rect = container.getBoundingClientRect();
    const pct = ((e.clientX - rect.left) / rect.width) * 100;
    if (pct >= minPct && pct <= maxPct) panel.style.width = pct + '%';
  });
  document.addEventListener('mouseup', () => {
    if (dragging) {
      dragging = false;
      handle.classList.remove('dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    }
  });
}

function setupColResizeInner(handleId, panelId, containerId, minPct, maxPct) {
  const handle = document.getElementById(handleId);
  const panel = document.getElementById(panelId);
  const container = document.getElementById(containerId);
  if (!handle || !panel || !container) return;
  let dragging = false;
  handle.addEventListener('mousedown', e => {
    dragging = true;
    handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const rect = container.getBoundingClientRect();
    const pct = ((e.clientX - rect.left) / rect.width) * 100;
    if (pct >= minPct && pct <= maxPct) {
      panel.style.flex = 'none';
      panel.style.width = pct + '%';
    }
  });
  document.addEventListener('mouseup', () => {
    if (dragging) {
      dragging = false;
      handle.classList.remove('dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    }
  });
}

function setupRowResize(handleId, panelId, containerId, minPct, maxPct) {
  const handle = document.getElementById(handleId);
  const panel = document.getElementById(panelId);
  const container = document.getElementById(containerId);
  if (!handle || !panel || !container) return;
  let dragging = false;
  handle.addEventListener('mousedown', e => {
    dragging = true;
    handle.classList.add('dragging');
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const rect = container.getBoundingClientRect();
    const pct = ((e.clientY - rect.top) / rect.height) * 100;
    if (pct >= minPct && pct <= maxPct) {
      panel.style.height = pct + '%';
      panel.style.flexShrink = '0';
    }
  });
  document.addEventListener('mouseup', () => {
    if (dragging) {
      dragging = false;
      handle.classList.remove('dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    }
  });
}

function setupRowResizeFromBottom(handleId, panelId, containerId, minPct, maxPct) {
  const handle = document.getElementById(handleId);
  const panel = document.getElementById(panelId);
  const container = document.getElementById(containerId);
  if (!handle || !panel || !container) return;
  let dragging = false;
  handle.addEventListener('mousedown', e => {
    dragging = true;
    handle.classList.add('dragging');
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const rect = container.getBoundingClientRect();
    const pct = ((rect.bottom - e.clientY) / rect.height) * 100;
    if (pct >= minPct && pct <= maxPct) {
      panel.style.flex = 'none';
      panel.style.height = pct + '%';
    }
  });
  document.addEventListener('mouseup', () => {
    if (dragging) {
      dragging = false;
      handle.classList.remove('dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    }
  });
}


/* ================================================================
   View AF — layered graph of the abstract argumentation framework.
   Groups AF arguments by canonical key (top_rule, conclusion) so the
   diagram shows one node per logical argument rather than per Cartesian
   variant. Nodes laid out in horizontal layers by min-max defense depth
   (min-max numbering / Caminada 2015 §3.2), with "inf" (undecided
   and cycle-stuck arguments) rendered as a separate column.

   Colour palette: accepted → blue, rejected → orange, undecided → yellow.
   Edge style: solid for rebut, dashed for undercut. Arrows point from
   attacker to target.
   ================================================================ */

// Scope for the View-Graph modal. 'key' (default) restricts to the
// dialectical neighbourhood of scenario.conclusions; 'all' shows every
// argument conclusion in the AF.
let afScope = 'key';

function openAFModal() {
  renderAFView();
  document.getElementById('modal-af').classList.add('visible');
  // Fit the graph to the available viewport once the modal is painted.
  // computeAFFit() needs non-zero clientWidth/Height on the scroll
  // container, which is only true after the modal becomes visible and
  // the browser has laid it out.
  requestAnimationFrame(() => {
    afZoom = computeAFFit();
    applyAFZoom();
  });
}

function renderAFView() {
  const body = document.getElementById('af-modal-body');
  const af = state.bundle.af;
  const scn = state.bundle.scenario;
  let args = af.arguments || [];
  let attacks = af.attacks || [];
  if (args.length === 0) {
    body.innerHTML = `<div class="placeholder-msg">No arguments in the current state.</div>`;
    return;
  }

  // --- Scope filter ---------------------------------------------------------
  // 'key': restrict to the dialectical neighbourhood of the scenario's
  //         key conclusions (every argument concluding a key or its
  //         negation, plus transitive-closure attackers). Removes
  //         fact/assumption-level supports that don't engage in attacks.
  // 'all': include every argument in the AF; only the later isolated-
  //         node filter prunes nodes with no edges.
  if (afScope === 'key') {
    const keyIds = new Set(Object.keys(scn.conclusions || {}));
    const seedIds = new Set(
      args
        .filter(a => {
          const base = a.conclusion.startsWith('-') ? a.conclusion.slice(1) : a.conclusion;
          return keyIds.has(base);
        })
        .map(a => a.id),
    );
    if (seedIds.size === 0) {
      body.innerHTML = `<div class="placeholder-msg">No arguments for any key conclusion in this state.</div>`;
      return;
    }
    const relevant = new Set();
    const frontier = [...seedIds];
    while (frontier.length > 0) {
      const id = frontier.pop();
      if (relevant.has(id)) continue;
      relevant.add(id);
      for (const e of attacks) {
        if (e.to === id && !relevant.has(e.from)) frontier.push(e.from);
      }
    }
    args = args.filter(a => relevant.has(a.id));
    attacks = attacks.filter(e => relevant.has(e.from) && relevant.has(e.to));
  }

  // --- Conclusion-level grouping -------------------------------------------
  // One node per distinct conclusion literal (including rule-undercut
  // literals like -r4). Arguments that share a conclusion -- e.g. r1
  // and rh both conclude hayashi_no_return in Popov -- collapse into
  // a single node. Label aggregates with in > undec > out across all
  // contributing arguments; min_max is the minimum of finite values,
  // else "inf". Carries the full list of contributing rules so the
  // tooltip can surface them.
  const groups = new Map();
  for (const a of args) {
    const key = a.conclusion;
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        conclusion: a.conclusion,
        conclusion_nl: a.conclusion_nl,
        rules: [],
        args: [],
      });
    }
    const g = groups.get(key);
    if (!g.rules.includes(a.top_rule)) g.rules.push(a.top_rule);
    g.args.push(a);
  }
  for (const g of groups.values()) {
    const labels = new Set(g.args.map(a => a.label));
    g.label = labels.has('in') ? 'in' : (labels.has('undec') ? 'undec' : 'out');
    const finites = g.args.map(a => a.min_max).filter(v => typeof v === 'number');
    g.min_max = finites.length > 0 ? Math.min(...finites) : 'inf';
  }

  // --- Conclusion-level edges ----------------------------------------------
  // Project an argument-level attack A → B down to Conc(A) → Conc(B) only
  // when the attacker's argument-level label matches the source conclusion
  // node's (aggregated) label AND the target argument's label matches the
  // target node's label. Without this guard the quotient can surface
  // edges that contradict grounded semantics -- e.g. if X has args X1(in)
  // and X2(out), and Y has args Y1(out) and Y2(undec), then Def 11 sets
  // X=in and Y=undec; an argument-level edge X2→Y1 (out→out, fine) gets
  // projected as X→Y, which reads on screen as in→undec and is never a
  // valid grounded configuration. Dropping the projection in that case
  // loses a structural edge but keeps the diagram faithful to the labels
  // the user sees in the Conclusions panel.
  //
  // If both rebut and undercut edges between the same conclusion pair
  // survive the filter, prefer 'rebut' as the semantically direct one.
  const edgeMap = new Map();
  const argConcl = new Map();
  const argLabelById = new Map();
  for (const a of args) {
    argConcl.set(a.id, a.conclusion);
    argLabelById.set(a.id, a.label);
  }
  for (const e of attacks) {
    const sk = argConcl.get(e.from);
    const dk = argConcl.get(e.to);
    if (!sk || !dk) continue;
    const sg = groups.get(sk);
    const dg = groups.get(dk);
    if (!sg || !dg) continue;
    if (argLabelById.get(e.from) !== sg.label) continue;
    if (argLabelById.get(e.to) !== dg.label) continue;
    const pair = sk + '->' + dk;
    const prev = edgeMap.get(pair);
    if (prev === 'rebut') continue;
    edgeMap.set(pair, e.type);
  }

  // Drop conclusion nodes that have no edges (isolated). These are
  // usually fact / assumption / validity-chain supports that feed into
  // the closure but don't themselves engage in the dialectic.
  //
  // Exempt literals that are explicitly declared as a proposition or
  // conclusion in the scenario: even when unchallenged (e.g. a newly-
  // added rule that derives an accepted conclusion with no attackers),
  // the user expects to see them in the graph because they also appear
  // in the Conclusions panel's "All" filter.
  const participating = new Set();
  for (const pair of edgeMap.keys()) {
    const [sk, dk] = pair.split('->');
    participating.add(sk);
    participating.add(dk);
  }
  const exempt = new Set([
    ...Object.keys(scn.conclusions || {}),
    ...Object.keys(scn.propositions || {}),
  ]);
  for (const k of [...groups.keys()]) {
    const base = k.startsWith('-') ? k.slice(1) : k;
    if (!participating.has(k) && !exempt.has(base)) groups.delete(k);
  }
  if (groups.size === 0) {
    body.innerHTML = `<div class="placeholder-msg">No attacks in the current state: every argument is isolated.</div>`;
    return;
  }

  // --- Layer partition ------------------------------------------------------
  // Finite layers: sorted ascending. Layer 1 at the bottom of the diagram.
  // Infinite cluster goes to the right as its own column.
  const finiteLayers = new Map();
  const infGroup = [];
  for (const g of groups.values()) {
    if (g.min_max === 'inf') { infGroup.push(g); continue; }
    if (!finiteLayers.has(g.min_max)) finiteLayers.set(g.min_max, []);
    finiteLayers.get(g.min_max).push(g);
  }
  // Stable sort within each layer by (top_rule, conclusion) so layouts
  // are deterministic across renders.
  const sortKey = (g) => g.top_rule + '::' + g.conclusion;
  for (const layer of finiteLayers.values()) layer.sort((a, b) => sortKey(a).localeCompare(sortKey(b)));
  infGroup.sort((a, b) => sortKey(a).localeCompare(sortKey(b)));

  const depths = [...finiteLayers.keys()].sort((a, b) => a - b);

  // --- Layout via dagre -----------------------------------------------------
  // Dagre handles layered ranking + polyline edge routing. rankdir 'BT'
  // places sources (attackers with no incoming edges) at the bottom and
  // sinks at the top (sources = attackers with no incoming edges).
  const NODE_W = 170, NODE_H = 36;
  if (typeof dagre === 'undefined') {
    body.innerHTML = `<div class="placeholder-msg">Graph layout library failed to load (dagre). Check your network and reload.</div>`;
    return;
  }
  const dg = new dagre.graphlib.Graph();
  dg.setGraph({ rankdir: 'BT', nodesep: 30, ranksep: 55, marginx: 24, marginy: 24 });
  dg.setDefaultEdgeLabel(() => ({}));
  for (const g of groups.values()) dg.setNode(g.key, { width: NODE_W, height: NODE_H });
  for (const [pair, type] of edgeMap) {
    const [sk, dk] = pair.split('->');
    dg.setEdge(sk, dk, { type });
  }
  dagre.layout(dg);
  // Compute the real bounding box including every edge waypoint. Dagre
  // reports `graph().width/height` based on node extents plus margins,
  // but the polyline control points it places for long back-edges can
  // extend beyond that box -- if we used the dagre-reported dimensions
  // as the viewBox, those segments would be drawn outside the canvas
  // and appear to wrap around. Taking the real bounds guarantees every
  // segment stays inside.
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const key of dg.nodes()) {
    const n = dg.node(key);
    minX = Math.min(minX, n.x - n.width / 2);
    minY = Math.min(minY, n.y - n.height / 2);
    maxX = Math.max(maxX, n.x + n.width / 2);
    maxY = Math.max(maxY, n.y + n.height / 2);
  }
  for (const e of dg.edges()) {
    for (const p of (dg.edge(e).points || [])) {
      minX = Math.min(minX, p.x);
      minY = Math.min(minY, p.y);
      maxX = Math.max(maxX, p.x);
      maxY = Math.max(maxY, p.y);
    }
  }
  const BBOX_PAD = 24;
  minX -= BBOX_PAD; minY -= BBOX_PAD; maxX += BBOX_PAD; maxY += BBOX_PAD;
  const totalWidth = maxX - minX;
  const totalHeight = maxY - minY;

  // --- Rendering ------------------------------------------------------------
  const fillFor = (label) => ({
    in:    '#3a7ad0',
    out:   '#e08a3a',
    undec: '#e6c94e',
  })[label] || '#b0b6c0';
  const textFor = (label) => label === 'undec' ? '#5a5a5a' : '#fff';

  // Build a smooth SVG path from a polyline of points using the classic
  // "quadratic Bezier through midpoints" construction: the curve starts
  // at pts[0], draws a Q segment with each interior point as control,
  // ending at the midpoint of the next pair, then a final L to pts[last].
  // Rounds sharp bends that dagre hands back without introducing wild
  // overshoots.
  const smoothPath = (pts) => {
    if (pts.length < 3) {
      return pts.map((p, i) => (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ',' + p.y.toFixed(1)).join(' ');
    }
    let d = `M ${pts[0].x.toFixed(1)},${pts[0].y.toFixed(1)}`;
    for (let i = 1; i < pts.length - 1; i++) {
      const curr = pts[i], next = pts[i + 1];
      const mx = (curr.x + next.x) / 2;
      const my = (curr.y + next.y) / 2;
      d += ` Q ${curr.x.toFixed(1)},${curr.y.toFixed(1)} ${mx.toFixed(1)},${my.toFixed(1)}`;
    }
    d += ` L ${pts[pts.length - 1].x.toFixed(1)},${pts[pts.length - 1].y.toFixed(1)}`;
    return d;
  };

  // A rebut edge is "mutual" (equal-strength, no preference decided it)
  // when the reverse direction is also in the AF. Anything else that's
  // solid is preference-decided — and gets the hollow arrowhead that
  // signals asymmetry in the legend. Undercut stays filled (it's always
  // one-way by structure, not by preference).
  const isMutualRebut = (sk, dk, type) => {
    if (type !== 'rebut') return false;
    return edgeMap.get(dk + '->' + sk) === 'rebut';
  };

  let edgeSvg = '';
  for (const e of dg.edges()) {
    const edge = dg.edge(e);
    const pts = edge.points || [];
    if (pts.length < 2) continue;
    // Dagre's last point lies on the target node's border. Pull it back
    // a few pixels along the final segment so the arrowhead has a gap.
    const tail = pts[pts.length - 2];
    const head = pts[pts.length - 1];
    const dx = head.x - tail.x, dy = head.y - tail.y;
    const len = Math.hypot(dx, dy) || 1;
    const back = 4;
    const headAdj = { x: head.x - (dx / len) * back, y: head.y - (dy / len) * back };
    const routed = [...pts.slice(0, -1), headAdj];
    const d = smoothPath(routed);
    const dash = edge.type === 'undercut' ? '6,4' : '';
    const hollow = edge.type === 'rebut' && !isMutualRebut(e.v, e.w, edge.type);
    const marker = hollow ? 'af-arrow-hollow' : 'af-arrow';
    edgeSvg += `<path d="${d}" stroke="#5a6a78" stroke-width="1.5" stroke-dasharray="${dash}" fill="none" marker-end="url(#${marker})"/>`;
  }

  // A conclusion literal `-<name>` where <name> is a rule id is an
  // undercut literal (meta-claim that the rule does not apply here), not
  // a propositional negation. Render it with a ✕ prefix and a dashed
  // node border so it stops looking identical to, say, ¬crispy.
  const ruleIds = new Set(Object.keys(scn.rules || {}));
  const isRuleUndercut = (lit) => lit.startsWith('-') && ruleIds.has(lit.slice(1));
  const THIN_SP = '\u2009';      // half-width separator for "- atom"
  const NBSP    = '\u00a0';      // keep ✕ and the rule name on the same line
  const formatLiteralLabel = (lit) => {
    if (isRuleUndercut(lit)) return '✕' + NBSP + lit.slice(1);
    return lit;
  };

  let nodeSvg = '';
  for (const key of dg.nodes()) {
    const n = dg.node(key);
    const g = groups.get(key);
    if (!g || !n) continue;
    const x = n.x - n.width / 2;
    const y = n.y - n.height / 2;
    const fill = fillFor(g.label);
    const color = textFor(g.label);
    const undercutNode = isRuleUndercut(g.conclusion);
    // Render the label: for undercut-literal nodes, bump the ✕ glyph two
    // sizes larger than the rule name via a tspan so the undercut signal
    // reads strongly even at default zoom. Otherwise render as one run.
    const labelSvg = undercutNode
      ? `<tspan font-size="20" font-weight="800">✕</tspan><tspan>${NBSP}${escapeHtml(g.conclusion.slice(1))}</tspan>`
      : escapeHtml(formatLiteralLabel(g.conclusion));
    nodeSvg += `<g transform="translate(${x}, ${y})" class="af-node${undercutNode ? ' af-node-undercut' : ''}" data-af-concl="${escapeAttr(g.conclusion_nl)}" data-af-label="${escapeAttr(g.label)}" data-af-lit="${escapeAttr(g.conclusion)}" data-af-rules="${escapeAttr(g.rules.join(', '))}">
      <rect width="${NODE_W}" height="${NODE_H}" rx="6" ry="6" fill="${fill}" stroke="#203040" stroke-width="0.5"/>
      <text x="${NODE_W / 2}" y="${NODE_H / 2}" text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="600" fill="${color}" font-family="SF Mono, Menlo, Consolas, monospace">${labelSvg}</text>
    </g>`;
  }

  // Legend: labels (fill colors), edges (rebut mutual / rebut preference /
  // undercut), and undercut-literal node style.
  const arrowDefs = `
    <defs>
      <marker id="lgd-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="#5a6a78"/>
      </marker>
      <marker id="lgd-arrow-hollow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M 0 0 L 10 5 L 0 10" fill="none" stroke="#5a6a78" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/>
      </marker>
    </defs>`;
  const legend = `
    <div class="af-legend">
      <span class="af-swatch" style="background:#3a7ad0"></span> Accepted
      <span class="af-swatch" style="background:#e08a3a"></span> Rejected
      <span class="af-swatch" style="background:#e6c94e"></span> Undecided
      <span class="af-legend-sep"></span>
      <span class="af-edge-sample" title="Rebut &mdash; mutual (equal-strength conflict, both sides attack)"><svg width="34" height="10">${arrowDefs}<line x1="2" y1="5" x2="28" y2="5" stroke="#5a6a78" stroke-width="1.5" marker-end="url(#lgd-arrow)"/></svg></span> Rebut (mutual)
      <span class="af-edge-sample" title="Rebut &mdash; preference decided (stronger side won, one-way)"><svg width="34" height="10">${arrowDefs}<line x1="2" y1="5" x2="28" y2="5" stroke="#5a6a78" stroke-width="1.5" marker-end="url(#lgd-arrow-hollow)"/></svg></span> Rebut (preference)
      <span class="af-edge-sample" title="Undercut &mdash; the target's rule does not apply here"><svg width="34" height="10">${arrowDefs}<line x1="2" y1="5" x2="28" y2="5" stroke="#5a6a78" stroke-width="1.5" stroke-dasharray="6,4" marker-end="url(#lgd-arrow)"/></svg></span> Undercut
      <span class="af-legend-ucnode" title="Rule undercut &mdash; a node asserting that the named rule does not apply here (bold ✕ prefix distinguishes this from a propositional negation)"><span class="af-ucnode-sample"><span class="af-ucnode-x">✕</span>&nbsp;rule</span></span> Rule undercut
    </div>
  `;

  body.innerHTML = `
    ${legend}
    <div class="af-toolbar">
      <div class="af-scope-control">
        <span class="af-control-label">Scope:</span>
        <button class="af-scope-btn ${afScope==='key' ? 'active' : ''}" data-af-scope="key" title="Only show arguments relevant to the key conclusions">Key conclusions</button>
        <button class="af-scope-btn ${afScope==='all' ? 'active' : ''}" data-af-scope="all" title="Show every argument in the AF">All conclusions</button>
      </div>
      <div class="af-zoom-controls">
        <button class="btn btn-small" data-af-zoom="out" title="Zoom out">−</button>
        <button class="btn btn-small" data-af-zoom="reset" title="Reset to 100%">100%</button>
        <button class="btn btn-small" data-af-zoom="in" title="Zoom in">+</button>
        <button class="btn btn-small" data-af-zoom="fit" title="Fit to window">Fit</button>
        <span class="af-zoom-readout" id="af-zoom-readout">100%</span>
      </div>
    </div>
    <div class="af-svg-scroll" id="af-svg-scroll">
      <svg width="${totalWidth}" height="${totalHeight}" viewBox="${minX} ${minY} ${totalWidth} ${totalHeight}" xmlns="http://www.w3.org/2000/svg" data-base-w="${totalWidth}" data-base-h="${totalHeight}">
        <defs>
          <marker id="af-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#5a6a78"/>
          </marker>
          <marker id="af-arrow-hollow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10" fill="none" stroke="#5a6a78" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/>
          </marker>
        </defs>
        ${edgeSvg}
        ${nodeSvg}
      </svg>
    </div>
    <div class="af-tooltip" id="af-tooltip" style="display:none"></div>
  `;
  afZoom = 1;
  applyAFZoom();
  wireAFZoom();
  wireAFTooltip();
}

// Zoom state and controls. Scaling is done by resizing the SVG's width/
// height attributes while the viewBox is unchanged; coordinates inside
// the SVG remain the same. The scroll container (.af-svg-scroll) lets
// the user pan when the scaled diagram overflows.
let afZoom = 1;
function applyAFZoom() {
  const svg = document.querySelector('#af-svg-scroll svg');
  if (!svg) return;
  const w = Number(svg.dataset.baseW);
  const h = Number(svg.dataset.baseH);
  svg.setAttribute('width', (w * afZoom).toFixed(1));
  svg.setAttribute('height', (h * afZoom).toFixed(1));
  const readout = document.getElementById('af-zoom-readout');
  if (readout) readout.textContent = Math.round(afZoom * 100) + '%';
}
function wireAFZoom() {
  const body = document.getElementById('af-modal-body');
  if (!body) return;
  const STEP = 0.15, MIN = 0.25, MAX = 3;
  for (const btn of body.querySelectorAll('[data-af-zoom]')) {
    btn.addEventListener('click', () => {
      const action = btn.dataset.afZoom;
      if (action === 'in')         afZoom = Math.min(MAX, afZoom + STEP);
      else if (action === 'out')   afZoom = Math.max(MIN, afZoom - STEP);
      else if (action === 'reset') afZoom = 1;
      else if (action === 'fit')   afZoom = computeAFFit();
      applyAFZoom();
    });
  }
  // Scope toggle: re-renders the whole view with the new filter, then
  // re-fits so the new graph is sized to the container.
  for (const btn of body.querySelectorAll('[data-af-scope]')) {
    btn.addEventListener('click', () => {
      const next = btn.dataset.afScope;
      if (next === afScope) return;
      afScope = next;
      renderAFView();
      requestAnimationFrame(() => { afZoom = computeAFFit(); applyAFZoom(); });
    });
  }
  // Mouse wheel + Ctrl/Cmd → zoom. Plain wheel scrolls the container.
  const scroll = document.getElementById('af-svg-scroll');
  if (scroll) {
    scroll.addEventListener('wheel', e => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      const delta = e.deltaY > 0 ? -STEP : STEP;
      afZoom = Math.min(MAX, Math.max(MIN, afZoom + delta));
      applyAFZoom();
    }, { passive: false });
  }
}
function computeAFFit() {
  const svg = document.querySelector('#af-svg-scroll svg');
  const container = document.getElementById('af-svg-scroll');
  if (!svg || !container) return 1;
  const w = Number(svg.dataset.baseW);
  const h = Number(svg.dataset.baseH);
  // Subtract container padding (.4rem all around ≈ 13px) plus a small
  // breathing margin so nodes at the boundary don't clip against the
  // container edge.
  const availW = container.clientWidth - 24;
  const availH = container.clientHeight - 24;
  if (availW <= 0 || availH <= 0) return 1;
  // Don't scale up past 1.0 on open -- a small graph shouldn't be
  // blown up and pixelated just to fill the viewport. Scale down if
  // the natural size overflows.
  const raw = Math.min(availW / w, availH / h);
  return Math.max(0.25, Math.min(1, raw));
}

// Custom hover tooltip: shows the full conclusion NL (matching the text
// in the Conclusions dashboard) plus the rule id and status lozenge.
// Uses position:fixed so it doesn't care about modal-body scroll.
function wireAFTooltip() {
  const body = document.getElementById('af-modal-body');
  const tip = document.getElementById('af-tooltip');
  if (!body || !tip) return;
  const statusMap = { in: 'Accepted', out: 'Rejected', undec: 'Undecided' };
  const statusClass = { in: 'status-accepted', out: 'status-rejected', undec: 'status-undecided' };
  for (const el of body.querySelectorAll('g.af-node')) {
    el.addEventListener('mouseenter', () => {
      const concl = el.dataset.afConcl || '';
      const label = el.dataset.afLabel || '';
      const rulesCsv = el.dataset.afRules || '';
      const ruleTags = rulesCsv.split(',').map(r => r.trim()).filter(Boolean)
        .map(r => `<span class="inline-id">[${escapeHtml(r)}]</span>`).join(' ');
      tip.innerHTML = `
        <div class="af-tooltip-claim">${escapeHtml(concl)}</div>
        <div class="af-tooltip-meta">
          ${ruleTags}
          <span class="af-tooltip-status ${statusClass[label] || ''}">${escapeHtml(statusMap[label] || label)}</span>
        </div>
      `;
      tip.style.display = 'block';
      // Position to the right of the node; flip left if it would overflow.
      const rect = el.getBoundingClientRect();
      const tipW = tip.offsetWidth;
      const tipH = tip.offsetHeight;
      let left = rect.right + 10;
      if (left + tipW > window.innerWidth - 8) left = rect.left - tipW - 10;
      let top = rect.top;
      if (top + tipH > window.innerHeight - 8) top = window.innerHeight - tipH - 8;
      tip.style.left = Math.max(8, left) + 'px';
      tip.style.top = Math.max(8, top) + 'px';
    });
    el.addEventListener('mouseleave', () => {
      tip.style.display = 'none';
    });
  }
}

/* ================================================================
   Game Explorer — interactive argument-tree walker
   Opened from the Explain button on a conclusion. User picks one of
   the candidate arguments (filtered by label matching the conclusion
   status), then walks the HTB/CB dialectic by expanding nodes. No
   two-player adversarial play; user expands and backtracks freely.
   Reads everything from state.bundle.af.
   ================================================================ */

// Game-scoped state (reset each time the modal opens).
let gameNodes = {};
let gameNodeCounter = 0;
let gameFocusId = null;
let gameRootId = null;
let gameConclusionId = null;
// When the conclusion is rejected purely because a strict rule derives its
// negation (no for-argument at all), there is no Caminada game trace to
// play -- we render a prose rationale plus the winning argument card, no
// HTB/CB moves, no "Back to arguments" (picker is skipped upstream).
let gameExplanationOnly = false;
let _renderGuard = 0;

function makeGameNode(type, argId, parentId) {
  const id = 'gn' + (++gameNodeCounter);
  const node = { id, type, argId, parentId, children: [], resolution: null, collapsed: false };
  gameNodes[id] = node;
  return node;
}

// --- AF lookups ------------------------------------------------------

function getArgumentById(argId) {
  return (state.bundle.af.arguments || []).find(a => a.id === argId);
}

function getAttackersOf(argId) {
  return (state.bundle.af.attacks || []).filter(a => a.to === argId);
}

function getArgumentsConcluding(conclusionId) {
  return (state.bundle.af.arguments || []).filter(a => a.conclusion === conclusionId);
}

// A "canonical argument" is a (top_rule, conclusion) pair. Different
// Cartesian-product derivations that share this pair differ only in
// their sub-argument substructure and are treated as one logical
// argument throughout the game explorer -- picker, tree, and moves
// panel all operate at this level so the user doesn't see duplicates
// like "Popov has possession of the ball [a42]" and "... [a43]" for
// what is, to them, the same bb1a-based claim.
function canonicalKey(arg) {
  return arg ? arg.top_rule + '::' + arg.conclusion : '';
}

function getVariantsOf(argId) {
  const arg = getArgumentById(argId);
  if (!arg) return [];
  const key = canonicalKey(arg);
  return (state.bundle.af.arguments || []).filter(a => canonicalKey(a) === key);
}

// Canonical attackers: union of attackers across every variant of
// the canonical target, then deduped by the attacker's own canonical
// key. One entry per distinct (attacker_top_rule, attacker_conclusion).
function getCanonicalAttackerIds(argId) {
  const variants = getVariantsOf(argId).map(a => a.id);
  const variantSet = new Set(variants);
  const attackerIds = new Set();
  for (const e of state.bundle.af.attacks || []) {
    if (variantSet.has(e.to)) attackerIds.add(e.from);
  }
  const seen = new Set();
  const result = [];
  for (const aid of attackerIds) {
    const a = getArgumentById(aid);
    if (!a) continue;
    const key = canonicalKey(a);
    if (!seen.has(key)) { seen.add(key); result.push(aid); }
  }
  return result;
}

// Arguments concluding this proposition that are consistent with the
// aggregated status. Mapping:
//   accepted  → "in" args only
//   rejected  → "out" args only
//   undecided → "out" OR "undec" args (an undecided proposition can
//               never have an "in" arg, but it CAN consist entirely of
//               "out" arguments when no "in" argument for -X exists --
//               e.g. Popov's popov_has_poss under the Cartesian fix.)
//   absent    → no candidates (modal disables Explain upstream)
function getCandidateRootArguments(conclusionId) {
  const status = state.bundle.af.labels_by_proposition?.[conclusionId];
  const allowed = {
    accepted: new Set(['in']),
    rejected: new Set(['out']),
    undecided: new Set(['out', 'undec']),
  }[status];
  if (!allowed) return [];
  let matching = getArgumentsConcluding(conclusionId).filter(a => allowed.has(a.label));
  // Rejected-via-negation fallback: the conclusion has no for-arguments
  // at all (e.g. reached only through a strict rule on -c). Surface the
  // "in" arguments for -c as explainable roots -- walking one of them
  // shows why -c is warranted, which is why c is rejected.
  if (status === 'rejected' && matching.length === 0) {
    matching = getArgumentsConcluding('-' + conclusionId).filter(a => a.label === 'in');
  }
  // Dedupe by canonical key (top_rule + conclusion) so Cartesian
  // variants of the same logical argument show as one picker entry.
  const seen = new Set();
  const out = [];
  for (const a of matching) {
    const key = canonicalKey(a);
    if (!seen.has(key)) { seen.add(key); out.push(a); }
  }
  return out;
}

// --- Modal open / close ---------------------------------------------

function openExplainModal(conclusionId) {
  gameConclusionId = conclusionId;
  gameNodes = {};
  gameNodeCounter = 0;
  gameFocusId = null;
  gameRootId = null;
  gameExplanationOnly = false;

  const scn = state.bundle.scenario;
  const entry = scn.conclusions?.[conclusionId] || scn.propositions?.[conclusionId];
  document.getElementById('game-modal-title').textContent =
    'Explain: ' + (entry ? entry.description : conclusionId);

  // Route directly to the explanation view when the conclusion is
  // rejected and has no for-arguments (so nothing to walk). The unique
  // in-argument for -c becomes the explanation root.
  const af = state.bundle.af;
  const status = af.labels_by_proposition?.[conclusionId];
  const forArgs = (af.arguments || []).filter(a => a.conclusion === conclusionId);
  if (status === 'rejected' && forArgs.length === 0) {
    const negIn = (af.arguments || []).find(
      a => a.conclusion === '-' + conclusionId && a.label === 'in'
    );
    if (negIn) {
      gameExplanationOnly = true;
      const root = makeGameNode('htb', negIn.id, null);
      gameRootId = root.id;
      gameFocusId = root.id;
      renderGame();
      document.getElementById('modal-game').classList.add('visible');
      return;
    }
  }

  renderArgumentPicker();
  document.getElementById('modal-game').classList.add('visible');
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove('visible');
}

// --- Edit modal: Propose / Refine / Apply for add-rule, modify-rule, add-fact, add-assumption ---

// Per-modal state. Reset on open; cleared on close.
const editState = {
  task: null,              // 'add-rule' | 'modify-rule' | 'add-fact' | 'add-assumption'
  existingId: null,        // for modify-rule
  lastProposal: null,      // most recent {op, review_issues, ...} from /propose
  inFlight: false,
};

function openEditModal(task, existingId = null) {
  editState.task = task;
  editState.existingId = existingId;
  editState.lastProposal = null;
  editState.inFlight = false;

  const titles = {
    'add-rule': 'Add Rule',
    'modify-rule': existingId ? `Edit Rule: ${existingId}` : 'Edit Rule',
    'add-fact': 'Add Fact',
    'add-assumption': 'Add Assumption',
  };
  document.getElementById('edit-modal-title').textContent = titles[task] || 'Edit';

  const ta = document.getElementById('edit-instruction');
  ta.value = '';
  if (task === 'modify-rule' && existingId) {
    // Seed the textarea with the current rule body so the user can edit
    // it directly or replace with NL.
    const rule = state.bundle?.scenario?.rules?.[existingId];
    if (rule) {
      const premiseLits = (rule.premises || []).map(p => renderLiteral(p));
      const conclusionLit = renderLiteral(rule.conclusion);
      const connective = rule.type === 'strict' ? 'necessarily' : 'normally';
      const current = premiseLits.length
        ? `Current rule: If ${premiseLits.join(' and ')} then ${connective} ${conclusionLit}.\n\nChanges I want:\n`
        : `Current rule: ${connective} ${conclusionLit}.\n\nChanges I want:\n`;
      ta.placeholder = current;
    }
  } else {
    ta.placeholder = {
      'add-rule': "e.g. 'Add a rule saying that if X then normally/necessarily Y.'  Use 'normally' for a defeasible rule (can be defeated) or 'necessarily' for a strict rule (cannot be defeated).",
      'add-fact': "e.g. 'Add a fact that the patient is on a chronic PPI.'",
      'add-assumption': "e.g. 'Add an assumption that the witness is treated as reliable.'",
    }[task] || '';
  }

  document.getElementById('edit-instruction-label').textContent =
    task === 'modify-rule'
      ? 'Describe how you want this rule to change:'
      : 'Describe the edit you want:';

  document.getElementById('edit-status').innerHTML = '';
  document.getElementById('edit-preview').innerHTML = '';
  _renderEditFooter();

  document.getElementById('modal-edit').classList.add('visible');
  setTimeout(() => ta.focus(), 0);
}

function closeEditModal() {
  editState.task = null;
  editState.existingId = null;
  editState.lastProposal = null;
  editState.inFlight = false;
  document.getElementById('modal-edit').classList.remove('visible');
}

async function sendPropose() {
  if (editState.inFlight) return;
  const ta = document.getElementById('edit-instruction');
  const instruction = ta.value.trim();
  if (!instruction) {
    _setEditStatus('error', 'Please describe what you want to edit.');
    return;
  }

  editState.inFlight = true;
  editState.lastProposal = null;
  _setEditStatus('loading', 'Proposing…');
  document.getElementById('edit-preview').innerHTML = '';
  _renderEditFooter();

  const payload = {
    scenario_id: state.scenario_id,
    diff_ops: state.diff_ops,
    task: editState.task,
    instruction,
  };
  if (editState.task === 'modify-rule') payload.existing_id = editState.existingId;

  try {
    const r = await fetch('/propose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      _renderEditError(r.status, body);
      return;
    }
    editState.lastProposal = body;
    _renderProposal(body);
    _setEditStatus('ok', `Proposed in ${body.latency_ms} ms${body.proposer_attempts > 1 ? ` (${body.proposer_attempts} attempts)` : ''}.`);
  } catch (e) {
    _setEditStatus('error', `Network error: ${e.message}`);
  } finally {
    editState.inFlight = false;
    _renderEditFooter();
  }
}

async function applyProposal() {
  if (!editState.lastProposal?.op) return;
  const op = editState.lastProposal.op;
  closeEditModal();
  await applyOp(op);
}

function refineProposal() {
  // Keep the modal open, clear the preview so the user can retype.
  editState.lastProposal = null;
  document.getElementById('edit-preview').innerHTML = '';
  _setEditStatus('', '');
  _renderEditFooter();
  document.getElementById('edit-instruction').focus();
}

function _setEditStatus(kind, msg) {
  const el = document.getElementById('edit-status');
  if (!msg) { el.innerHTML = ''; return; }
  const cls = kind ? `edit-status-${kind}` : '';
  if (kind === 'loading') {
    el.innerHTML = `<div class="${cls}"><span class="edit-loading-label">${escapeHtml(msg)}</span><span class="edit-loading-dots"><span class="chat-dot"></span><span class="chat-dot"></span><span class="chat-dot"></span></span></div>`;
    return;
  }
  el.innerHTML = `<div class="${cls}">${escapeHtml(msg)}</div>`;
}

function _renderEditFooter() {
  const footer = document.getElementById('edit-footer');
  const hasProposal = !!editState.lastProposal?.op;
  if (hasProposal) {
    footer.innerHTML = `
      <button class="btn" onclick="closeEditModal()">Cancel</button>
      <button class="btn" onclick="refineProposal()">Refine</button>
      <button class="btn btn-primary" onclick="applyProposal()">Apply</button>
    `;
  } else {
    const busy = editState.inFlight;
    footer.innerHTML = `
      <button class="btn" onclick="closeEditModal()">Cancel</button>
      <button class="btn btn-primary" onclick="sendPropose()" ${busy ? 'disabled' : ''}>${busy ? 'Proposing…' : 'Propose'}</button>
    `;
  }
}

function _renderProposal(body) {
  const preview = document.getElementById('edit-preview');
  const op = body.op;
  const kind = op.op;

  let mainHtml = '';
  if (kind === 'add-rule' || kind === 'modify-rule') {
    const rule = op.rule;
    const connective = rule.type === 'strict' ? 'necessarily' : 'normally';
    const proposerNotes = op.new_premise_notes || [];
    const notesById = {};
    for (const n of proposerNotes) notesById[n.id] = n.description;

    // --- NL view (top) ---
    // Use descMap when the literal is already in the scenario; fall
    // back to the Proposer's new_premise_notes description for
    // forward references; last-resort fall back to the bare id so
    // there's always something readable.
    const nlFor = (lit) => {
      const neg = lit.startsWith('-');
      const base = neg ? lit.slice(1) : lit;
      let desc = state.descMap[base];
      if (!desc && notesById[base]) desc = notesById[base];
      if (!desc && state.ruleIds?.has(base)) return neg ? `rule ${base} does not apply` : `rule ${base} applies`;
      if (!desc) desc = base;  // fallback: bare id
      return neg ? `it is not the case that ${desc}` : desc;
    };
    const nlPremises = (rule.premises || []).map(nlFor);
    const nlConclusion = nlFor(rule.conclusion);
    const nlPremisesHtml = nlPremises.map(escapeHtml).join(' <span class="kw">and</span> ');
    const nlBody = nlPremises.length
      ? `<span class="kw">If</span> ${nlPremisesHtml} <span class="kw">then</span> <span class="kw">${connective}</span> ${escapeHtml(nlConclusion)}`
      : `<span class="kw">${connective}</span> ${escapeHtml(nlConclusion)}`;

    // --- ASPIC- view (bottom) ---
    const arrow = rule.type === 'strict' ? '->' : '=>';
    const aspicArrow = `<span class="aspic-arrow">${escapeHtml(arrow)}</span>`;
    const aspicLit = (lit) => {
      if (lit.startsWith('-')) {
        return `<span class="aspic-negation">-</span>${escapeHtml(lit.slice(1))}`;
      }
      return escapeHtml(lit);
    };
    const aspicPremises = (rule.premises || []).map(aspicLit).join(', ');
    const aspicConclusion = aspicLit(rule.conclusion);
    const aspicBody = aspicPremises
      ? `${aspicPremises} ${aspicArrow} ${aspicConclusion}`
      : `${aspicArrow} ${aspicConclusion}`;
    const aspicLine = `${aspicBody} <span class="aspic-name">[${escapeHtml(op.id)}]</span>`;

    mainHtml = `
      <div class="edit-prop-heading">${kind === 'add-rule' ? 'Proposed new rule' : 'Proposed updated rule'} <span class="inline-id">[${escapeHtml(op.id)}]</span></div>
      <div class="edit-prop-body">${nlBody}</div>
      <div class="edit-prop-aspic-label">ASPIC- syntax</div>
      <pre class="edit-prop-aspic">${aspicLine}</pre>
    `;
  } else if (kind === 'add-fact' || kind === 'add-assumption') {
    const payload = op.fact || op.assumption;
    // Detect "promotion": this op's id already exists as a pending
    // proposition (declared during a prior rule edit but not yet
    // backed by a fact/assumption/rule). Promoting replaces the
    // pending entry -- the upstream rule that introduced it then
    // becomes firable. Flag this distinctly so the user understands
    // they're not adding a fresh item but fulfilling a forward
    // reference.
    const pending = _pendingPropositionFor(op.id);
    if (pending) {
      const kindLabel = kind === 'add-fact' ? 'fact' : 'assumption';
      mainHtml = `
        <div class="edit-prop-heading edit-prop-promote">Promoting pending item into a ${kindLabel} <span class="inline-id">[${escapeHtml(op.id)}]</span></div>
        <div class="edit-prop-promote-ref">Currently in the scenario as: <em>${escapeHtml(pending.description)}</em></div>
        <div class="edit-prop-body">${escapeHtml(payload.description)}</div>
      `;
    } else {
      mainHtml = `
        <div class="edit-prop-heading">${kind === 'add-fact' ? 'Proposed new fact' : 'Proposed new assumption'} <span class="inline-id">[${escapeHtml(op.id)}]</span></div>
        <div class="edit-prop-body">${escapeHtml(payload.description)}</div>
      `;
    }
  }

  // Metadata strip: category, source, block (when present).
  const meta = op.rule || op.fact || op.assumption || {};
  const metaBits = [];
  if (meta.category) metaBits.push(`Category: ${escapeHtml(meta.category)}`);
  if (meta.source) metaBits.push(`Source: ${escapeHtml(meta.source)}`);
  if (meta.block && meta.block !== 1) metaBits.push(`Block: ${meta.block}`);
  if (meta.active === false) metaBits.push('Inactive');
  const metaHtml = metaBits.length ? `<div class="edit-prop-meta">${metaBits.join(' · ')}</div>` : '';

  // Advisory Reviewer issues.
  let issuesHtml = '';
  if (body.review_issues?.length) {
    const rows = body.review_issues.map(iss => {
      const sev = iss.severity;
      const icon = sev === 'blocker' ? '⛔' : sev === 'warning' ? '⚠' : 'ℹ';
      return `<li class="edit-issue edit-issue-${escapeAttr(sev)}"><span class="edit-issue-icon">${icon}</span><span>${escapeHtml(iss.message)}</span></li>`;
    }).join('');
    issuesHtml = `
      <div class="edit-issues-heading">Reviewer notes (advisory — you can still Apply):</div>
      <ul class="edit-issues">${rows}</ul>
    `;
  }

  preview.innerHTML = mainHtml + metaHtml + issuesHtml;
}

// A proposition is "pending" when it's declared in scenario.propositions
// but has no rule concluding it -- typically a forward-reference
// auto-declared during a prior rule edit. Returns the pending
// proposition entry, or null if `id` isn't pending.
function _pendingPropositionFor(id) {
  const scn = state.bundle?.scenario;
  if (!scn) return null;
  const prop = scn.propositions?.[id];
  if (!prop) return null;
  // Must not also exist as fact / assumption / conclusion / rule
  // (those are never "pending" -- they're already defined).
  if (scn.facts?.[id] || scn.assumptions?.[id] || scn.conclusions?.[id] || scn.rules?.[id]) {
    return null;
  }
  // Must have no rule concluding it. Strip leading '-' when comparing.
  const rules = scn.rules || {};
  for (const r of Object.values(rules)) {
    const c = r.conclusion || '';
    const ref = c.startsWith('-') ? c.slice(1) : c;
    if (ref === id) return null;
  }
  return prop;
}

function _renderEditError(status, body) {
  // Unknown-premise is no longer an error path -- it comes through as a
  // severity=warning review_issue with the op. What remains here is a
  // generic 422 proposer_retry_exhausted (blocking issues the Proposer
  // couldn't fix across 3 attempts) plus other 4xx/5xx.
  const detail = body?.detail;
  const msg = detail?.message || detail || body?.detail || `Error ${status}`;
  _setEditStatus('error', typeof msg === 'string' ? msg : JSON.stringify(msg));
}

// --- Save as new scenario ---------------------------------------------

// Per-modal state. The title/id inputs are live-bound; `idEdited` tracks
// whether the user has manually touched the id field so we stop the
// auto-slug from clobbering their edits on subsequent title keystrokes.
// `overwriteSource` mirrors the checkbox that asks "overwrite current?".
const saveState = {
  idEdited: false,
  inFlight: false,
  overwriteSource: false,
};

function openSaveModal() {
  saveState.idEdited = false;
  saveState.inFlight = false;
  saveState.overwriteSource = false;
  const titleInput = document.getElementById('save-title');
  const idInput = document.getElementById('save-id');
  const overwriteCb = document.getElementById('save-overwrite-source');
  titleInput.value = '';
  idInput.value = '';
  titleInput.disabled = false;
  idInput.disabled = false;
  overwriteCb.checked = false;
  _setSaveSubmitLabel('Save');
  _setSaveStatus('', '');
  _setSaveSubmitEnabled(true);
  document.getElementById('modal-save').classList.add('visible');
  titleInput.focus();
}

function onSaveOverwriteToggle() {
  const cb = document.getElementById('save-overwrite-source');
  const titleInput = document.getElementById('save-title');
  const idInput = document.getElementById('save-id');
  saveState.overwriteSource = cb.checked;
  if (cb.checked) {
    // Auto-fill with the currently loaded scenario's title and id; lock
    // both fields so the user can't drift mid-flow. The confirm modal on
    // submit is the real guard against accidental overwrite.
    const scn = state.bundle?.scenario;
    titleInput.value = scn?.title || '';
    idInput.value = state.scenario_id || '';
    titleInput.disabled = true;
    idInput.disabled = true;
    _setSaveSubmitLabel('Overwrite');
    _setSaveStatus('', '');
  } else {
    titleInput.value = '';
    idInput.value = '';
    titleInput.disabled = false;
    idInput.disabled = false;
    saveState.idEdited = false;
    _setSaveSubmitLabel('Save');
    _setSaveStatus('', '');
    titleInput.focus();
  }
}

function onSaveTitleInput() {
  if (saveState.idEdited) return;
  const title = document.getElementById('save-title').value;
  document.getElementById('save-id').value = _slugifyScenarioId(title);
}

function onSaveIdInput() {
  // User touched the id field manually; stop mirroring from title.
  saveState.idEdited = true;
}

// Slugify a free-form title into a valid scenario id. Pattern is the
// same identifier regex the server enforces: [A-Za-z_][A-Za-z0-9_]*
function _slugifyScenarioId(title) {
  let slug = title.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
  // Id must start with a letter or underscore; prepend '_' if it starts with a digit.
  if (/^[0-9]/.test(slug)) slug = '_' + slug;
  return slug;
}

async function submitSave(overwrite) {
  if (saveState.inFlight) return;
  const title = document.getElementById('save-title').value.trim();
  const save_as_id = document.getElementById('save-id').value.trim();
  if (!title) { _setSaveStatus('error', 'Please enter a title.'); return; }
  if (!save_as_id) { _setSaveStatus('error', 'Please enter a scenario id.'); return; }

  // Overwrite-source path: the checkbox implies both overwrite=true and
  // the user's intent to replace the current scenario. Route through an
  // explicit confirm modal before actually sending the request.
  if (saveState.overwriteSource && !overwrite) {
    document.getElementById('modal-save-overwrite-confirm').classList.add('visible');
    return;
  }

  saveState.inFlight = true;
  _setSaveSubmitEnabled(false);
  _setSaveStatus('info', 'Saving…');
  try {
    const body = await apiSaveScenario({
      source_id: state.scenario_id,
      diff_ops: state.diff_ops,
      save_as_id,
      title,
      overwrite,
    });
    // Success: refresh switcher, pivot to the saved scenario.
    state.scenarios = await apiListScenarios();
    populateScenarioSelect();
    closeModal('modal-save');
    closeModal('modal-save-collision');
    await loadScenario(body.id);
  } catch (e) {
    if (e.status === 409) {
      closeModal('modal-save');
      _showSaveCollisionModal(save_as_id);
    } else {
      _setSaveStatus('error', e.message || 'Save failed.');
    }
  } finally {
    saveState.inFlight = false;
    _setSaveSubmitEnabled(true);
  }
}

function _showSaveCollisionModal(save_as_id) {
  document.getElementById('save-collision-body').textContent =
    `A scenario with id "${save_as_id}" already exists. Overwrite it, or go back and rename?`;
  document.getElementById('modal-save-collision').classList.add('visible');
}

function onSaveCollisionOverwrite() {
  closeModal('modal-save-collision');
  submitSave(true);
}

function onSaveCollisionRename() {
  closeModal('modal-save-collision');
  // Re-open the save modal; inputs still hold the user's values.
  document.getElementById('modal-save').classList.add('visible');
  // Mark id as edited so auto-slug doesn't clobber the user's choice.
  saveState.idEdited = true;
  document.getElementById('save-id').focus();
  document.getElementById('save-id').select();
}

function onSaveOverwriteConfirm() {
  // User confirmed via the dedicated overwrite-current-scenario modal.
  // Close the confirm, then re-enter submitSave with overwrite=true so
  // the request goes through without the double-check.
  closeModal('modal-save-overwrite-confirm');
  submitSave(true);
}

function onSaveCollisionCancel() {
  // Reopen the save modal with a status hint so the user can see why the
  // attempt didn't go through, rather than ending up on an unchanged main
  // screen with no feedback.
  closeModal('modal-save-collision');
  document.getElementById('modal-save').classList.add('visible');
  saveState.idEdited = true;
  _setSaveStatus('info', 'Save canceled. Pick a different id, or close this dialog to abandon.');
}

function _setSaveStatus(kind, msg) {
  const el = document.getElementById('save-status');
  el.className = 'save-status' + (kind ? ' save-status-' + kind : '');
  el.textContent = msg || '';
}

function _setSaveSubmitEnabled(enabled) {
  const btn = document.getElementById('save-submit-btn');
  if (btn) btn.disabled = !enabled;
}

function _setSaveSubmitLabel(label) {
  const btn = document.getElementById('save-submit-btn');
  if (btn) btn.textContent = label;
}

// --- Show ASPIC- modal -------------------------------------------------

function openAspicModal() {
  const scn = state.bundle?.scenario;
  if (!scn) return;
  const text = buildAspicText(scn);
  const pre = document.getElementById('aspic-pre');
  pre.dataset.raw = text;
  pre.innerHTML = text.split('\n').map(highlightAspicLine).join('\n');
  const btn = document.getElementById('aspic-copy-btn');
  btn.textContent = 'Copy';
  btn.classList.remove('copied');
  document.getElementById('modal-aspic').classList.add('visible');
}

function buildAspicText(scn) {
  const lines = [];
  if (scn.title) lines.push(`# ${scn.title}`);

  // Glossary of propositions (positive names, plus any negated forms that
  // actually appear in rule premises or conclusions, excluding rule-name
  // literals like `-box_softens`).
  const glossary = buildAspicGlossary(scn);
  if (glossary.length > 0) {
    lines.push('');
    lines.push('# Glossary of propositions:');
    for (const { id, text } of glossary) lines.push(glossaryLine(id, text));
  }

  // Facts, grouped by category.
  const factIds = Object.keys(scn.facts || {});
  if (factIds.length > 0) {
    lines.push('', '# Facts');
    for (const [cat, ids] of groupIdsByCategory(factIds, id => scn.facts[id])) {
      lines.push(`# ${cat}`);
      for (const id of ids) lines.push(`-> ${id}`);
    }
  }

  // Assumptions, grouped by category.
  const assumptionIds = Object.keys(scn.assumptions || {});
  if (assumptionIds.length > 0) {
    lines.push('', '# Assumptions');
    for (const [cat, ids] of groupIdsByCategory(assumptionIds, id => scn.assumptions[id])) {
      lines.push(`# ${cat}`);
      for (const id of ids) {
        const data = scn.assumptions[id];
        const mark = (data.active === false) ? '# [suspended] ' : '';
        lines.push(`${mark}=> ${id} [${id}]`);
      }
    }
  }

  // Rules, organised by block, then by category within block.
  const byBlock = new Map();
  for (const [id, r] of Object.entries(scn.rules || {})) {
    const block = r.block ?? 1;
    if (!byBlock.has(block)) byBlock.set(block, []);
    byBlock.get(block).push({ id, data: r });
  }
  const sortedBlocks = [...byBlock.keys()].sort((a, b) => a - b);
  for (const block of sortedBlocks) {
    const label = sortedBlocks.length > 1
      ? (block === sortedBlocks[0] ? ' (weakest)'
         : block === sortedBlocks[sortedBlocks.length - 1] ? ' (strongest)'
         : '')
      : '';
    lines.push('', `# Block ${block}${label}`);
    const byCat = groupIdsByCategory(
      byBlock.get(block).map(it => it.id),
      id => byBlock.get(block).find(it => it.id === id).data
    );
    for (const [cat, ids] of byCat) {
      lines.push(`# ${cat}`);
      for (const id of ids) {
        const { data } = byBlock.get(block).find(it => it.id === id);
        const arrow = data.type === 'strict' ? '->' : '=>';
        const premises = (data.premises || []).join(', ');
        const body = premises ? `${premises} ${arrow} ${data.conclusion}` : `${arrow} ${data.conclusion}`;
        const mark = (data.active === false) ? '# [suspended] ' : '';
        lines.push(`${mark}${body} [${id}]`);
      }
    }
  }

  return lines.join('\n').replace(/\n+$/, '');
}

function groupIdsByCategory(ids, getData) {
  const groups = new Map();
  for (const id of ids) {
    const cat = getData(id).category || 'uncategorized';
    if (!groups.has(cat)) groups.set(cat, []);
    groups.get(cat).push(id);
  }
  return groups;
}

function glossaryLine(id, text) {
  // Right-align so the alphanumeric part of the identifier sits at the same
  // column regardless of a leading '-'. Produces:
  //   #   x = "..."
  //   #  -x = "..."
  const indent = id.startsWith('-') ? ' ' : '  ';
  const escaped = text.replace(/"/g, '\\"');
  return `# ${indent}${id} = "${escaped}"`;
}

function buildAspicGlossary(scn) {
  // Collect which negated literals actually occur in rule premises /
  // conclusions (excluding rule-name undercut literals like `-box_softens`).
  const ruleIds = new Set(Object.keys(scn.rules || {}));
  const negatedUsed = new Set();
  for (const rule of Object.values(scn.rules || {})) {
    for (const p of (rule.premises || [])) {
      if (p.startsWith('-') && !ruleIds.has(p.slice(1))) negatedUsed.add(p);
    }
    const c = rule.conclusion;
    if (c && c.startsWith('-') && !ruleIds.has(c.slice(1))) negatedUsed.add(c);
  }

  const entries = [];
  const seen = new Set();
  const push = (id, text) => {
    if (seen.has(id) || !text) return;
    entries.push({ id, text });
    seen.add(id);
  };
  const addWithNeg = (id, source) => {
    push(id, source.description);
    const neg = `-${id}`;
    if (negatedUsed.has(neg)) {
      push(neg, source.negated_description || `not ${source.description}`);
    }
  };

  for (const [id, f] of Object.entries(scn.facts || {})) addWithNeg(id, f);
  for (const [id, a] of Object.entries(scn.assumptions || {})) addWithNeg(id, a);
  for (const [id, p] of Object.entries(scn.propositions || {})) addWithNeg(id, p);
  for (const [id, c] of Object.entries(scn.conclusions || {})) addWithNeg(id, c);

  return entries;
}

function highlightAspicLine(line) {
  const esc = line
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  if (esc.trimStart().startsWith('#')) {
    return `<span class="aspic-comment">${esc}</span>`;
  }
  // Stash arrows under sentinels so the negation regex doesn't eat the '-' in '->'.
  let out = esc
    .replace(/=&gt;/g, '\x01DEF\x01')
    .replace(/-&gt;/g, '\x01STR\x01');
  out = out
    .replace(/\[([A-Za-z_][A-Za-z0-9_]*)\]/g, '<span class="aspic-name">[$1]</span>')
    .replace(/(^|[\s,])(-)([A-Za-z_])/g, '$1<span class="aspic-negation">$2</span>$3')
    .replace(/\x01DEF\x01/g, '<span class="aspic-arrow">=&gt;</span>')
    .replace(/\x01STR\x01/g, '<span class="aspic-arrow">-&gt;</span>');
  return out;
}

async function copyAspicToClipboard() {
  const pre = document.getElementById('aspic-pre');
  const text = pre.dataset.raw || pre.textContent;
  const btn = document.getElementById('aspic-copy-btn');
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => {
      btn.textContent = 'Copy';
      btn.classList.remove('copied');
    }, 1500);
  } catch (e) {
    btn.textContent = 'Copy failed';
    setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
  }
}

// --- Argument picker -------------------------------------------------

function renderArgumentPicker() {
  const body = document.getElementById('game-modal-body');
  const candidates = getCandidateRootArguments(gameConclusionId);
  const status = state.bundle.af.labels_by_proposition?.[gameConclusionId];

  if (candidates.length === 0) {
    body.innerHTML = `<div class="placeholder-msg">No derivation available for this conclusion.</div>`;
    return;
  }

  // Detect the rejected-via-negation case: the candidates are "in" args
  // for -c rather than "out" args for c. The main explanation view for
  // this case skips the picker entirely; this branch only fires if that
  // upstream detection misses (e.g. multiple distinct in-args for -c).
  const rejectedViaNeg = status === 'rejected'
    && candidates.every(a => a.conclusion === '-' + gameConclusionId && a.label === 'in');

  const headerByStatus = {
    accepted: 'This conclusion is accepted. Pick a derivation to see why.',
    rejected: rejectedViaNeg
      ? 'This conclusion is rejected. No argument supports it directly; the warranted argument for its negation below explains why.'
      : 'This conclusion is rejected. Pick an argument to see what defeated it.',
    undecided: 'This conclusion is undecided. Pick a derivation to trace the argument chain.',
  };

  body.innerHTML = `
    <div class="game-picker-header">${escapeHtml(headerByStatus[status] || 'Select an argument to explore:')}</div>
    <div class="game-picker-list">
      ${candidates.map(renderPickerCard).join('')}
    </div>
  `;

  for (const card of body.querySelectorAll('.game-picker-card')) {
    card.addEventListener('click', () => startGameWithRoot(card.dataset.argId));
  }
}

function renderPickerCard(arg) {
  const rule = state.bundle.scenario.rules?.[arg.top_rule];
  let ruleLine;
  if (rule) {
    ruleLine = renderRuleText(arg.top_rule, rule);
  } else {
    // Fallback for bodyless rules (fact / assumption): render the top rule id only.
    ruleLine = `<span class="inline-id">[${escapeHtml(arg.top_rule)}]</span> ${escapeHtml(arg.conclusion_nl)}`;
  }
  return `<div class="game-picker-card" data-arg-id="${escapeAttr(arg.id)}">
    <div class="game-picker-rule">${ruleLine}</div>
    <div class="game-picker-meta">Derivation ${escapeHtml(arg.id)}</div>
  </div>`;
}

function renderRuleText(ruleId, rule) {
  const premises = (rule.premises || []).map(p => escapeHtml(renderLiteral(p))).join(' <span class="kw">and</span> ');
  const conclusion = escapeHtml(renderLiteral(rule.conclusion));
  const connective = rule.type === 'strict' ? 'necessarily' : 'normally';
  const idTag = `<span class="inline-id">[${escapeHtml(ruleId)}]</span>`;
  if (!premises) {
    return `<span class="kw">${connective}</span> ${conclusion} ${idTag}`;
  }
  return `<span class="kw">If</span> ${premises} <span class="kw">then</span> <span class="kw">${connective}</span> ${conclusion} ${idTag}`;
}

// --- Tree view -------------------------------------------------------

function startGameWithRoot(argId) {
  gameNodes = {};
  gameNodeCounter = 0;
  const root = makeGameNode('htb', argId, null);
  gameRootId = root.id;
  gameFocusId = root.id;
  renderGame();
}

function renderGame() {
  const body = document.getElementById('game-modal-body');
  const rootNode = gameNodes[gameRootId];
  if (!rootNode) { renderArgumentPicker(); return; }

  if (gameExplanationOnly) {
    body.innerHTML = `
      ${renderRejectionRationale(rootNode)}
      <div class="game-tree">${renderGameNode(rootNode)}</div>
    `;
    bindGameTreeHandlers(body);
    return;
  }

  body.innerHTML = `
    <div class="game-toolbar">
      <button class="btn btn-small" id="game-back-btn">← Back to arguments</button>
    </div>
    <div class="game-main">
      <div class="game-main-left">
        <div class="game-tree">${renderGameNode(rootNode)}</div>
        <div class="game-moves" id="game-moves"></div>
      </div>
    </div>
  `;
  document.getElementById('game-back-btn').addEventListener('click', renderArgumentPicker);
  bindGameTreeHandlers(body);
  renderGameMoves();
}

// Compact SVG sidebar showing the whole game tree as dots + connector
// lines. One row per node (depth-first traversal), x = depth, y = row.
// Fill colour mirrors the main tree's bar/badge palette; current focus
// gets a heavier accent stroke. Clicking a dot refocuses.
// Hidden when the tree is trivial (0 or 1 node) since there's nothing
// to map.
function renderMinimap() {
  if (!gameRootId || !gameNodes[gameRootId]) return '';

  const STEP_Y = 12;
  const INDENT_X = 10;
  const PAD = 8;
  const R = 4;

  // Depth-first traversal, honouring collapsed nodes so the minimap
  // matches what's actually visible in the main tree.
  const rows = [];
  (function walk(nodeId, depth) {
    const n = gameNodes[nodeId];
    if (!n) return;
    rows.push({ id: nodeId, depth });
    if (n.collapsed) return;
    for (const cid of n.children) walk(cid, depth + 1);
  })(gameRootId, 0);

  if (rows.length <= 1) return '';

  const maxDepth = Math.max(...rows.map(r => r.depth));
  const width = PAD * 2 + maxDepth * INDENT_X + R * 2;
  const height = PAD * 2 + (rows.length - 1) * STEP_Y + R * 2;

  const pos = {};
  rows.forEach((r, i) => {
    pos[r.id] = { x: PAD + R + r.depth * INDENT_X, y: PAD + R + i * STEP_Y };
  });

  let edges = '';
  for (const r of rows) {
    const node = gameNodes[r.id];
    if (!node.parentId || !pos[node.parentId]) continue;
    const p = pos[node.parentId];
    const c = pos[r.id];
    // L-shape: down from parent centre, then across to child centre.
    edges += `<path d="M ${p.x} ${p.y + R} V ${c.y} H ${c.x - R}" stroke="#b0b6c0" stroke-width="1" fill="none"/>`;
  }

  const fillFor = (node) => {
    if (node.type === 'cycle') return '#d4b857';
    if (node.resolution === 'conceded' || node.resolution === 'uncontested') return '#5aa36f';
    if (node.resolution === 'defeated' || node.resolution === 'retracted') return '#c45a5a';
    if (node.resolution === 'undecided') return '#c4a850';
    if (node.type === 'htb') return '#5a7a8a';
    if (node.type === 'cb')  return '#8a7050';
    return '#b0b6c0';
  };

  let dots = '';
  for (const r of rows) {
    const node = gameNodes[r.id];
    const p = pos[r.id];
    const isFocus = r.id === gameFocusId;
    const stroke = isFocus ? '#2d5aa0' : 'rgba(0,0,0,0.2)';
    const sw = isFocus ? 2 : 0.5;
    const radius = isFocus ? R + 1 : R;
    dots += `<circle cx="${p.x}" cy="${p.y}" r="${radius}" fill="${fillFor(node)}" stroke="${stroke}" stroke-width="${sw}" data-minimap-focus-id="${escapeAttr(r.id)}" style="cursor:pointer"></circle>`;
  }

  return `<div class="game-minimap" aria-label="Game tree minimap">
    <div class="game-minimap-header">Tree</div>
    <svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">
      ${edges}
      ${dots}
    </svg>
  </div>`;
}

// Auto-resolve any off-path node that is stuck waiting on Continue --
// no canonical moves available, no resolution yet. Mirrors what the
// Continue button does: HTB → conceded, CB → uncontested, then
// propagate. Running this on focus-change prevents two downstream
// problems: (a) the stuck-on-"Resolving…" panel when the user backs up
// past an unresolved leaf, and (b) subtrees never registering as
// "fully resolved" for auto-collapse, because the leaf was unresolved.
function autoResolveStuckOffPath() {
  const ancestors = new Set();
  let cur = gameFocusId;
  while (cur) {
    ancestors.add(cur);
    cur = gameNodes[cur]?.parentId;
  }
  for (const node of Object.values(gameNodes)) {
    if (ancestors.has(node.id)) continue;
    if (node.resolution) continue;
    if (node.type === 'cycle') continue;
    const hasMoves = (node.type === 'htb' && getGameCBs(node).length > 0) ||
                     (node.type === 'cb'  && getGameHTBs(node).length > 0);
    if (hasMoves) continue;  // still has something to play; not stuck
    // Mirrors resolveGameUncontested / resolveGameUndefended.
    node.resolution = node.type === 'htb' ? 'conceded' : 'uncontested';
    propagateGame(node);
  }
}

// Combined helper called on every focus change. Currently only
// auto-resolves stuck off-path leaves so status badges appear and
// parents can be considered resolved for propagation purposes.
// Auto-collapse is intentionally NOT applied -- the user controls
// collapse manually via the per-node caret.
function tidyOffPath() {
  autoResolveStuckOffPath();
}

function toggleGameCollapse(nodeId) {
  const node = gameNodes[nodeId];
  if (!node) return;
  node.collapsed = !node.collapsed;
  renderGame();
}

// Prose banner shown above the root argument card when the conclusion is
// rejected because a strict rule on -c stands unchallenged. The argument
// card below carries the rule's natural-language rendering, so the banner
// only says *why* this is a no-game case, not the rule's full text.
function renderRejectionRationale(rootNode) {
  const arg = getArgumentById(rootNode.argId);
  if (!arg) return '';
  const rule = state.bundle.scenario.rules?.[arg.top_rule];
  const isStrictTop = rule && rule.type === 'strict';

  const ruleRef = `<span class="inline-id">[${escapeHtml(arg.top_rule)}]</span>`;
  const detail = isStrictTop
    ? `The strict rule ${ruleRef} derives its negation (shown below). Strict rules can't be challenged, so this stands.`
    : `The argument below for its negation is warranted, and nothing contests it.`;

  return `<div class="game-rationale">
    <p>This conclusion is <strong>rejected</strong>. No argument supports it directly, so there is no game trace.</p>
    <p>${detail}</p>
  </div>`;
}

function renderGameNode(node) {
  const arg = getArgumentById(node.argId);
  if (!arg) return '';
  const isFocus = (node.id === gameFocusId);
  const activeClass = isFocus ? ' game-node-active' : '';
  const resClass = node.resolution ? ` game-node-${node.resolution}` : '';

  // Cycle leaf
  if (node.type === 'cycle') {
    return `<div class="game-node game-node-cycle${resClass}">
      <div class="game-node-inner">
        <div class="game-node-topbar game-bar-cycle">↺ Cycle. Returns to an argument above.</div>
        <div class="game-node-content">
          <div class="game-node-claim">${escapeHtml(arg.conclusion_nl)}</div>
          <div class="game-node-detail">This creates a circular dependency. All arguments in the cycle are undecided.</div>
        </div>
        <div class="game-node-status"><span class="badge badge-undecided">Undecided</span></div>
      </div>
    </div>`;
  }

  let moveLabel, barClass;
  if (node.type === 'htb') { moveLabel = 'Has to be the case:'; barClass = 'game-bar-htb'; }
  else { moveLabel = 'Can be the case that:'; barClass = 'game-bar-cb'; }

  // Inline collapse caret. Always shown when this node has children so
  // the user can fold any subtree they want out of the way -- whether
  // or not it's fully resolved. Stops click propagation so toggling
  // doesn't also shift focus to this node.
  let caretBtn = '';
  if (node.children.length > 0) {
    const label = node.collapsed
      ? `▸ Expand (${countDescendants(node)} hidden)`
      : `▾ Collapse`;
    caretBtn = `<button class="game-topbar-caret" data-toggle-collapse-id="${escapeAttr(node.id)}">${label}</button>`;
  }

  let resBadge = '', resReason = '';
  if (node.resolution === 'conceded')     { resBadge = '<span class="badge badge-accepted">Accepted</span>'; resReason = '<div class="game-node-res-reason">Conceded. No challenges remain.</div>'; }
  else if (node.resolution === 'defeated')   { resBadge = '<span class="badge badge-rejected">Rejected</span>'; resReason = '<div class="game-node-res-reason">Rejected. An undefended challenge stands.</div>'; }
  else if (node.resolution === 'retracted')  { resBadge = '<span class="badge badge-rejected">Rejected</span>'; resReason = '<div class="game-node-res-reason">Retracted. Defense was accepted.</div>'; }
  else if (node.resolution === 'uncontested'){ resBadge = '<span class="badge badge-accepted">Accepted</span>'; resReason = '<div class="game-node-res-reason">Challenge stands. No defense available.</div>'; }
  else if (node.resolution === 'undecided')  { resBadge = '<span class="badge badge-undecided">Undecided</span>'; resReason = '<div class="game-node-res-reason">Undecided. Circular dependency.</div>'; }

  const hasMoves = (node.type === 'htb' && getGameCBs(node).length > 0) ||
                   (node.type === 'cb'  && getGameHTBs(node).length > 0);
  const clickable = !node.resolution || hasMoves;
  const clickAttr = clickable ? ` data-focus-id="${escapeAttr(node.id)}"` : '';
  const cursor = clickable ? ' style="cursor:pointer"' : '';

  const ruleText = (() => {
    const rule = state.bundle.scenario.rules?.[arg.top_rule];
    return rule ? renderRuleText(arg.top_rule, rule)
                : `<span class="inline-id">[${escapeHtml(arg.top_rule)}]</span> (fact/assumption)`;
  })();

  // Show supports for sub-arguments that could be attacked at all in the
  // AF -- not just those already attacked in the current game tree. This
  // keeps the set of visible supports stable independent of which branch
  // the user has explored, and matches what the moves panel offers: any
  // sub-arg whose canonical key has at least one incoming attack edge
  // somewhere in the AF is dialectically relevant.
  //
  // Explanation-only mode (rejected-via-strict) and accepted conclusions
  // should show the derivation itself, even when there are no challenges
  // to play through. Other statuses keep the dialectically relevant subset.
  const showFullDerivation = gameExplanationOnly
    || state.bundle.af.labels_by_proposition?.[gameConclusionId] === 'accepted';
  const attackableIds = showFullDerivation
    ? new Set([arg.id, ...(arg.sub_arguments || [])])
    : getAttackableSubArgIds(node);
  const supports = attackableIds.size > 0 ? generateSupports(arg, attackableIds) : [];
  let supportHtml = '';
  if (supports.length > 0) {
    const sid = 'support-' + node.id;
    supportHtml = `<div class="game-supports">
      <div class="game-supports-toggle" data-supports-id="${sid}">
        <span class="game-supports-arrow" id="arrow-${sid}">▶</span> Premises and subarguments:
      </div>
      <div class="game-supports-list" id="${sid}" style="display:none">
        ${supports.map(s => `<div class="game-support-card">
          <div class="game-support-label">${escapeHtml(s.label)}</div>
          <div class="game-support-rules">${s.rules}</div>
          ${s.facts ? `<div class="game-support-facts">${s.facts}</div>` : ''}
        </div>`).join('')}
      </div>
    </div>`;
  }

  let attackInfoHtml = '';
  if (node.type === 'cb' && node.parentId) {
    attackInfoHtml = renderAttackInfo(node);
  }

  let html = `<div class="game-node${resClass}${activeClass}" id="gnode-${node.id}">
    <div class="game-node-inner"${clickAttr}${cursor}>
      <div class="game-node-topbar ${barClass}"><span>${moveLabel}</span>${caretBtn}</div>
      <div class="game-node-content">
        ${attackInfoHtml}
        <div class="game-node-claim">${escapeHtml(arg.conclusion_nl)}</div>
        <div class="game-node-rule">${ruleText}</div>
      </div>
      <div class="game-node-status">${resBadge}</div>
    </div>
    ${resReason}
    ${supportHtml}`;

  if (node.children.length > 0 && !node.collapsed) {
    html += '<div class="game-children">';
    for (const cid of node.children) html += renderGameNode(gameNodes[cid]);
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function isSubtreeResolved(node) {
  if (!node) return false;
  if (node.type === 'cycle') return true;
  if (!node.resolution) return false;
  return node.children.every(cid => isSubtreeResolved(gameNodes[cid]));
}

function countDescendants(node) {
  let n = 0;
  for (const cid of node.children) {
    const c = gameNodes[cid];
    if (!c) continue;
    n += 1 + countDescendants(c);
  }
  return n;
}

function renderAttackInfo(cbNode) {
  const parent = gameNodes[cbNode.parentId];
  if (!parent) return '';
  const edge = (state.bundle.af.attacks || []).find(a => a.from === cbNode.argId && a.to === parent.argId);
  if (!edge) return '';
  const attacker = getArgumentById(cbNode.argId);
  const parentArg = getArgumentById(parent.argId);
  let desc;
  if (edge.type === 'undercut') {
    const ruleId = (attacker?.conclusion || '').replace(/^-/, '');
    desc = `undercuts rule [${escapeHtml(ruleId)}]`;
  } else {
    desc = 'directly rebuts the conclusion';
  }
  const target = parentArg ? escapeHtml(parentArg.conclusion_nl) : '';
  return `<div class="game-attack-info">Attacks: ${target} <span class="game-attack-type">(${desc})</span></div>`;
}

// Identify the sub-arguments of a node's arg (including the arg itself)
// that have at least one incoming attack edge somewhere in the AF. A
// match against any Cartesian variant of the sub-arg's canonical key
// counts -- we want canonical-level dialectical relevance, not
// variant-level. This is what drives the visibility of the "Supporting
// arguments" panel: we surface every premise whose chain contains an
// attackable node, regardless of whether the user has played an attack
// against it yet.
function getAttackableSubArgIds(node) {
  const arg = getArgumentById(node.argId);
  const out = new Set();
  if (!arg) return out;
  const allArgs = state.bundle.af.arguments || [];
  const attacks = state.bundle.af.attacks || [];
  const idsByKey = new Map();
  for (const a of allArgs) {
    const k = canonicalKey(a);
    if (!idsByKey.has(k)) idsByKey.set(k, new Set());
    idsByKey.get(k).add(a.id);
  }
  const ownSubs = [arg.id, ...(arg.sub_arguments || [])];
  for (const sid of ownSubs) {
    const s = getArgumentById(sid);
    if (!s) continue;
    const variantIds = idsByKey.get(canonicalKey(s)) || new Set([sid]);
    if (attacks.some(e => variantIds.has(e.to))) out.add(sid);
  }
  return out;
}

// True iff sub-arg with id `subArgId`, or any of its transitive
// sub-arguments, is in the attackable set.
function isRelevantSupport(subArgId, attackableIds) {
  if (attackableIds.has(subArgId)) return true;
  const s = getArgumentById(subArgId);
  if (!s) return false;
  for (const id of s.sub_arguments || []) {
    if (attackableIds.has(id)) return true;
  }
  return false;
}

function generateSupports(arg, attackableIds) {
  const rules = state.bundle.scenario.rules || {};
  const topRule = rules[arg.top_rule];
  if (!topRule || !topRule.premises || topRule.premises.length === 0) return [];

  return topRule.premises.map((premLit, i) => {
    const subArgId = arg.premises?.[i];
    if (!subArgId) return null;
    if (!isRelevantSupport(subArgId, attackableIds)) return null;
    const subArg = getArgumentById(subArgId);

    let rulesHtml = '';
    if (subArg) {
      const subRule = rules[subArg.top_rule];
      rulesHtml = subRule
        ? `<div class="support-rule-line">${renderRuleText(subArg.top_rule, subRule)}</div>`
        : `<div class="support-rule-line"><span class="inline-id">[${escapeHtml(subArg.top_rule)}]</span> ${escapeHtml(subArg.conclusion_nl)}</div>`;
    }

    let factsHtml = '';
    if (subArg) {
      const factArgs = [subArg, ...(subArg.sub_arguments || []).map(getArgumentById).filter(Boolean)]
        .filter(a => a.is_fact);
      // Dedupe by top_rule id (facts appear once per argument chain).
      const seen = new Set();
      const lines = [];
      for (const fa of factArgs) {
        if (seen.has(fa.top_rule)) continue;
        seen.add(fa.top_rule);
        lines.push(`<span class="inline-id">[${escapeHtml(fa.top_rule)}]</span> ${escapeHtml(fa.conclusion_nl)} <span class="fact-tag">fact</span>`);
      }
      factsHtml = lines.join('<br>');
    }

    return { label: renderLiteral(premLit), rules: rulesHtml, facts: factsHtml };
  }).filter(Boolean);
}

// --- Moves panel -----------------------------------------------------

function renderGameMoves() {
  const panel = document.getElementById('game-moves');
  if (!panel) return;
  if (!gameFocusId || !gameNodes[gameFocusId]) {
    panel.innerHTML = '<div class="game-moves-header">Exploration complete</div><div class="game-moves-empty">All branches have been explored.</div>';
    return;
  }

  _renderGuard++;
  if (_renderGuard > 5) {
    _renderGuard = 0;
    panel.innerHTML = '<div class="game-moves-header">Exploration complete</div><div class="game-moves-empty">All branches have been explored.</div>';
    return;
  }

  const node = gameNodes[gameFocusId];

  if (node.resolution) {
    const hasMoves = (node.type === 'htb' && getGameCBs(node).length > 0) ||
                     (node.type === 'cb'  && getGameHTBs(node).length > 0);
    if (!hasMoves) {
      const nextId = findGameOpen(gameNodes[gameRootId]);
      if (nextId && nextId !== gameFocusId) { gameFocusId = nextId; tidyOffPath(); renderGame(); return; }
      panel.innerHTML = '<div class="game-moves-header">Exploration complete</div><div class="game-moves-empty">All branches have been explored.</div>';
      _renderGuard = 0;
      return;
    }
  }
  _renderGuard = 0;

  const byId = (argId) => {
    const a = getArgumentById(argId);
    // Disambiguate: multiple arguments can share a conclusion (different
    // Cartesian-product derivations), so append the arg id as a quiet suffix.
    return a
      ? `${escapeHtml(a.conclusion_nl)} <span class="inline-id">[${escapeHtml(argId)}]</span>`
      : escapeHtml(argId);
  };

  // Backtracking affordance: list every still-open node in the tree that
  // isn't the current focus. Appended to EVERY non-resolving leaf (cycle,
  // uncontested, undefended) and to the main moves list, so the user can
  // jump to any open branch at any depth without having to click through
  // Continue first.
  const otherOpenHtml = () => {
    const allOpen = findAllGameOpen(gameNodes[gameRootId], []);
    const otherOpen = allOpen.filter(n => n.id !== gameFocusId);
    if (otherOpen.length === 0) return '';
    return `<div class="game-other-branches">
      <div class="game-moves-header">Other open branches:</div>
      ${otherOpen.map(n => {
        const action = n.type === 'htb' ? 'Challenge' : 'Defend';
        return `<div class="game-move-card game-move-other" data-focus-id="${escapeAttr(n.id)}">
          <div class="game-move-label"><em>${action}:</em> ${byId(n.argId)}</div>
        </div>`;
      }).join('')}
    </div>`;
  };

  if (node.type === 'htb') {
    const cbs = getGameCBs(node);
    if (cbs.length === 0) {
      const cyc = getGameCycleAttackers(node);
      if (cyc.length > 0) {
        panel.innerHTML = '<div class="game-moves-header">Cycle detected</div><div class="game-moves-empty">The following challenge creates a cycle.</div>' +
          cyc.map(a => `<div class="game-move-card" data-cycle-cb="${escapeAttr(a)}" data-parent="${escapeAttr(node.id)}">
            <div class="game-move-type game-bar-cycle">↺</div>
            <div class="game-move-label">${byId(a)} <em class="game-node-dim">(cycle)</em></div>
          </div>`).join('') +
          otherOpenHtml();
        wireMoveCardHandlers(panel);
        return;
      }
      const hasUnresolved = node.children.some(cid => !gameNodes[cid].resolution);
      if (hasUnresolved) {
        const nextId = findGameOpen(node);
        if (nextId && nextId !== node.id) { gameFocusId = nextId; tidyOffPath(); renderGame(); return; }
        // Fallback: unresolved descendants exist but none has moves available --
        // user skipped Continue somewhere. Jump focus to one so they can finish
        // the resolution cascade rather than landing on a dead "Resolving…" panel.
        const stuckId = findStuckUnresolved(node);
        if (stuckId && stuckId !== node.id) { gameFocusId = stuckId; tidyOffPath(); renderGame(); return; }
        panel.innerHTML = '<div class="game-moves-header">Resolving…</div>';
        return;
      }
      const prefNote = preferenceDisclosureFor(node.argId);
      panel.innerHTML = '<div class="game-moves-header">No challenges available</div>' +
        '<div class="game-moves-empty">This claim has no remaining challenges. It is accepted.</div>' +
        prefNote +
        otherOpenHtml() +
        `<div style="margin-top:.5rem"><button class="btn btn-small" data-resolve="uncontested" data-node="${escapeAttr(node.id)}">Continue</button></div>`;
      wireMoveCardHandlers(panel);
      return;
    }
    panel.innerHTML = '<div class="game-moves-header">Challenge this claim:</div>' +
      cbs.map(a => `<div class="game-move-card" data-move="cb" data-arg="${escapeAttr(a)}" data-parent="${escapeAttr(node.id)}">
        <div class="game-move-type game-bar-cb">Can be the case that:</div>
        <div class="game-move-label">${byId(a)}</div>
      </div>`).join('');
  } else {
    const htbs = getGameHTBs(node);
    if (htbs.length === 0) {
      const cyc = getGameCycleAttackers(node);
      if (cyc.length > 0) {
        panel.innerHTML = '<div class="game-moves-header">Cycle detected</div><div class="game-moves-empty">The following defense creates a cycle.</div>' +
          cyc.map(a => `<div class="game-move-card" data-cycle-htb="${escapeAttr(a)}" data-parent="${escapeAttr(node.id)}">
            <div class="game-move-type game-bar-cycle">↺</div>
            <div class="game-move-label">${byId(a)} <em class="game-node-dim">(cycle)</em></div>
          </div>`).join('') +
          otherOpenHtml();
        wireMoveCardHandlers(panel);
        return;
      }
      const hasUnresolved = node.children.some(cid => !gameNodes[cid].resolution);
      if (hasUnresolved) {
        const nextId = findGameOpen(node);
        if (nextId && nextId !== node.id) { gameFocusId = nextId; tidyOffPath(); renderGame(); return; }
        // Fallback: unresolved descendants exist but none has moves available --
        // user skipped Continue somewhere. Jump focus to one so they can finish
        // the resolution cascade rather than landing on a dead "Resolving…" panel.
        const stuckId = findStuckUnresolved(node);
        if (stuckId && stuckId !== node.id) { gameFocusId = stuckId; tidyOffPath(); renderGame(); return; }
        panel.innerHTML = '<div class="game-moves-header">Resolving…</div>';
        return;
      }
      const prefNote = preferenceDisclosureFor(node.argId);
      panel.innerHTML = '<div class="game-moves-header">No defense available</div>' +
        '<div class="game-moves-empty">This challenge cannot be defended against. It stands.</div>' +
        prefNote +
        otherOpenHtml() +
        `<div style="margin-top:.5rem"><button class="btn btn-small" data-resolve="undefended" data-node="${escapeAttr(node.id)}">Continue</button></div>`;
      wireMoveCardHandlers(panel);
      return;
    }
    panel.innerHTML = '<div class="game-moves-header">Defend against this challenge:</div>' +
      htbs.map(a => `<div class="game-move-card" data-move="htb" data-arg="${escapeAttr(a)}" data-parent="${escapeAttr(node.id)}">
        <div class="game-move-type game-bar-htb">Has to be the case:</div>
        <div class="game-move-label">${byId(a)}</div>
      </div>`).join('');
  }

  panel.innerHTML += otherOpenHtml();
  wireMoveCardHandlers(panel);
}

// Moves-panel handlers. Scoped to `root` (the moves panel, or the modal
// body in explanation-only mode) so re-running on a panel repaint won't
// re-attach duplicate handlers on the game tree.
function wireMoveCardHandlers(root) {
  for (const el of root.querySelectorAll('[data-move]')) {
    el.addEventListener('click', () => playGameMove(el.dataset.move, el.dataset.arg, el.dataset.parent));
  }
  for (const el of root.querySelectorAll('[data-cycle-cb]')) {
    el.addEventListener('click', () => playGameCycleMove('cb', el.dataset.cycleCb, el.dataset.parent));
  }
  for (const el of root.querySelectorAll('[data-cycle-htb]')) {
    el.addEventListener('click', () => playGameCycleMove('htb', el.dataset.cycleHtb, el.dataset.parent));
  }
  for (const el of root.querySelectorAll('.game-move-card[data-focus-id]')) {
    el.addEventListener('click', () => setGameFocus(el.dataset.focusId));
  }
  for (const el of root.querySelectorAll('[data-resolve]')) {
    el.addEventListener('click', () => {
      const node = gameNodes[el.dataset.node];
      if (el.dataset.resolve === 'uncontested') resolveGameUncontested(node);
      else if (el.dataset.resolve === 'undefended') resolveGameUndefended(node);
    });
  }
}

// Game-tree handlers (support-toggle, collapse-caret, minimap, and
// focus-on-node-click). Bound once per renderGame() so they survive
// even when the moves panel early-returns with "Exploration complete"
// -- previously those early returns skipped wireMoveCardHandlers and
// left the tree without click handlers.
function bindGameTreeHandlers(root) {
  for (const el of root.querySelectorAll('[data-supports-id]')) {
    el.addEventListener('click', e => { e.stopPropagation(); toggleSupports(el.dataset.supportsId); });
  }
  for (const el of root.querySelectorAll('[data-toggle-collapse-id]')) {
    el.addEventListener('click', e => { e.stopPropagation(); toggleGameCollapse(el.dataset.toggleCollapseId); });
  }
  for (const el of root.querySelectorAll('[data-minimap-focus-id]')) {
    el.addEventListener('click', e => { e.stopPropagation(); setGameFocus(el.dataset.minimapFocusId); });
  }
  for (const el of root.querySelectorAll('.game-node-inner[data-focus-id]')) {
    el.addEventListener('click', () => setGameFocus(el.dataset.focusId));
  }
}

function setGameFocus(nodeId) {
  gameFocusId = nodeId;
  tidyOffPath();
  renderGame();
}

function toggleSupports(id) {
  const el = document.getElementById(id);
  const arrow = document.getElementById('arrow-' + id);
  if (!el || !arrow) return;
  if (el.style.display === 'none') { el.style.display = ''; arrow.textContent = '▼'; }
  else { el.style.display = 'none'; arrow.textContent = '▶'; }
}

// --- Move helpers ----------------------------------------------------

function getGameUsedCanonicalKeys(node) {
  const used = new Set();
  let cur = node;
  while (cur) {
    const arg = getArgumentById(cur.argId);
    if (arg) used.add(canonicalKey(arg));
    cur = cur.parentId ? gameNodes[cur.parentId] : null;
  }
  return used;
}

// Relevance rule (grounded semantics): only surface attackers that
// contribute to the target's label under Caminada's game.
//   target IN    → show OUT attackers (why IN: every attacker defeated)
//   target OUT   → show IN attackers  (why OUT: the winning attackers)
//   target UNDEC → show UNDEC attackers (why UNDEC: the tie-makers;
//                  OUT attackers were defeated elsewhere and aren't
//                  why this is undecided)
// An attacker's "label" at the canonical level is the strongest label
// any of its Cartesian variants carries (in > undec > out), matching
// how proposition labels aggregate in the backend.
function canonicalLabelOf(argId) {
  const labels = new Set(getVariantsOf(argId).map(a => a.label));
  if (labels.has('in')) return 'in';
  if (labels.has('undec')) return 'undec';
  return 'out';
}

function _getGameCanonicalMoves(node) {
  const target = getArgumentById(node.argId);
  const canonicalIds = getCanonicalAttackerIds(node.argId);
  const usedKeys = getGameUsedCanonicalKeys(node);
  const childKeys = new Set();
  for (const cid of node.children) {
    const carg = getArgumentById(gameNodes[cid].argId);
    if (carg) childKeys.add(canonicalKey(carg));
  }
  const relevantLabel = target && { in: 'out', out: 'in', undec: 'undec' }[target.label];
  return canonicalIds.filter(aid => {
    const a = getArgumentById(aid);
    const key = a ? canonicalKey(a) : null;
    if (!key || usedKeys.has(key) || childKeys.has(key)) return false;
    if (!relevantLabel) return true;
    return canonicalLabelOf(aid) === relevantLabel;
  });
}

// HTB and CB both face the same structural problem: given this node,
// what canonical attackers have not yet appeared in the branch? The
// HTB/CB distinction is semantic, not structural.
function getGameCBs(htbNode)  { return _getGameCanonicalMoves(htbNode); }
function getGameHTBs(cbNode)  { return _getGameCanonicalMoves(cbNode); }

// Find arguments in the AF that would attack `targetArgId` (rebut or
// undercut) but have no attack edge against it because a rule preference
// filtered the attack out. Dedupes by canonical key.
function getSuppressedAttackersOf(targetArgId) {
  const af = state.bundle.af;
  const target = getArgumentById(targetArgId);
  if (!target) return [];
  const negConclusion = target.conclusion.startsWith('-')
    ? target.conclusion.slice(1)
    : '-' + target.conclusion;
  const undercutTarget = '-' + target.top_rule;
  const wouldAttack = (af.arguments || []).filter(
    a => a.conclusion === negConclusion || a.conclusion === undercutTarget
  );
  const actual = new Set(
    (af.attacks || []).filter(e => e.to === targetArgId).map(e => e.from)
  );
  const suppressed = wouldAttack.filter(a => !actual.has(a.id));
  const seen = new Set();
  const out = [];
  for (const a of suppressed) {
    const k = canonicalKey(a);
    if (!seen.has(k)) { seen.add(k); out.push(a); }
  }
  return out;
}

// HTML fragment describing preference-suppressed counter-attackers.
// Empty string when no preference story applies.
function preferenceDisclosureFor(argId) {
  const suppressed = getSuppressedAttackersOf(argId);
  if (suppressed.length === 0) return '';
  const items = suppressed.map(a => {
    const rule = state.bundle.scenario.rules?.[a.top_rule];
    const body = rule
      ? renderRuleText(a.top_rule, rule)
      : `<em>"${escapeHtml(a.conclusion_nl)}"</em> <span class="inline-id">[${escapeHtml(a.top_rule)}]</span>`;
    return `<li>${body}</li>`;
  }).join('');
  const lead = suppressed.length === 1
    ? 'A rule preference rules out this counter-argument:'
    : `A rule preference rules out these ${suppressed.length} counter-arguments:`;
  return `<div class="game-pref-disclosure">
    <div class="game-pref-lead">${lead}</div>
    <ul class="game-pref-list">${items}</ul>
  </div>`;
}

function getGameCycleAttackers(node) {
  const canonicalIds = getCanonicalAttackerIds(node.argId);
  const usedKeys = getGameUsedCanonicalKeys(node);
  return canonicalIds.filter(aid => {
    const a = getArgumentById(aid);
    return a && usedKeys.has(canonicalKey(a));
  });
}

function findGameOpen(node) {
  if (!node || node.type === 'cycle') return null;
  for (const cid of node.children) {
    const child = gameNodes[cid];
    const deep = findGameOpen(child);
    if (deep) return deep;
  }
  const hasMoves = (node.type === 'htb' && getGameCBs(node).length > 0) ||
                   (node.type === 'cb'  && getGameHTBs(node).length > 0);
  if (!node.resolution && hasMoves) return node.id;
  return null;
}

// Find any unresolved descendant regardless of move availability. Used
// as a fallback when findGameOpen returns nothing but the subtree still
// has unresolved leaves -- those are "stuck on Continue" nodes (user
// saw a No-defense / No-challenges panel but never clicked Continue).
// Focusing to one lets the user finish the resolution cascade instead
// of landing on a permanent "Resolving…" placeholder.
function findStuckUnresolved(node) {
  if (!node || node.type === 'cycle') return null;
  for (const cid of node.children) {
    const child = gameNodes[cid];
    const deep = findStuckUnresolved(child);
    if (deep) return deep;
  }
  if (!node.resolution) return node.id;
  return null;
}

function findAllGameOpen(node, results) {
  if (!node || node.type === 'cycle') return results;
  for (const cid of node.children) findAllGameOpen(gameNodes[cid], results);
  const hasMoves = (node.type === 'htb' && getGameCBs(node).length > 0) ||
                   (node.type === 'cb'  && getGameHTBs(node).length > 0);
  if (!node.resolution && hasMoves && !results.find(r => r.id === node.id)) results.push(node);
  return results;
}

function playGameMove(type, argId, parentId) {
  const parent = gameNodes[parentId];
  if (!parent) return;
  const child = makeGameNode(type, argId, parentId);
  parent.children.push(child.id);
  gameFocusId = child.id;
  // Playing a new move counts as navigating into a fresh branch; fold
  // any resolved subtrees that are now off the ancestor path. The new
  // child's own subtree is empty, so it isn't affected.
  tidyOffPath();
  renderGame();
}

function playGameCycleMove(type, argId, parentId) {
  const parent = gameNodes[parentId];
  if (!parent) return;
  const child = makeGameNode('cycle', argId, parentId);
  child.resolution = 'undecided';
  parent.children.push(child.id);
  propagateGameUndecided(parent);
  renderGame();
}

function resolveGameUncontested(htbNode) {
  // HTB with no remaining challenges = proponent's claim stands = conceded.
  // (Previously set 'uncontested' here, which is CB terminology and made the
  // propagation cascade classify the parent as defeated instead of retracted.)
  htbNode.resolution = 'conceded';
  propagateGame(htbNode);
  renderGame();
}

function resolveGameUndefended(cbNode) {
  // CB with no defense = challenge stands = uncontested; parent HTB defeated.
  cbNode.resolution = 'uncontested';
  propagateGame(cbNode);
  renderGame();
}

function propagateGameUndecided(node) {
  if (!node) return;
  node.resolution = 'undecided';
  if (node.parentId) propagateGameUndecided(gameNodes[node.parentId]);
}

// Bubble resolutions up the tree using grounded-game semantics.
// Ordering matters: undecided > defeated/uncontested > conceded/retracted.
// An HTB is "conceded" (proponent's claim stands) only when every CB
// available to the opponent has been played and retracted -- if more CBs
// remain unplayed, the node is still under consideration. Same symmetric
// rule for CB "uncontested". Without the "all-exhausted" check, a partial
// walk (e.g., user explored only one of three possible CBs) would falsely
// resolve the root as conceded.
function propagateGame(node) {
  if (!node) return;
  const parent = node.parentId ? gameNodes[node.parentId] : null;

  if (node.type === 'htb') {
    if (node.children.some(cid => gameNodes[cid].resolution === 'undecided')) {
      node.resolution = 'undecided';
    } else if (node.children.some(cid => gameNodes[cid].resolution === 'uncontested')) {
      // Any unanswerable CB defeats the HTB.
      node.resolution = 'defeated';
    } else if (
      node.children.length > 0
      && node.children.every(cid => gameNodes[cid].resolution === 'retracted')
      && getGameCBs(node).length === 0
    ) {
      // Every played CB was defended; no further CBs available.
      node.resolution = 'conceded';
    }
  } else if (node.type === 'cb') {
    if (node.children.some(cid => gameNodes[cid].resolution === 'undecided')) {
      node.resolution = 'undecided';
    } else if (node.children.some(cid => gameNodes[cid].resolution === 'conceded')) {
      // Any successful defense retracts the CB.
      node.resolution = 'retracted';
    } else if (
      node.children.length > 0
      && node.children.every(cid => gameNodes[cid].resolution === 'defeated')
      && getGameHTBs(node).length === 0
    ) {
      // Every played HTB failed; no further defenses available.
      node.resolution = 'uncontested';
    }
  }

  if (parent) propagateGame(parent);
}
