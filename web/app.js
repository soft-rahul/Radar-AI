// ShelfRadar dashboard: fetches /api/runs/{id}/report and draws it. All text goes through
// textContent (report values come from third-party AI answers and must never become HTML).

const ENGINES = { ai_overview: "AI Overview", ai_mode: "AI Mode", copilot: "Copilot" };
const VARIANTS = { "mumbai-en": "English", "delhi-hi": "Hindi", "delhi-hinglish": "Hinglish" };
const VARIANT_STYLE = {
  "mumbai-en": { color: "var(--series-1)", shape: "circle" },
  "delhi-hi": { color: "var(--series-2)", shape: "square" },
  "delhi-hinglish": { color: "var(--series-3)", shape: "diamond" },
};
const SVG = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);
const pct = (x) => `${Math.round(x * 100)}%`;
const range = (r) => `${pct(r.low)}–${pct(r.high)}`;

// Same 95% Wilson interval as the Python analyzer, for cells the report leaves implicit (0 of n).
function wilson(k, n, z = 1.96) {
  if (!n) return [0, 1];
  const p = k / n, d = 1 + (z * z) / n;
  const c = (p + (z * z) / (2 * n)) / d;
  const m = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / d;
  return [Math.max(0, c - m), Math.min(1, c + m)];
}

function el(tag, attrs = {}, ...kids) {
  const node = tag.startsWith("svg:") ? document.createElementNS(SVG, tag.slice(4)) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") node.textContent = v;
    else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
    else node.setAttribute(k, v);
  }
  for (const kid of kids) if (kid != null) node.append(kid);
  return node;
}

// --- Tooltip: one floating element, same content on hover and keyboard focus -------------
const tip = $("tip");
function tipFor(node, lines) {
  const show = (x, y) => {
    tip.replaceChildren(el("strong", { text: lines[0] }), ...lines.slice(1).map((l) => el("div", { text: l })));
    tip.hidden = false;
    const w = tip.offsetWidth;
    tip.style.left = `${Math.min(x + 14, window.innerWidth - w - 8)}px`;
    tip.style.top = `${y + 14}px`;
  };
  node.addEventListener("pointermove", (e) => show(e.clientX, e.clientY));
  node.addEventListener("pointerleave", () => (tip.hidden = true));
  node.addEventListener("focus", () => {
    const b = node.getBoundingClientRect();
    show(b.left, b.bottom);
  });
  node.addEventListener("blur", () => (tip.hidden = true));
  node.setAttribute("aria-label", lines.join(". "));
}

// --- Data ---------------------------------------------------------------------------------
async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}

async function init() {
  try {
    const runs = (await getJSON("/api/runs")).filter((r) => r.status !== "running" && r.done_cells > 0);
    if (!runs.length) {
      $("status").textContent = "No runs yet. Run: uv run python scripts/scan.py --mode replay";
      return;
    }
    for (const r of runs) {
      $("run").append(el("option", { value: r.id, text: `#${r.id} · ${r.mode} · ${r.done_cells} answers · ${r.created_at.slice(0, 10)}` }));
    }
    const questions = await getJSON("/api/questions");
    const first = await getJSON(`/api/runs/${runs[0].id}/report`);
    const brands = [...new Set(first.overall.map((r) => r.brand))];
    for (const b of brands) $("focus").append(el("option", { value: b, text: b }));
    $("focus").value = first.focus;
    $("run").addEventListener("change", load);
    $("focus").addEventListener("change", load);
    window.QUESTIONS = Object.fromEntries(questions.map((q) => [q.id, q]));
    render(first);
  } catch (err) {
    $("status").textContent = `Could not load the report: ${err.message}`;
  }
}

async function load() {
  $("report").style.opacity = 0.5; // keep the frame while refetching
  try {
    render(await getJSON(`/api/runs/${$("run").value}/report?focus=${encodeURIComponent($("focus").value)}`));
  } catch (err) {
    $("status").textContent = `Could not load the report: ${err.message}`;
  } finally {
    $("report").style.opacity = 1;
  }
}

let lastReport = null;
let resizeTimer = 0;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => lastReport && renderLanguages(lastReport), 150);
});

function render(rep) {
  lastReport = rep;
  $("status").textContent = "";
  $("report").hidden = false;
  const run = rep.run;
  $("badge").textContent = run.mode === "replay"
    ? `Replay · 0 searches spent · ${rep.answers} answers`
    : `Live data · ${run.live_calls} searches · ${rep.answers} answers`;
  renderTiles(rep);
  renderHeat(rep);
  renderLanguages(rep);
  renderSources(rep);
  renderStance(rep);
  renderDice(rep);
  renderMethod(rep);
}

