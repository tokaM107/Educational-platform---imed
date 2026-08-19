/*
 * Instructor post-exam view.
 *
 * Every figure on this page is a GROUP BY — nothing here is generated or
 * interpreted, so the page answers in milliseconds and says the same thing every
 * time it is opened. If the numbers ever stop speaking for themselves, prose can
 * be layered on later; they usually do.
 *
 * Two conventions the markup keeps everywhere:
 *   · a percentage with no denominator prints "—", never 0%. "Nobody answered"
 *     and "everybody got it wrong" are opposite findings.
 *   · a percentage from fewer than a handful of answers is labelled as thin
 *     rather than hidden, so the instructor can see it and discount it.
 */

const sheet = document.getElementById("sheet");
const picker = document.getElementById("exam");
const passInput = document.getElementById("pass-mark");

const params = new URLSearchParams(location.search);

const state = {
  lectureId: params.get("lecture_id"),
  courseId: params.get("course_id"),
  passMark: params.get("pass_mark") || "60",
};

passInput.value = state.passMark;


// -------------------------
// Formatting
// -------------------------

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text === null || text === undefined ? "" : String(text);
  return div.innerHTML;
}

function percent(value) {
  return value === null || value === undefined ? "—" : `${Math.round(value)}%`;
}

function tone(value) {
  if (value === null || value === undefined) return "";
  return value >= 75 ? "high" : value < 50 ? "low" : "";
}

function bar(value) {
  const width = value === null || value === undefined ? 0 : Math.min(value, 100);
  return `
    <div class="bar ${tone(value)}"><span style="width:${width}%"></span></div>
    <div class="bar-value">${escapeHtml(percent(value))}</div>`;
}


// -------------------------
// Sections
// -------------------------

function header(data) {

  const course = data.course_title
    ? `${escapeHtml(data.course_title)} — <b>${escapeHtml(data.doctor_name || "")}</b>`
    : "غير مرتبطة بمقرر";

  return `
    <section class="card cover">
      <div class="cover-head">
        <div>
          <p class="kicker">نتائج الامتحان</p>
          <h1>${escapeHtml(data.lecture_title)}</h1>
          <p class="course">${course}</p>
        </div>
        <p class="week-range">${data.summary.total_questions} سؤال</p>
      </div>
    </section>`;
}

function figures(data) {

  const s = data.summary;

  const item = (value, name, note, klass = "") => `
    <div class="figure ${klass}">
      <div class="value">${escapeHtml(value)}</div>
      <div class="name">${escapeHtml(name)}</div>
      <p class="note">${note}</p>
    </div>`;

  const cells = [
    item(percent(s.average_score), "متوسط الدرجة",
         "من إجمالي أسئلة الامتحان — السؤال اللي محدّش جاوبه بيتحسب غلط، زي الدرجة بالظبط.",
         (s.average_score || 0) >= s.pass_mark ? "good" : "bad"),
    item(percent(s.average_accuracy), "متوسط نسبة الصح",
         "من الأسئلة اللي الطالب جاوبها فعلاً. ده الرقم العادل لطالب حلّ نصّ الامتحان."),
    item(percent(s.average_score_completed), "متوسط اللي كمّلوا",
         `${s.students_completed} طالب جاوبوا كل الأسئلة. ده أقرب رقم لمتوسط امتحان حقيقي.`),
    item(percent(s.pass_rate), `نسبة النجاح (${Math.round(s.pass_mark)}%)`,
         "نسبة اللي درجتهم من درجة النجاح أو أعلى.",
         (s.pass_rate || 0) >= 60 ? "good" : "warn"),
    item(
      s.cohort_size === null
        ? String(s.students_attempted)
        : `${s.students_attempted} / ${s.cohort_size}`,
      "دخلوا الامتحان",
      s.cohort_size === null
        ? "المحاضرة مش تابعة لمقرر، فمفيش عدد مسجّلين نقارن بيه."
        : `${percent(s.participation_percent)} من المسجّلين في المقرر.`),
    item(percent(s.median_score), "الوسيط",
         "نصّ الطلبة فوقه ونصّهم تحته. لو بعيد عن المتوسط يبقى فيه طالب أو اتنين شادّين الرقم."),
  ];

  const outsiders = s.attempts_from_non_enrolled
    ? `<p class="section-note" style="margin-top:16px">
         استُبعدت ${s.attempts_from_non_enrolled} محاولة من أشخاص مش مسجّلين في المقرر،
         عشان محاولة واحدة غريبة ما تحرّكش المتوسط.</p>`
    : "";

  return `
    <section class="card">
      <h2>الأرقام الأساسية</h2>
      <p class="section-note">كل رقم تحته سطر يقول معناه بالظبط.</p>
      <div class="figures">${cells.join("")}</div>
      ${outsiders}
    </section>`;
}

