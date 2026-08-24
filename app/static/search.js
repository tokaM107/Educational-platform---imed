/*
 * The search test bench.
 *
 * Every reply is shown in three layers, because when a query goes wrong you
 * need to know *where*: the verdict (what the assistant decided), the rows (what
 * the catalog held), and the plan (what the model read the sentence as). A miss
 * with a filter on `users.name = "Zaghloul"` is a different bug from a miss with
 * no doctor filter at all, and only the third layer tells them apart.
 */

if (!requireSession()) {
  throw new Error("not signed in");
}

const form = document.getElementById("ask");
const input = document.getElementById("query");
const send = document.getElementById("send");

const answer = document.getElementById("results");
const verdict = document.getElementById("verdict");
const panel = document.getElementById("answer");

const clarifyBox = document.getElementById("clarify-box");
const clarifyText = document.getElementById("clarify-text");
const clarifyForm = document.getElementById("clarify-form");
const clarifyInput = document.getElementById("clarify-input");

// The conversation, so an answer to a clarification continues the search
// instead of starting a new one. Cleared whenever a fresh query is typed.
let history = [];

// What following the link actually lands on. Only lectures have a page of
// their own; everything else resolves to a lecture inside it.
const OPENS = {
  lecture: "افتح المحاضرة",
  course: "أول محاضرة",
  module: "أول محاضرة",
  subject: "محاضرة في المادة",
  doctor: "محاضرة للدكتور",
  student: "محاضرة",
};

const WORDS = {
  go: ["فيه نتيجة واحدة", "اتنقل عليها على طول"],
  choose: ["فيه أكتر من نتيجة", "الطالب هو اللي يختار"],
  none: ["مفيش نتايج", "الكاتالوج مفهوش حاجة بالمواصفات دي"],
  clarify: ["محتاج توضيح", "السؤال مفهوش حاجة نبحث بيها"],
  unsupported: ["مش متاح", "المنصة مفهاش الحاجة دي"],
  error: ["مشكلة", "الطلب نفسه فشل"],
};


async function run(query, extraHistory) {

  send.disabled = true;
  send.textContent = "بيدوّر…";

  try {

    const response = await api("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, history: extraHistory || [] }),
    });

    if (!response.ok) {
      throw new Error(`الخادم رجّع ${response.status}`);
    }

    show(await response.json(), query);

  } catch (error) {
    show({
      ok: false, outcome: "error", results: [], total: 0,
      notes: [String(error.message || error)],
      clarify: "", reason: "", missing: [], dropped: [], plan: null, sql: "",
    }, query);

  } finally {
    send.disabled = false;
    send.textContent = "ابحث";
  }
}


function show(out, query) {

  panel.hidden = false;

  const [word, said] = WORDS[out.outcome] || WORDS.error;

  verdict.className = `verdict ${out.outcome}`;
  verdict.innerHTML = "";
  verdict.append(el("span", "word", word), el("span", "said", said));

  if (out.total) {
    verdict.append(el("span", "said", `${out.total} نتيجة`));
  }

  // Clarification: keep the turn so the follow-up merges with it.
  clarifyBox.hidden = out.outcome !== "clarify";

  if (out.outcome === "clarify") {
    clarifyText.textContent = out.clarify || "محتاج تفاصيل أكتر.";
    clarifyInput.value = "";
    history = [
      { role: "user", content: query },
      { role: "model", content: JSON.stringify({ intent: "clarify", clarify: out.clarify }) },
    ];
    clarifyInput.focus();
  }

  answer.innerHTML = "";

  if (out.reason) {
    answer.append(el("p", "note", out.reason));
  }

  out.results.forEach((row) => answer.append(hit(row)));

  drawPlan(out);
  drawDebug(out);
}