// --- Tiles ------------------------------------------------------------------------------
function renderTiles(rep) {
  const k = rep.focus_kpis;
  const fc = rep.factcheck;
  const tile = (label, s, hero = false, note = "") => el("div", { class: `tile${hero ? " hero" : ""}` },
    el("span", { class: "label", text: label }),
    el("span", { class: "value", text: s && s.n ? pct(s.rate) : "–" }),
    el("span", { class: "range", text: s && s.n ? `95% range ${range(s)} · ${s.k} of ${s.n}${note}` : "no data yet" }));
  $("tiles").replaceChildren(
    tile(`${rep.focus} named in AI answers`, k.named, true, " answers"),
    tile("Recommended when named", fc.available ? fc.focus_recommended : null, false, " readings"),
    tile("Named first", k.named_first, false, " answers"),
    tile("Own website cited", k.own_site_cited, false, " answers"),
  );
}

// --- Heatmap ----------------------------------------------------------------------------
function seqColor(rate) {
  if (rate <= 0) return "var(--seq-0)";
  return `var(--seq-${Math.min(7, 1 + Math.floor(rate * 7))})`;
}

function renderHeat(rep) {
  const groups = {};
  for (const r of rep.by_engine_variant) (groups[r.group] ??= {})[r.brand] = r;
  const answeredIn = (g) => Object.values(groups[g] || {})[0]?.answered ?? 0;
  const noBlockIn = (g) => Object.values(groups[g] || {})[0]?.no_ai_block ?? 0;
  const brands = rep.overall.map((r) => r.brand);
  const cols = Object.keys(ENGINES).flatMap((e) => Object.keys(VARIANTS).map((v) => `${e}|${v}`));

  const head1 = el("tr", {}, el("th"), ...Object.entries(ENGINES).map(([, name]) =>
    el("th", { class: "engine", colspan: 3, scope: "colgroup", text: name })));
  const head2 = el("tr", {}, el("th", { class: "brand", scope: "col", text: "Brand" }),
    ...cols.map((c) => el("th", { scope: "col", text: VARIANTS[c.split("|")[1]] })));
  const rows = brands.map((b) => el("tr", { class: b === rep.focus ? "focus" : "" },
    el("th", { class: "brand", scope: "row", text: b }),
    ...cols.map((c) => {
      const n = answeredIn(c);
      const [engine, variant] = c.split("|");
      if (!n) {
        const td = el("td", { class: "empty", tabindex: 0, text: "–" });
        tipFor(td, [`${b}: no AI answer`, `${ENGINES[engine]} · ${VARIANTS[variant]}`,
          `${noBlockIn(c)} searches showed no AI answer`]);
        return td;
      }
      const r = groups[c]?.[b] ?? { named: 0, answered: n, rate: 0 };
      if (!groups[c]?.[b]) [r.low, r.high] = wilson(0, n);
      const td = el("td", { tabindex: 0, text: pct(r.rate),
        style: { background: seqColor(r.rate), color: r.rate >= 0.5 ? "#ffffff" : "var(--text-primary)" } });
      tipFor(td, [`${pct(r.rate)} · ${b}`, `${ENGINES[engine]} · ${VARIANTS[variant]}`,
        `named in ${r.named} of ${r.answered} answers`, `95% range ${range(r)}`]);
      return td;
    })));
  $("heat").replaceChildren(el("thead", {}, head1, head2), el("tbody", {}, ...rows));
}

// --- Language dot plot with ranges ------------------------------------------------------
function marker(shape, cx, cy, color) {
  const common = { fill: color, stroke: "var(--surface-1)", "stroke-width": 2 };
  if (shape === "square") return el("svg:rect", { x: cx - 5, y: cy - 5, width: 10, height: 10, rx: 1, ...common });
  if (shape === "diamond") return el("svg:path", { d: `M${cx} ${cy - 6.5}L${cx + 6.5} ${cy}L${cx} ${cy + 6.5}L${cx - 6.5} ${cy}Z`, ...common });
  return el("svg:circle", { cx, cy, r: 5.5, ...common });
}