function distribution(data) {

  const buckets = data.score_distribution;
  const peak = Math.max(...buckets.map((b) => b.students), 1);

  if (!data.summary.students_attempted) return "";

  const columns = buckets.map((b) => {
    const height = b.students ? Math.max((b.students / peak) * 100, 8) : 0;
    const fail = b.high <= data.summary.pass_mark;
    return `
      <div class="dist-col">
        <span class="dist-count">${b.students || ""}</span>
        <div class="dist-track">
          <div class="dist-bar ${b.students ? (fail ? "fail" : "") : "empty"}"
               style="height:${height}%"></div>
        </div>
        <span class="dist-label">${b.low}-${b.high}</span>
      </div>`;
  }).join("");

  return `
    <section class="card">
      <h2>توزيع الدرجات</h2>
      <p class="section-note">
        عدد الطلبة في كل شريحة. الأعمدة البرتقالي تحت درجة النجاح.
      </p>
      <div class="dist">${columns}</div>
    </section>`;
}

function optionBreakdown(q) {
  /* Which option the class actually picked.
     "38% got it wrong" might just be a hard question. "50% chose A" says one
     distractor is doing the teaching, and that is a thing an instructor can act
     on in the next lecture — so the options go under every stem, not behind a
     toggle. */

  if (!q.answers_recorded) {
    return `<span class="qmeta no-choices">
      مفيش إجابات مسجّل فيها الاختيار للسؤال ده
      (${q.attempts} محاولة اتسجّلت قبل ما النظام يخزّن الاختيار).
    </span>`;
  }

  const rows = q.options.map((o) => `
    <div class="opt ${o.is_correct ? "right" : ""} ${o.picks ? "" : "untouched"}">
      <span class="opt-key">${escapeHtml(o.option)}</span>
      <span class="opt-bar"><i style="width:${o.percent || 0}%"></i></span>
      <span class="opt-pct num">${escapeHtml(percent(o.percent))}</span>
      <span class="opt-text">${escapeHtml(o.text || "(اختيار مش موجود في السؤال)")}</span>
    </div>`).join("");

  const partial = q.answers_recorded < q.attempts
    ? `<span class="qmeta">مبني على ${q.answers_recorded} من ${q.attempts} محاولة —
       الباقي اتسجّل قبل ما النظام يخزّن الاختيار.</span>`
    : "";

  const warning = q.top_distractor
    ? `<p class="distractor">
         ⚠️ ${escapeHtml(percent(q.top_distractor.percent))} من الإجابات اختاروا
         «${escapeHtml(q.top_distractor.text || q.top_distractor.option)}».
         ده مش تشتيت عشوائي — يا إما السؤال ملخبط، يا إما الفكرة دي اتفهمت غلط
         وتستاهل دقيقة في المحاضرة الجاية.
       </p>`
    : "";

  return `<div class="options">${rows}</div>${warning}${partial}`;
}


