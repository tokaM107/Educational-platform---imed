/*
 * Weekly report renderer.
 *
 * The page is written to be *read*, not decoded. Three rules follow from that:
 *
 *  1. A number belongs in a sentence. "شفت 45% من مادة الأسبوع" tells a student
 *     something; a tile reading 45.1 does not. So the summary is prose with the
 *     figures set into it, and the raw per-lecture numbers live behind a
 *     "التفاصيل" toggle for whoever actually wants them.
 *  2. A shape beats a column of digits. The week is a row of bars, coverage is a
 *     ring, and where the time went is one stacked bar — each replacing several
 *     numbers that nobody would have compared by eye.
 *  3. Time away is time the lecture page was not visible, and nothing more. Said
 *     at every occurrence and again in the footnote, because the data cannot
 *     support any stronger claim.
 *
 * Everything the model wrote goes through escapeHtml before it reaches the DOM.
 */

const sheet = document.getElementById("sheet");
const subjectPicker = document.getElementById("subject");
const weekLabel = document.getElementById("week-label");

const params = new URLSearchParams(location.search);

const state = {
  studentId: params.get("student_id"),
  courseId: params.get("course_id"),
  weekStart: params.get("week_start"),
  // Set when opened from a notification: a report a completion froze, read back
  // exactly as it was issued rather than recomputed.
  reportId: params.get("report_id"),
};


// -------------------------
// Formatting
// -------------------------

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text === null || text === undefined ? "" : String(text);
  return div.innerHTML;
}

function duration(seconds) {

  const total = Math.round(seconds || 0);

  if (total < 60) return `${total} ثانية`;

  // Round to whole minutes first, then split. Splitting first lets a remainder
  // of 3590 s round to 60 minutes and print "2 س 60 د".
  const allMinutes = Math.round(total / 60);
  const hours = Math.floor(allMinutes / 60);
  const minutes = allMinutes % 60;

  if (!hours) return `${minutes} دقيقة`;
  if (!minutes) return `${hours} ساعة`;

  return `${hours} س ${minutes} د`;
}

function percent(value) {
  // null means the denominator is unknown, which is not the same as zero.
  return value === null || value === undefined ? "—" : `${Math.round(value)}%`;
}

/** A number set into the sentence around it, so it never stands alone. */
function n(value) {
  return `<b class="fig">${escapeHtml(value)}</b>`;
}