function renderLanguages(rep) {
  const byBrand = {};
  for (const r of rep.by_variant) (byBrand[r.brand] ??= {})[r.group] = r;
  const brands = rep.overall.map((r) => r.brand);
  const variants = Object.keys(VARIANTS).filter((v) => rep.by_variant.some((r) => r.group === v));
  const answered = Object.fromEntries(variants.map((v) => [v, rep.by_variant.find((r) => r.group === v)?.answered ?? 0]));

  $("lang-legend").replaceChildren(...variants.map((v) => {
    const s = el("svg:svg", { width: 16, height: 14, "aria-hidden": "true" }, marker(VARIANT_STYLE[v].shape, 8, 7, VARIANT_STYLE[v].color));
    return el("span", {}, s, document.createTextNode(`${VARIANTS[v]} (${answered[v]} answers)`));
  }));

  // Draw at the container's real width so labels stay readable on phones (no scaled-down text).
  const W = Math.max(300, $("lang-chart").clientWidth || 760);
  const left = W < 520 ? 104 : 150, right = 14, rowH = 34, top = 24;
  const H = top + brands.length * rowH + 8;
  const x = (v) => left + v * (W - left - right);
  const svg = el("svg:svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
    "aria-label": "Share of answers naming each brand, by language, with 95% ranges" });
  for (const t of [0, 0.25, 0.5, 0.75, 1]) {
    svg.append(el("svg:line", { class: "grid", x1: x(t), x2: x(t), y1: top - 6, y2: H - 4 }));
    svg.append(el("svg:text", { x: x(t), y: 12, "text-anchor": "middle", text: pct(t) }));
  }
  brands.forEach((b, i) => {
    const cy = top + i * rowH + rowH / 2;
    const g = el("svg:g");
    g.append(el("svg:text", { class: "brand-label", x: left - 10, y: cy + 4, "text-anchor": "end", text: b }));
    const lines = [b];
    variants.forEach((v, j) => {
      const r = byBrand[b]?.[v] ?? { rate: 0, named: 0, answered: answered[v] };
      if (!byBrand[b]?.[v]) [r.low, r.high] = wilson(0, answered[v]);
      const y = cy + (j - (variants.length - 1) / 2) * 8;
      const color = VARIANT_STYLE[v].color;
      g.append(el("svg:line", { x1: x(r.low), x2: x(r.high), y1: y, y2: y, stroke: color, "stroke-width": 2,
        "stroke-linecap": "round", "stroke-opacity": 0.55 }));
      g.append(marker(VARIANT_STYLE[v].shape, x(r.rate), y, color));
      lines.push(`${VARIANTS[v]}: ${pct(r.rate)} (${r.named}/${r.answered}, range ${range(r)})`);
    });
    const hit = el("svg:rect", { class: "row-hit", x: 0, y: cy - rowH / 2, width: W, height: rowH, tabindex: 0 });
    tipFor(hit, lines);
    svg.prepend(hit);
    svg.append(g);
  });
  $("lang-chart").replaceChildren(svg);

  $("lang-table").replaceChildren(
    el("thead", {}, el("tr", {}, el("th", { text: "Brand" }),
      ...variants.map((v) => el("th", { text: `${VARIANTS[v]} (range)` })))),
    el("tbody", {}, ...brands.map((b) => el("tr", {}, el("td", { text: b }),
      ...variants.map((v) => {
        const r = byBrand[b]?.[v];
        return el("td", { class: "num", text: r ? `${pct(r.rate)} (${range(r)})` : "0%" });
      })))));
}

// --- Sources and citation gap -----------------------------------------------------------
const CLASS_LABEL = { other: "Blogs and other", marketplace: "Shops", brand: "Brand websites", ugc: "Reddit, YouTube…",
  media: "News media", search_engine: "Google/Bing pages" };

function renderSources(rep) {
  const entries = Object.entries(rep.source_classes).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, n]) => n));
  $("sources").replaceChildren(...entries.map(([cls, n]) => {
    const row = el("div", { class: "hbar", tabindex: 0 },
      el("span", { text: CLASS_LABEL[cls] ?? cls }),
      el("div", { class: "track" }, el("div", { class: "fill", style: { width: `${(n / max) * 100}%` } })),
      el("span", { class: "n", text: String(n) }));
    tipFor(row, [`${n} sources`, CLASS_LABEL[cls] ?? cls]);
    return row;
  }));
  const o = rep.outside_top10;
  $("source-facts").replaceChildren(
    el("div", { class: "fact" }, el("b", { text: pct(rep.translated_share) }),
      el("span", { text: "of sources are English pages shown through Google Translate" })),
    el("div", { class: "fact" }, el("b", { text: o.sources ? pct(o.outside_by_url) : "–" }),
      el("span", { text: `of AI Overview sources are not in Google's top 10 for the same search (${o.sources} sources)` })));

  $("gap-sub").textContent = `Websites the AI cites when it names a rival but not ${rep.focus}. Getting covered here is the most direct route into those answers.`;
  $("gap").replaceChildren(
    el("thead", {}, el("tr", {}, el("th", { text: "Website" }), el("th", { text: "Type" }), el("th", { text: "Answers" }))),
    el("tbody", {}, ...(rep.citation_gap.length ? rep.citation_gap.map((g) => el("tr", {},
      el("td", { text: g.domain }), el("td", { text: CLASS_LABEL[g.class] ?? g.class }),
      el("td", { class: "num", text: String(g.answers) })))
      : [el("tr", {}, el("td", { colspan: 3, text: "No gap: the focus brand appears whenever a rival does." }))])));
}