function questions(data) {

  const rows = data.questions.map((q) => {

    const flagged = q.calibration && q.calibration !== "as_labelled";

    const badges = [];

    if (q.difficulty) {
      badges.push(`<span class="pill ${escapeHtml(q.difficulty)}">${escapeHtml(q.difficulty)}</span>`);
    }
    if (!q.reliable) {
      badges.push('<span class="pill thin">إجابات قليلة</span>');
    }
    if (q.calibration === "easier_than_labelled") {
      badges.push('<span class="pill mislabelled">أسهل من تصنيفه</span>');
    }
    if (q.calibration === "harder_than_labelled") {
      badges.push('<span class="pill mislabelled">أصعب من تصنيفه</span>');
    }
    if (q.question_id === data.hardest) {
      badges.push('<span class="pill hard">أصعب سؤال</span>');
    }
    if (q.question_id === data.easiest) {
      badges.push('<span class="pill easy">أسهل سؤال</span>');
    }

    if (q.top_distractor) {
      badges.push('<span class="pill mislabelled">مشتّت قوي</span>');
    }

    return `
      <tr class="${flagged || q.top_distractor ? "flagged" : ""}">
        <td class="stem">
          <b>${escapeHtml(q.stem)}</b>
          <span class="qmeta">${escapeHtml(q.topic)} · ${badges.join(" ")}</span>
          ${optionBreakdown(q)}
        </td>
        <td class="bar-cell">${bar(q.correct_percent)}</td>
        <td class="num">${escapeHtml(percent(q.first_attempt_percent))}</td>
        <td class="num">${q.students_correct} / ${q.students_answered}</td>
        <td class="num">${q.attempts_per_student === null ? "—" : q.attempts_per_student}</td>
      </tr>`;
  }).join("");

  return `
    <section class="card">
      <h2>سؤال سؤال</h2>
      <p class="section-note">
        «نسبة الصح» بتحسب الطالب صح لو جاوب صح في أي محاولة. «من أول مرة» بتستبعد
        إعادة المحاولة — الفرق بين الرقمين هو اللي بيقول إن السؤال محتاج تفكير تاني.
        «محاولات لكل طالب» فوق 1 معناها الفصل احتاج أكتر من محاولة.
      </p>
      <table class="qtable">
        <thead>
          <tr>
            <th>السؤال</th><th>نسبة الصح</th><th>من أول مرة</th>
            <th>صح / جاوبوا</th><th>محاولات لكل طالب</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </section>`;
}

function topics(data) {

  if (!data.topics.length) return "";

  const rows = data.topics.map((t) => `
    <div class="topic">
      <div class="topic-name">
        ${escapeHtml(t.topic)}
        ${t.reliable ? "" : '<span class="thin">إجابات قليلة</span>'}
      </div>
      <div class="meter ${tone(t.correct_percent)}">
        <span style="width:${t.correct_percent === null ? 0 : Math.min(t.correct_percent, 100)}%"></span>
      </div>
      <div class="topic-score num">${escapeHtml(percent(t.correct_percent))}</div>
    </div>`).join("");

  return `
    <section class="card">
      <h2>المواضيع — الأضعف أولاً</h2>
      <p class="section-note">
        محسوبة بالمحاولة على مستوى الموضوع كله، فهي بتقول أي جزء من المحاضرة
        محتاج يتعاد شرحه.
      </p>
      <div class="topics">${rows}</div>
    </section>`;
}

function roster(data) {

  if (!data.roster.length) {
    return `
      <section class="card">
        <h2>الطلبة</h2>
        <p class="section-note">محدّش جاوب أي سؤال في الامتحان ده لحد دلوقتي.</p>
      </section>`;
  }

  const rows = data.roster.map((r) => {

    const failed = r.score_percent !== null && r.score_percent < data.summary.pass_mark;

    return `
      <tr class="${failed ? "failed" : ""} ${r.completed ? "" : "incomplete"}">
        <td>${escapeHtml(r.name)}</td>
        <td class="num">${escapeHtml(percent(r.score_percent))}</td>
        <td class="num">${escapeHtml(percent(r.accuracy_percent))}</td>
        <td class="num">${r.questions_correct} / ${r.questions_answered}</td>
        <td class="num">${r.attempts}</td>
        <td>${r.completed ? "كمّل" : "لسه"}</td>
      </tr>`;
  }).join("");

  return `
    <section class="card">
      <h2>الطلبة</h2>
      <p class="section-note">
        مرتّبين بالدرجة. الصفوف الباهتة لطلبة لسه مخلّصوش كل الأسئلة، فدرجتهم
        ناقصة مش بالضرورة ضعيفة — بصّ على «نسبة الصح» جنبها.
      </p>
      <table class="roster">
        <thead>
          <tr><th>الطالب</th><th>الدرجة</th><th>نسبة الصح</th>
              <th>صح / جاوب</th><th>محاولات</th><th></th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </section>`;
}