function shiftDays(isoDate, days) {
  const date = new Date(`${isoDate}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

const DAY_NAMES = ["الأحد", "الاتنين", "التلات", "الأربع", "الخميس", "الجمعة", "السبت"];

function dayName(isoDate) {
  return DAY_NAMES[new Date(`${isoDate}T00:00:00Z`).getUTCDay()];
}


// -------------------------
// Shapes
// -------------------------

function ring(value, caption) {
  /* Coverage as a ring: the one number the whole report turns on, given the
     space it deserves instead of a row in a grid. */

  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  const filled = (Math.min(Math.max(value || 0, 0), 100) / 100) * circumference;

  const tone = (value || 0) >= 70 ? "good" : (value || 0) >= 40 ? "mid" : "low";

  return `
    <div class="ring-wrap">
      <svg viewBox="0 0 128 128" class="ring ${tone}" aria-hidden="true">
        <circle cx="64" cy="64" r="${radius}" class="ring-track"></circle>
        <circle cx="64" cy="64" r="${radius}" class="ring-fill"
                stroke-dasharray="${filled} ${circumference - filled}"
                transform="rotate(-90 64 64)"></circle>
      </svg>
      <div class="ring-text">
        <span class="ring-value">${escapeHtml(percent(value))}</span>
        <span class="ring-caption">${caption}</span>
      </div>
    </div>`;
}

function weekBars(daily) {
  /* The week as a shape. "6 active days" leaves the reader guessing which six
     and whether they were even; this shows both at once. */

  const peak = Math.max(...daily.map((day) => day.watch_time_seconds), 1);

  const bars = daily.map((day) => {

    const height = day.watch_time_seconds
      ? Math.max((day.watch_time_seconds / peak) * 100, 6)
      : 0;

    const title = day.watch_time_seconds
      ? `${dayName(day.date)} ${day.date}: ${duration(day.watch_time_seconds)}`
      : `${dayName(day.date)} ${day.date}: مفيش مذاكرة`;

    return `
      <div class="day" title="${escapeHtml(title)}">
        <div class="day-track">
          <div class="day-bar ${day.watch_time_seconds ? "" : "empty"}"
               style="height:${height}%"></div>
        </div>
        <span class="day-name">${escapeHtml(dayName(day.date).slice(0, 4))}</span>
      </div>`;
  }).join("");

  return `<div class="week-bars">${bars}</div>`;
}

function timeBar(totals) {
  /* Where the session time went, as one bar. Watch time, time paused and time
     away are three numbers nobody compares by eye — as widths of the same bar
     the comparison is the picture. */

  const session = Math.max(totals.session_duration_seconds, 1);
  const watch = Math.min(totals.watch_time_seconds, session);
  const away = Math.min(totals.time_away_seconds, session - watch);
  const idle = Math.max(session - watch - away, 0);

  const width = (value) => `${(value / session) * 100}%`;

  return `
    <div class="time-bar">
      <div class="seg watch" style="width:${width(watch)}"
           title="الفيديو شغال"></div>
      <div class="seg idle" style="width:${width(idle)}"
           title="واقف أو مفتوح بدون تشغيل"></div>
      <div class="seg away" style="width:${width(away)}"
           title="الصفحة مش ظاهرة"></div>
    </div>
    <div class="time-key">
      <span><i class="swatch watch"></i> مشاهدة ${escapeHtml(duration(watch))}</span>
      <span><i class="swatch idle"></i> واقف ${escapeHtml(duration(idle))}</span>
      <span><i class="swatch away"></i> بعيد عن الصفحة ${escapeHtml(duration(away))}</span>
    </div>`;
}

function strip(lecture) {
  /* Time runs left-to-right like the video's own timeline, even though the page
     is right-to-left. */

  const length = lecture.duration_seconds;

  if (!lecture.opened || !length) {
    return `<div class="strip"><div class="strip-bar empty"></div></div>`;
  }

  const place = (span, className) => {
    const left = (span.start / length) * 100;
    const wide = Math.max(((span.end - span.start) / length) * 100, 0.5);
    return `<div class="${className}" style="left:${left}%;width:${wide}%"></div>`;
  };

  return `
    <div class="strip">
      <div class="strip-bar">
        ${lecture.skipped_spans.map((span) => place(span, "gap")).join("")}
        ${lecture.rewatched_spans.map((span) => place(span, "repeat")).join("")}
      </div>
    </div>`;
}


// -------------------------
// Sections
// -------------------------

function cover(data) {

  const course = data.course
    ? `${escapeHtml(data.course.title)} — <b>${escapeHtml(data.course.doctor_name)}</b>`
    : "مش مسجّل في كورس";

  const hero = data.totals
    ? `
      <div class="cover-hero">
        ${ring(data.totals.coverage_percentage, "من مادة الأسبوع")}
        <div class="cover-week">
          ${weekBars(data.totals.daily)}
          <p class="cover-week-note">
            مذاكرة في ${n(data.totals.active_days)} يوم من
            ${n(data.totals.week_days)} — طول العمود = وقت المشاهدة في اليوم.
          </p>
        </div>
      </div>`
    : "";

  // The same figures serve all three occasions; only the framing changes.
  const kicker = {
    module: "تقرير ختامي — خلّصت المقرر 🎓",
    exam: "تقرير — خلّصت أسئلة المحاضرة 📝",
  }[data.kind] || "تقرير أسبوعي";

  const subject = data.kind === "exam" && data.lecture_title
    ? `<p class="course">المحاضرة: ${escapeHtml(data.lecture_title)}</p>`
    : "";

  return `
    <section class="card cover">
      <div class="cover-head">
        <div>
          <p class="kicker">${escapeHtml(kicker)}</p>
          <h1>${escapeHtml(data.student.name)}</h1>
          <p class="course">${course}</p>
          ${subject}
        </div>
        <p class="week-range">${escapeHtml(data.week.start)} → ${escapeHtml(data.week.end)}</p>
      </div>
      ${hero}
    </section>`;
}

function summary(data) {

  if (!data.narrative) return "";

  return `
    <section class="card lead">
      <p class="headline">${escapeHtml(data.narrative.headline)}</p>
      <p class="prose">${escapeHtml(data.narrative.summary)}</p>
    </section>`;
}

function story(data) {
  /* The old tile grid, rewritten as sentences. Same figures, readable in one
     pass, and each one arrives with the words that make it mean something. */

  const t = data.totals;

  /* A student who has not opened anything yet. A wall of zeros is technically a
     report and useless as one, so say the one true thing instead. This is the
     normal state for a freshly enrolled student, not an error. */
  if (!t.lectures_opened && !t.questions_attempted) {

    return `
      <section class="card">
        <h2>الأسبوع ده</h2>
        <p class="prose">مفيش أي نشاط مسجّل على المنصة الأسبوع ده:
          ${n(t.lectures_registered)} محاضرة مسجّل فيها،
          إجمالي مدتها ${n(duration(t.lecture_material_seconds))}، ومفتحتش أي واحدة.</p>
        <p class="prose">التقرير بيتبني من المشاهدة الحقيقية، فأول ما تبدأ تتفرج
          هتلاقي هنا وقت المشاهدة، ونسبة اللي شفته من كل محاضرة، والأجزاء اللي
          محتاجة رجوع.</p>
      </section>`;
  }

  const lines = [];

  lines.push(
    `فتحت ${n(t.lectures_opened)} من ${n(t.lectures_registered)} محاضرة مسجّل فيها` +
    (t.lectures_completed ? `، ووصلت لآخر ${n(t.lectures_completed)} منهم` : "") +
    (t.lectures_untouched ? `، و${n(t.lectures_untouched)} مفتحتهاش خالص` : "") + "."
  );

  lines.push(
    `الفيديو كان شغال ${n(duration(t.watch_time_seconds))} بالمجموع، ` +
    `وشفت ${n(duration(t.covered_seconds))} من أصل ${n(duration(t.lecture_material_seconds))} ` +
    `مدة محاضرات الأسبوع — يعني ${n(percent(t.coverage_percentage))} من المادة. ` +
    `الجزء اللي رجعت سمعته تاني محسوب مرة واحدة هنا.`
  );

  if (t.session_duration_seconds) {
    lines.push(
      `قعدت على المنصة ${n(duration(t.session_duration_seconds))}، ` +
      `منهم ${n(duration(t.time_away_seconds))} صفحة المحاضرة مكانتش ظاهرة ` +
      `(${n(percent(t.time_away_rate))} من وقتك). الوقت ده مش محسوب من المشاهدة، ` +
      `والنظام مش عارف كنت بتعمل إيه فيه.`
    );
  }

  if (t.questions_attempted) {
    lines.push(
      `حلّيت ${n(t.questions_attempted)} سؤال، ` +
      `${n(t.questions_correct)} منهم صح (${n(percent(t.accuracy))}) ` +
      `في ${n(t.attempts)} محاولة.`
    );
  } else {
    lines.push("محلّيتش أي أسئلة الأسبوع ده، فمفيش حاجة نقيس بيها الفهم غير المشاهدة.");
  }

  if (t.pause_count || t.seek_count) {
    lines.push(
      `وقفت الفيديو ${n(t.pause_count)} مرة، ونقلت مكان التشغيل ${n(t.seek_count)} مرة.`
    );
  }

  if (t.lectures_without_length) {
    lines.push(
      `${n(t.lectures_without_length)} محاضرة مش معروف مدتها لأن نصّها مش مرفوع، ` +
      `فمش داخلة في نسبة المادة فوق.`
    );
  }

  const time = t.session_duration_seconds
    ? `<div class="time-block"><h3>وقتك رايح فين</h3>${timeBar(t)}</div>`
    : "";

  return `
    <section class="card">
      <h2>أسبوعك في سطور</h2>
      <ul class="story">${lines.map((line) => `<li>${line}</li>`).join("")}</ul>
      ${time}
    </section>`;
}

function verdict(lecture) {
  /* One plain sentence per lecture. The point of the whole page: a student
     should know where they stand before reading a single figure. */

  if (!lecture.opened) {
    return "مفتحتهاش الأسبوع ده — دي أول حاجة تبدأ بيها.";
  }

  const coverage = lecture.coverage_percentage;
  const parts = [];

  if (coverage === null) {
    parts.push(
      `سمعت ${duration(lecture.watch_time_seconds)}، بس مدة المحاضرة مش معروفة ` +
      `فمش قادرين نقول شفت قد إيه منها.`
    );
  } else if (lecture.completed && coverage >= 85) {
    parts.push(`خلّصتها وشفت ${percent(coverage)} منها — ده اللي المفروض يحصل.`);
  } else if (lecture.completed) {
    parts.push(
      `وصلت لآخرها، بس نقلت فوق أجزاء كبيرة: شفت ${percent(coverage)} بس من المحاضرة.`
    );
  } else if (coverage >= 60) {
    parts.push(`شفت ${percent(coverage)} منها ولسه مخلّصتهاش — فاضل الآخر.`);
  } else {
    parts.push(`بدأتها وسبتها بدري: ${percent(coverage)} بس من المحاضرة.`);
  }

  if ((lecture.time_away_rate || 0) >= 25) {
    parts.push(
      `و${duration(lecture.time_away_seconds)} من قعدتك الصفحة مكانتش ظاهرة.`
    );
  }

  if (lecture.rewatched_spans.length) {
    parts.push(
      `رجعت سمعت ${lecture.rewatched_spans.length} جزء تاني — غالباً الجزء الصعب.`
    );
  }

  if (lecture.weak_topics.length) {
    parts.push(`وغلطت في أسئلة عن ${lecture.weak_topics.join(" و")}.`);
  }

  return parts.join(" ");
}

function lectureCard(lecture) {

  const badge = !lecture.opened
    ? '<span class="badge untouched">مفتحتهاش</span>'
    : lecture.completed
      ? '<span class="badge done">خلّصتها</span>'
      : '<span class="badge open">لسه</span>';

  const q = lecture.questions;

  const rows = [
    ["وقت المشاهدة", duration(lecture.watch_time_seconds), "الفيديو كان شغال"],
    ["نسبة المادة", percent(lecture.coverage_percentage),
     `${duration(lecture.covered_seconds)} من ${duration(lecture.duration_seconds)}`],
    ["مدة القعدة", duration(lecture.session_duration_seconds),
     `${lecture.sessions} جلسة، بالوقفات`],
    ["بعيد عن الصفحة", duration(lecture.time_away_seconds),
     `${percent(lecture.time_away_rate)} من قعدتك`],
    ["وقفات", String(lecture.pause_count), "مرات إيقاف الفيديو"],
    ["تنقّلات", String(lecture.seek_count), "مرات نقل مكان التشغيل"],
    ["أسئلة", q.questions_attempted ? `${q.questions_correct} / ${q.questions_attempted}` : "—",
     q.questions_attempted ? `${percent(q.accuracy)} صح` : "محلّيتش عليها"],
  ];

  const chip = (span, className) =>
    `<span class="chip ${className}">${escapeHtml(span.start_label)} → ${escapeHtml(span.end_label)}</span>`;

  const spans = [];

  if (lecture.skipped_spans.length) {
    spans.push(`
      <p class="spans"><span class="k">أجزاء مشفتهاش:</span>
      ${lecture.skipped_spans.map((span) => chip(span, "gap")).join("")}</p>`);
  }

  if (lecture.rewatched_spans.length) {
    spans.push(`
      <p class="spans"><span class="k">رجعت سمعتها:</span>
      ${lecture.rewatched_spans.map((span) => chip(span, "repeat")).join("")}</p>`);
  }

  const details = lecture.opened
    ? `
      <details class="detail">
        <summary>التفاصيل والأرقام</summary>
        <dl class="numbers">
          ${rows.map(([key, value, note]) => `
            <div>
              <dt>${escapeHtml(key)}</dt>
              <dd><b>${escapeHtml(value)}</b> <span>${escapeHtml(note)}</span></dd>
            </div>`).join("")}
        </dl>
        ${spans.join("")}
      </details>`
    : "";

  return `
    <article class="lecture">
      <div class="lecture-head">
        <h3>${escapeHtml(lecture.title)}</h3>
        ${badge}
      </div>
      <p class="verdict">${verdict(lecture)}</p>
      ${strip(lecture)}
      ${details}
    </article>`;
}

function lectures(data) {

  const needsWork = [];
  const fine = [];
  const untouched = [];

  data.lectures.forEach((lecture) => {

    if (!lecture.opened) {
      untouched.push(lecture);
    } else if (
      (lecture.coverage_percentage !== null && lecture.coverage_percentage < 70) ||
      lecture.weak_topics.length ||
      (lecture.time_away_rate || 0) >= 25
    ) {
      needsWork.push(lecture);
    } else {
      fine.push(lecture);
    }
  });

  const group = (title, note, items) =>
    items.length
      ? `<h3 class="group">${escapeHtml(title)}
           <span class="group-note">${escapeHtml(note)}</span></h3>
         ${items.map(lectureCard).join("")}`
      : "";

  return `
    <section class="card">
      <h2>محاضرات الأسبوع</h2>
      <p class="section-note">
        الشريط تحت كل محاضرة هو المحاضرة من أولها لآخرها:
        <i class="swatch watched"></i> شفته،
        <i class="swatch gap"></i> مشفتوش،
        <i class="swatch repeat"></i> رجعت سمعته.
      </p>
      ${group("محتاجة رجوع", `${needsWork.length}`, needsWork)}
      ${group("ماشية تمام", `${fine.length}`, fine)}
      ${group("مفتحتهاش", `${untouched.length}`, untouched)}
    </section>`;
}

function topics(data) {

  if (!data.topics.length) return "";

  const rows = data.topics.map((topic) => {

    const accuracy = topic.accuracy || 0;
    const tone = accuracy >= 75 ? "high" : accuracy < 60 ? "low" : "";

    return `
      <div class="topic">
        <div class="topic-name">
          ${escapeHtml(topic.topic)}
          ${topic.conclusive ? "" : '<span class="thin">أسئلة قليلة</span>'}
        </div>
        <div class="meter ${tone}"><span style="width:${Math.min(accuracy, 100)}%"></span></div>
        <div class="topic-score">${topic.questions_correct}/${topic.questions_attempted}</div>
      </div>`;
  }).join("");

  return `
    <section class="card">
      <h2>المواضيع</h2>
      <p class="section-note">
        من أسئلة الأسبوع، بالسؤال مش بالمحاولة. الموضوع اللي فيه سؤال أو اتنين بس
        متعلّم عليه — نتيجته ممكن تكون صدفة.
      </p>
      <div class="topics">${rows}</div>
    </section>`;
}

function advice(data) {

  const nar = data.narrative;

  if (!nar) return "";

  const focus = nar.focus.length
    ? `
      <section class="card">
        <h2>ركّز على إيه</h2>
        ${nar.focus.map((point) => `
          <div class="focus">
            <b>${escapeHtml(point.lecture)}</b>
            <p class="what">${escapeHtml(point.what)}</p>
            <p class="why">${escapeHtml(point.why)}</p>
          </div>`).join("")}
      </section>`
    : "";

  const twoCol = (nar.strengths.length || nar.weaknesses.length)
    ? `
      <section class="card">
        <h2>نقط القوة والضعف</h2>
        <div class="two-col">
          <div>
            <h3>ماشي كويس</h3>
            <ul class="plain">${nar.strengths.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ul>
          </div>
          <div>
            <h3>محتاج شغل</h3>
            <ul class="plain">${nar.weaknesses.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ul>
          </div>
        </div>
      </section>`
    : "";

  return `
    ${twoCol}
    ${focus}
    <section class="card">
      <h2>خطة الأسبوع الجاي</h2>
      <ol class="plan">
        ${nar.advice.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ol>
      <p class="generated">
        القسم ده مكتوب بالذكاء الاصطناعي من أرقام التقرير نفسها، مش رأي شخصي من المحاضر.
      </p>
    </section>`;
}

function footnote(data) {

  return `
    <section class="footnote">
      <p><b>وقت المشاهدة</b> متجمّع من مقاطع التشغيل الحقيقية، مش بطرح آخر ثانية من
      أول ثانية — عشان النقل في الفيديو ما يزوّدش الرقم بالغلط. و<b>نسبة المادة</b>
      بتحسب الجزء المتكرر مرة واحدة، فهي تقول غطّيت قد إيه، مش قعدت قد إيه.</p>

      <p><b>الوقت البعيد عن الصفحة:</b> النظام بيسجّل بس إن صفحة المحاضرة بقت مش
      ظاهرة ورجعت. ده ممكن يكون تاب تاني، أو مكالمة، أو الشاشة اتقفلت —
      <b>مفيش طريقة</b> يعرف بيها النظام إنت فتحت إيه، والتقرير مش بيدّعي ده.
      الوقت ده بس مستبعد من وقت المشاهدة.</p>

      <p class="thin">اتولّد
      ${escapeHtml(new Date(data.generated_at).toLocaleString("ar-EG"))}</p>
    </section>`;
}


// -------------------------
// Load
// -------------------------

function render(data, pending = false) {

  const notice = data.notice
    ? `<div class="notice">${escapeHtml(data.notice)}</div>`
    : "";

  /* The numbers are already on screen at this point. Say plainly that the
     comment is being written now and roughly how long it takes, so half a minute
     of waiting reads as progress rather than as a page that has stalled. */
  const waiting = pending
    ? `<section class="card lead">
         <p class="headline pulse">التعليق بيتكتب دلوقتي…</p>
         <p class="prose">الأرقام كلها جاهزة تحت وتقدر تقراها أو تطبعها من دلوقتي.
         كتابة تعليق الدكتور بتاخد نص دقيقة تقريباً في أول مرة، وبعد كده بتفتح فوراً.</p>
       </section>`
    : "";

  if (!data.course) {
    sheet.innerHTML = cover(data) + notice;
    weekLabel.textContent = `${data.week.start} → ${data.week.end}`;
    return;
  }

  sheet.innerHTML = [
    cover(data),
    notice,
    pending ? waiting : summary(data),
    story(data),
    lectures(data),
    topics(data),
    pending ? "" : advice(data),
    footnote(data),
  ].join("");

  if (!state.reportId) {
    state.weekStart = data.week.start;
  }

  weekLabel.textContent = `${data.week.start} → ${data.week.end}`;

  const label = {
    module: "تقرير ختامي",
    exam: "تقرير المحاضرة",
  }[data.kind] || "التقرير الأسبوعي";

  document.title = `${label} — ${data.student.name} — ${data.week.end}`;
}

async function get(extra) {

  if (state.reportId) {

    const stored = await fetch(`/api/reports/${state.reportId}`);

    if (!stored.ok) throw new Error(await stored.text());

    return stored.json();
  }

  const query = new URLSearchParams({ student_id: state.studentId, ...extra });

  if (state.weekStart) query.set("week_start", state.weekStart);
  if (state.courseId) query.set("course_id", state.courseId);

  const response = await fetch(`/api/reports/weekly?${query}`);

  if (!response.ok) throw new Error(await response.text());

  return response.json();
}

function fail(error) {
  sheet.innerHTML = `
    <section class="card"><h2>مش قادر أجيب التقرير</h2>
    <p class="prose">${escapeHtml(String(error))}</p></section>`;
}

async function loadStored() {
  /* A frozen report: one fetch, nothing to generate, nothing to wait for. */

  sheet.innerHTML = '<p class="loading">جارِ فتح التقرير…</p>';

  try {
    render(await get({}));
  } catch (error) {
    fail(error);
  }
}

async function load({ refresh = false } = {}) {
  /* Two passes. The measured half comes back in milliseconds; a narrative that
     has to be written costs a model call. Drawing the numbers first means the
     report is readable and printable straight away — and once the narrative has
     been stored (see scripts/generate_weekly_reports.py) the second pass is a
     lookup and the two arrive together. */

  sheet.innerHTML = '<p class="loading">جارِ تجهيز التقرير…</p>';

  try {
    render(await get({ narrative: "false" }), true);
  } catch (error) {
    fail(error);
    return;
  }

  try {
    render(await get(refresh ? { refresh: "true" } : {}));
  } catch (error) {
    // The numbers are already on screen and true without it.
    console.error("narrative failed:", error);
    const banner = document.createElement("div");
    banner.className = "notice";
    banner.textContent = "تعليق الدكتور مش متاح دلوقتي، بس كل الأرقام في التقرير صحيحة.";
    sheet.prepend(banner);
  }
}

async function loadSubjects() {
  /* Who the report is about.
     A student arriving from the player's button already carries their own id in
     the URL, and for them the picker is noise — worse, it is a list of their
     classmates. So it only appears when there is genuinely a choice to make,
     which is the doctor's case. There is no session to read the current user
     from yet, so the alternative to asking would be guessing. */

  const field = subjectPicker.closest(".field");

  try {

    const response = await fetch("/api/reports/subjects");
    const subjects = await response.json();

    if (!subjects.length) {
      if (field) field.hidden = true;
      return subjects;
    }

    subjectPicker.innerHTML = subjects.map((subject) => `
      <option value="${subject.student_id}:${subject.course_id}">
        ${escapeHtml(subject.student_name)} — ${escapeHtml(subject.course_title)}
      </option>`).join("");

    if (!state.studentId) {
      state.studentId = String(subjects[0].student_id);
      state.courseId = String(subjects[0].course_id);
    }

    subjectPicker.value = `${state.studentId}:${state.courseId || subjects[0].course_id}`;

    if (field) field.hidden = subjects.length < 2;

    return subjects;

  } catch (error) {
    console.error("could not list report subjects:", error);
    if (field) field.hidden = true;
    return [];
  }
}

subjectPicker.addEventListener("change", () => {
  const [studentId, courseId] = subjectPicker.value.split(":");
  state.studentId = studentId;
  state.courseId = courseId;
  state.weekStart = null;
  load();
});

document.getElementById("prev").addEventListener("click", () => {
  state.weekStart = shiftDays(state.weekStart, -7);
  load();
});

document.getElementById("next").addEventListener("click", () => {
  state.weekStart = shiftDays(state.weekStart, 7);
  load();
});

document.getElementById("print").addEventListener("click", () => window.print());

/* The printed report is the full document, so every collapsed detail is opened
   for the print and put back afterwards. CSS cannot do this: a closed <details>
   is hidden by the browser's own stylesheet, not by a rule we can override.
   Bound to beforeprint rather than to the button, so Ctrl+P behaves the same. */

window.addEventListener("beforeprint", () => {
  document.querySelectorAll("details").forEach((node) => {
    node.dataset.wasOpen = String(node.open);
    node.open = true;
  });
});

window.addEventListener("afterprint", () => {
  document.querySelectorAll("details").forEach((node) => {
    node.open = node.dataset.wasOpen === "true";
  });
});

document.getElementById("rewrite").addEventListener("click", () => load({ refresh: true }));

(async function start() {

  if (state.reportId) {

    // Opened from a notification. The week nav and the student picker both
    // describe a live query, and this report is neither live nor a week.
    document.querySelector(".week-nav").hidden = true;
    document.getElementById("rewrite").hidden = true;

    const field = subjectPicker.closest(".field");
    if (field) field.hidden = true;

    loadStored();
    return;
  }

  const subjects = await loadSubjects();

  if (!state.studentId) {

    if (!subjects.length) {
      sheet.innerHTML = `
        <section class="card"><h2>مفيش تقارير لسه</h2>
        <p class="prose">مفيش طالب مسجّل في كورس، وبالتالي مفيش محاضرات نحسب عليها.
        التقرير بيتبني من مشاهدة حقيقية، فأول خطوة إنك تسجّل طالب في كورس:</p>
        <p class="prose"><code>python -m scripts.enroll list</code> — تشوف اللي عندك<br>
        <code>python -m scripts.enroll add --student-id N --course-id M</code></p>
        </section>`;
      return;
    }
  }

  load();
})();