// --- Stance (diverging: recommended / neutral / warned against) -------------------------
function renderStance(rep) {
  const fc = rep.factcheck;
  if (!fc.available) {
    $("fc-sub").textContent = "Fact-check not run for this run yet (scripts/factcheck.py).";
    $("stance").replaceChildren();
    $("stance-legend").replaceChildren();
    $("conflicts").replaceChildren();
    return;
  }
  $("fc-sub").textContent = `Gemini (${fc.model}${fc.models_note ? `; ${fc.models_note}` : ""}) read ${fc.answers_read} answers; every quote was checked word for word against the answer, ` +
    `and ${fc.dropped_by_verification} unverifiable items were dropped.`;
  const parts = [["recommended", "Recommended", "var(--pos)"], ["neutral", "Neutral", "var(--mid)"], ["negative", "Warned against", "var(--neg)"]];
  $("stance-legend").replaceChildren(...parts.map(([, label, c]) =>
    el("span", {}, el("i", { class: "key", style: { background: c } }), document.createTextNode(label))));
  $("stance").replaceChildren(...fc.stance.map((s) => {
    const total = s.recommended + s.neutral + s.negative;
    const bar = el("div", { class: "sbar" }, ...parts.filter(([k]) => s[k]).map(([k, , c]) =>
      el("i", { style: { width: `${(s[k] / total) * 100}%`, background: c } })));
    const row = el("div", { class: "srow", tabindex: 0 }, el("span", { text: s.brand }), bar,
      el("span", { class: "n", text: `${s.recommended}/${total}` }));
    tipFor(row, [`${s.brand}: recommended ${s.recommended} of ${total}`, `neutral ${s.neutral} · warned against ${s.negative}`]);
    return row;
  }));
  const items = fc.conflicts.map((c) => el("div", { class: "conflict" },
    el("div", { text: `${c.severity === "clear" ? "Clear conflict" : "Check"} · ${c.brand} ${c.product}: AI answers disagree on ${c.attribute.replaceAll("_", " ")} (${c.values.join(" vs ")})${c.severity === "clear" ? "" : ". Involves an approximate value, a range or a pack size."}` }),
    ...c.claims.map((cl) => el("div", {}, el("q", { text: cl.quote })))));
  for (const m of fc.fact_sheet_mismatches) {
    items.push(el("div", { class: "conflict" },
      el("div", { text: `${m.product}: AI says ${m.ai_says}, label says ${m.label_says} (${m.attribute.replaceAll("_", " ")})` }),
      el("q", { text: m.quote })));
  }
  if (!fc.fact_sheet_confirmed) {
    items.push(el("p", { class: "sub", text: "Fact sheet not confirmed yet: only disagreements between AI answers are shown." }));
  }
  $("conflicts").replaceChildren(...items);
}

// --- The dice ---------------------------------------------------------------------------
function renderDice(rep) {
  const chips = (list) => el("div", { class: "chips" }, ...(list.length ? list : ["(no brand)"]).map((b) =>
    el("span", { class: `chip${b === rep.focus ? " focus" : ""}`, text: b })));
  const rows = rep.dice.slice(0, 12).map((d) => el("tr", {},
    el("td", { text: window.QUESTIONS?.[d.question_id]?.intent ?? d.question_id }),
    el("td", { text: `${ENGINES[d.engine]} · ${VARIANTS[d.variant]}` }),
    el("td", {}, ...d.samples.map(chips)),
    el("td", { class: "changed", text: d.identical ? "same every time" : "changed" })));
  $("dice").replaceChildren(
    el("thead", {}, el("tr", {}, ...["Question", "Engine · language", "Brands named, run by run", ""].map((t) => el("th", { text: t })))),
    el("tbody", {}, ...(rows.length ? rows : [el("tr", {}, el("td", { colspan: 4, text: "Needs at least 2 samples of the same question." }))])));
}

function renderMethod(rep) {
  const s = rep.status;
  const notes = [
    `${rep.answers} AI answers: ${s.ok ?? 0} with an AI answer, ${s.no_ai_block ?? 0} where the engine showed none (left out of every percentage), ${s.error ?? 0} errors.`,
    "Ranges are 95% Wilson score intervals. Brands whose ranges overlap are tied.",
    "A brand counts only when the AI's own words name it (whole word, aliases in English and Hindi); names inside cited page titles do not count.",
    "Bing Copilot is queried without location or language settings; only the question text changes.",
    "Every SerpApi and Gemini response is saved, so this page can be reproduced offline with no keys.",
  ];
  $("method").replaceChildren(...notes.map((t) => el("li", { text: t })));
}

init();