function hit(row) {

  const live = Boolean(row.url);
  const node = document.createElement(live ? "a" : "div");

  node.className = `hit${live ? "" : " dead"}`;

  if (live) {
    node.href = row.url;
    node.target = "_blank";
    node.rel = "noopener";
  }

  const body = el("div", "body");
  body.append(el("div", "name", row.title || row.name || `#${row.id}`));

  const meta = el("div", "meta");

  // The year rides on the course title rather than standing as its own chip:
  // a lone Arabic run between English ones gets reordered to the far end of the
  // line, where it reads as belonging to nothing.
  const course = row.course
    && row.course.title + (row.course.academic_year ? ` (Y${row.course.academic_year})` : "");

  const bits = [
    row.doctor && row.doctor.name,
    course,
    row.module && row.module.title,
    row.subject,
    row.lectures !== undefined && `${row.lectures} lectures`,
    row.modules !== undefined && `${row.modules} modules`,
  ].filter(Boolean);

  bits.forEach((bit) => meta.append(el("span", "", bit)));

  if (bits.length) body.append(meta);

  node.append(el("span", "badge", row.kind), body);

  // Short label, full explanation on hover. `url_opens` is written for whoever
  // consumes the API and is too long to sit beside a title without shoving it.
  const label = el("span", live ? "go" : "go off",
    live ? (OPENS[row.kind] || "افتح") : "مفيش صفحة");

  if (row.url_opens) label.title = row.url_opens;

  node.append(label);

  return node;
}


function drawPlan(out) {

  const box = document.getElementById("plan");
  box.innerHTML = "";

  const plan = out.plan;

  if (!plan) {
    box.append(el("p", "note", "الموديل مرجّعش خطة."));
    return;
  }

  box.append(row("الهدف", plan.target || "—"));

  const filters = el("div", "val");

  (plan.filters || []).forEach((item) => {
    const chip = el("span", "filter");
    const shown = item.value || (item.values || []).join(", ");
    chip.append(document.createTextNode(`${item.table}.${item.column} ${item.op} "${shown}"`));
    if (item.means) chip.append(el("span", "from", `  ← ${item.means}`));
    filters.append(chip, document.createTextNode(" "));
  });

  box.append(row("الفلاتر", filters.childNodes.length ? filters : "مفيش"));

  if (plan.text) box.append(row("نص", plan.text));
  if (plan.missing && plan.missing.length) box.append(row("ناقص", plan.missing.join("، ")));

  box.append(row("الترتيب", plan.sort));
  box.append(row("الثقة", String(plan.confidence)));
}


function drawDebug(out) {

  const box = document.getElementById("debug");
  box.innerHTML = "";

  out.notes.forEach((note) => box.append(el("p", "note", note)));

  (out.dropped || []).forEach((item) => {
    const chip = el("span", "filter cut");
    const f = item.filter || {};
    chip.textContent = item.target
      ? `target ${item.target} — ${item.why}`
      : `${f.table}.${f.column} — ${item.why}`;
    box.append(chip, document.createElement("br"));
  });

  if (!out.notes.length && !(out.dropped || []).length) {
    box.append(el("p", "note", "مفيش حاجة اتشالت."));
  }

  if (out.sql) {
    const sql = document.createElement("pre");
    sql.className = "sql";
    sql.textContent = out.sql;
    box.append(sql);
  }
}


function row(key, value) {

  const line = el("div", "row");
  line.append(el("span", "key", key));

  if (typeof value === "string") {
    line.append(el("span", "val", value));
  } else {
    value.className = "val";
    line.append(value);
  }

  return line;
}


function el(tag, className, text) {

  const node = document.createElement(tag);

  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;

  return node;
}


form.addEventListener("submit", (event) => {
  event.preventDefault();
  const query = input.value.trim();
  if (!query) return;
  history = [];
  run(query);
});


clarifyForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const reply = clarifyInput.value.trim();
  if (!reply) return;
  run(reply, history);
});


// Sample chips come from search-assistant/cases.py — the same ten rows the
// grading script runs, so clicking one reproduces exactly what it tests.
api("/api/search/cases")
  .then((response) => response.json())
  .then((cases) => {

    const box = document.getElementById("samples");

    cases.forEach((sample) => {

      const chip = el("button", "chip");
      chip.type = "button";
      chip.title = sample.why;
      chip.append(el("span", "n", `${sample.n}`), document.createTextNode(sample.query));

      chip.addEventListener("click", () => {
        input.value = sample.query;
        history = sample.history || [];
        run(sample.query, sample.history);
      });

      box.append(chip);
    });
  })
  .catch(() => {});
