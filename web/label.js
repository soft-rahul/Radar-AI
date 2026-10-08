// Verify mode: the human judges each highlighted find (right/wrong) and notes anything missed.
// Needs no brand knowledge. All text is inserted with textContent / text nodes.
const VARIANTS = { "mumbai-en": "English (Mumbai)", "delhi-hi": "Hindi (Delhi)", "delhi-hinglish": "Hinglish (Delhi)" };
const ENGINES = { ai_overview: "Google AI Overview", ai_mode: "Google AI Mode", copilot: "Bing Copilot" };
const runId = Number(new URLSearchParams(location.search).get("run") || 2);
const DRAFT = `shelfradar-verify-${runId}`;
const state = {};

function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) k === "text" ? (node.textContent = v) : node.setAttribute(k, v);
  node.append(...kids.filter((k) => k != null));
  return node;
}

function isDone(s, found) {
  const judged = found.every((f) => s.right.includes(f.brand) || s.wrong.includes(f.brand));
  return judged && (s.nothingElse || s.other.trim() !== "");
}

function refresh(answers) {
  let done = 0;
  for (const a of answers) {
    const ok = isDone(state[a.key], a.found);
    done += ok;
    document.getElementById(`card-${a.key}`)?.classList.toggle("done", ok);
  }
  document.getElementById("progress").textContent = `${done} of ${answers.length} answers checked`;
  try { localStorage.setItem(DRAFT, JSON.stringify(state)); } catch { /* storage blocked: fine */ }
  return done;
}

function highlighted(text, found) {
  const box = el("div", { class: "answer" });
  const spans = [...found].sort((x, y) => x.offset - y.offset);
  let at = 0;
  const marks = {};
  for (const f of spans) {
    if (f.offset < at) continue;
    box.append(document.createTextNode(text.slice(at, f.offset)));
    const m = el("mark", { text: text.slice(f.offset, f.offset + f.length) });
    marks[f.brand] = m;
    box.append(m);
    at = f.offset + f.length;
  }
  box.append(document.createTextNode(text.slice(at)));
  return { box, marks };
}

async function init() {
  const res = await fetch(`/api/runs/${runId}/label-set`);
  if (!res.ok) {
    document.getElementById("progress").textContent = `Could not load run ${runId} (HTTP ${res.status})`;
    return;
  }
  const data = await res.json();
  let draft = {};
  try { draft = JSON.parse(localStorage.getItem(DRAFT) || "{}"); } catch { draft = {}; }

  const cards = data.answers.map((a, i) => {
    const saved = data.saved[a.key];
    state[a.key] = draft[a.key] || (saved && saved.right ? { nothingElse: false, ...saved }
      : { right: [], wrong: [], other: "", nothingElse: false });
    const s = state[a.key];
    const { box, marks } = highlighted(a.text, a.found);

    const finds = a.found.map((f) => {
      const name = `${a.key}::${f.brand}`;
      const choice = (value, label) => {
        const input = el("input", { type: "radio", name, value });
        input.checked = (value === "right" ? s.right : s.wrong).includes(f.brand);
        input.addEventListener("change", () => {
          s.right = s.right.filter((b) => b !== f.brand);
          s.wrong = s.wrong.filter((b) => b !== f.brand);
          (value === "right" ? s.right : s.wrong).push(f.brand);
          marks[f.brand]?.classList.toggle("right", value === "right");
          marks[f.brand]?.classList.toggle("wrong", value === "wrong");
          refresh(data.answers);
        });
        if (input.checked) marks[f.brand]?.classList.add(value);
        return el("label", {}, input, document.createTextNode(label));
      };
      return el("div", { class: "find" },
        el("b", { text: `“${marks[f.brand]?.textContent ?? f.brand}” → ${f.brand}` }),
        choice("right", "Right"), choice("wrong", "Wrong"));
    });

    const other = el("input", { type: "text", placeholder: "Other product or company names you see (comma-separated)",
      "aria-label": "Other names not highlighted" });
    other.value = s.other;
    other.addEventListener("input", () => { s.other = other.value; refresh(data.answers); });
    const nothing = el("input", { type: "checkbox" });
    nothing.checked = s.nothingElse;
    nothing.addEventListener("change", () => { s.nothingElse = nothing.checked; refresh(data.answers); });

    return el("section", { class: "card", id: `card-${a.key}`, "aria-label": `Answer ${i + 1}` },
      el("div", { class: "meta", text: `${i + 1} / ${data.answers.length} · ${ENGINES[a.engine]} · ${VARIANTS[a.variant] || a.variant}` }),
      el("div", { class: "q", text: a.query }),
      box,
      el("div", { class: "finds" }, ...(finds.length ? finds
        : [el("div", { class: "find", text: "The tool found no brand in this answer." })])),
      el("div", { class: "other" }, other,
        el("label", {}, nothing, document.createTextNode(" Nothing else I can see"))));
  });
  document.getElementById("cards").replaceChildren(...cards);
  refresh(data.answers);

  document.getElementById("save").addEventListener("click", async () => {
    const done = refresh(data.answers);
    if (done < data.answers.length && !confirm(`${data.answers.length - done} answers are not finished. Save anyway?`)) return;
    const labels = Object.fromEntries(data.answers.map((a) => {
      const s = state[a.key];
      return [a.key, { right: s.right, wrong: s.wrong, other: s.other }];
    }));
    const r = await fetch("/api/labels", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: runId, labeller: "human (verify mode)", labels }),
    });
    document.getElementById("progress").textContent = r.ok
      ? `Saved ${done} checked answers. Go back to the chat and type "labels saved".`
      : `Save failed (HTTP ${r.status})`;
  });
}

init();