function footnote() {

  return `
    <section class="footnote">
      <p><b>الدرجة</b> = الأسئلة اللي الطالب جابها صح ÷ كل أسئلة الامتحان، فالسؤال
      اللي مجاوبهوش بيتحسب غلط. <b>نسبة الصح</b> = الصح ÷ اللي جاوبه فعلاً. الرقمين
      مختلفين عن قصد: الأول بيحكم على الطالب، والتاني بيحكم على السؤال.</p>

      <p><b>توزيع الاختيارات</b> بيتحسب من الاختيار اللي الطالب ضغط عليه فعلاً،
      مش بس صح/غلط. الخيار اللي محدّش اختاره برضه معلومة: معناها إن السؤال في
      الحقيقة اختيار من تلاتة مش من أربعة.</p>

      <p><b>الصفحة دي فيها الإجابات الصحيحة</b>، فهي للمحاضر بس ولازم تتقفل
      بتسجيل دخول قبل ما المنصة تشتغل بجد — دلوقتي مفيش حاجة بتمنع طالب إنه
      يفتحها.</p>

      <p class="thin">كل الأرقام هنا محسوبة مباشرة من محاولات الطلبة وقت فتح
      الصفحة — مفيش أي جزء منها مكتوب بالذكاء الاصطناعي.</p>
    </section>`;
}


// -------------------------
// Load
// -------------------------

function render(data) {

  sheet.innerHTML = [
    header(data), figures(data), distribution(data),
    questions(data), topics(data), roster(data), footnote(),
  ].join("");

  document.title = `نتائج — ${data.lecture_title}`;
}

async function load() {

  sheet.innerHTML = '<p class="loading">جارِ تحميل النتائج…</p>';

  try {

    const query = new URLSearchParams({ pass_mark: state.passMark });
    const response = await fetch(`/api/exams/${state.lectureId}?${query}`);

    if (!response.ok) throw new Error(await response.text());

    render(await response.json());

  } catch (error) {
    sheet.innerHTML = `
      <section class="card"><h2>مش قادر أجيب النتائج</h2>
      <p class="prose">${escapeHtml(String(error))}</p></section>`;
  }
}

async function loadExams() {

  const query = new URLSearchParams();
  if (state.courseId) query.set("course_id", state.courseId);

  const response = await fetch(`/api/exams?${query}`);
  const exams = await response.json();

  if (!exams.length) {
    sheet.innerHTML = `
      <section class="card"><h2>مفيش امتحانات لسه</h2>
      <p class="prose">مفيش محاضرة عليها أسئلة. ضيف أسئلة لمحاضرة الأول.</p></section>`;
    picker.disabled = true;
    return;
  }

  picker.innerHTML = exams.map((exam) => `
    <option value="${exam.lecture_id}">
      ${escapeHtml(exam.lecture_title)} — ${exam.total_questions} سؤال،
      ${exam.students_attempted} طالب
    </option>`).join("");

  if (!state.lectureId) {
    state.lectureId = String(exams[0].lecture_id);
  }

  picker.value = state.lectureId;

  load();
}

picker.addEventListener("change", () => {
  state.lectureId = picker.value;
  load();
});

passInput.addEventListener("change", () => {
  // Clamped here as well as server-side; a blank box must not blank the page.
  const value = Number(passInput.value);
  state.passMark = String(Number.isFinite(value) ? Math.min(Math.max(value, 0), 100) : 60);
  passInput.value = state.passMark;
  load();
});

document.getElementById("print").addEventListener("click", () => window.print());

loadExams();
