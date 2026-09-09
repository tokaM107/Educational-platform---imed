/* Three-page, deterministic teacher assessment report. */
const sheet = document.getElementById("sheet");
const picker = document.getElementById("exam");
const passInput = document.getElementById("pass-mark");
const params = new URLSearchParams(location.search);
const state = { examId: params.get("exam_id") || params.get("lecture_id"),
  courseId: params.get("course_id"), passMark: params.get("pass_mark") || "" };
passInput.value = state.passMark;
passInput.placeholder = "المحفوظ";

function esc(value) { const node = document.createElement("div"); node.textContent = value ?? ""; return node.innerHTML; }
function pct(value) { return value === null || value === undefined ? "—" : `${Number(value).toFixed(1)}%`; }
function pp(value) { return value === null || value === undefined ? "—" : `${value >= 0 ? "+" : ""}${Number(value).toFixed(1)} pp`; }
function seconds(value) { return value === null || value === undefined ? "—" : `${Number(value).toFixed(1)} ث`; }
function evidence(level, n) { return `<span class="pill confidence ${String(level).toLowerCase()}">${esc(level)} · n=${esc(n)}</span>`; }
function card(value, label, sample = "") { return `<div class="metric"><b>${esc(value)}</b><span>${esc(label)}</span><small>${esc(sample)}</small></div>`; }
function label(value) { return ({easy:"سهل",moderate:"متوسط",difficult:"صعب",good:"جيد",weak:"ضعيف",negative:"سلبي",insufficient_data:"بيانات غير كافية",insufficient_variance:"تباين غير كاف",HIGH:"عالية",MEDIUM:"متوسطة",LOW:"منخفضة",STRONG_PERFORMANCE:"أداء قوي",ON_TRACK:"على المسار",NEEDS_SUPPORT:"يحتاج دعم",INCOMPLETE_OR_INSUFFICIENT:"غير مكتمل / دليل غير كاف"})[value] || value || "—"; }

function pageHeader(data, page, title) {
  return `<header class="page-head"><div><span>تحليلات التقييم · ${page}/3</span><h1>${esc(title)}</h1><p>${esc(data.exam_title)} — ${esc(data.course_title)}</p></div><div class="identity">${esc(data.doctor_name)}<br>${data.summary.total_questions} سؤال</div></header>`;
}

function healthPage(data) {
  const s = data.summary, d = s.difficulty_distribution, distPeak = Math.max(...data.score_distribution.map(x => x.students), 1);
  const distribution = data.score_distribution.map(x => `<div class="dist-col"><i style="height:${x.students / distPeak * 100}%"></i><b>${x.students || ""}</b><span>${x.low}–${x.high}</span></div>`).join("");
  return `<section class="report-page">${pageHeader(data,1,"صحة الامتحان")}
    <div class="evidence-line">${evidence(s.confidence,s.students_attempted)} <span>كل نسبة تُقرأ مع حجم عينتها.</span></div>
    <div class="metric-grid">
      ${card(`${s.students_attempted} / ${s.cohort_size}`,"بدأوا",pct(s.participation_percent))}
      ${card(s.students_completed,"أكملوا",pct(s.completion_percent))}
      ${card(pct(s.abandonment_percent),"توقفوا بعد البدء",`n=${s.students_attempted}`)}
      ${card(pct(s.average_score),"متوسط الدرجة","غير المجاب = خطأ")}
      ${card(pct(s.median_score),"وسيط الدرجة",`n=${s.students_attempted}`)}
      ${card(pct(s.pass_rate),`نجاح عند ${pct(s.pass_mark)}`,`n=${s.students_attempted}`)}
      ${card(pct(s.first_attempt_accuracy),"دقة أول محاولة",`n=${s.total_response_events}`)}
      ${card(pct(s.final_accuracy),"الدقة النهائية",`الكسب ${pp(s.learning_gain)}`)}
      ${card(s.average_attempts_per_question ?? "—","محاولة / طالب-سؤال",pct(s.students_needing_retry_percent)+" احتاجوا إعادة")}
    </div>
    <div class="two-col health-bottom"><div><h2>توزيع الدرجات</h2><div class="dist">${distribution}</div></div>
      <div><h2>صعوبة الأسئلة</h2><div class="difficulty"><span class="easy">${d.easy} سهل</span><span>${d.moderate} متوسط</span><span class="difficult">${d.difficult} صعب</span></div>
      <p class="compact">مدة الامتحان: متوسط ${seconds(s.duration.mean)}، وسيط ${seconds(s.duration.median)}، P25–P75 ${seconds(s.duration.p25)}–${seconds(s.duration.p75)} (n=${s.duration.sample_size}).</p></div></div>
    <div class="definition"><b>الدرجة</b> = الإجابات النهائية الصحيحة ÷ كل أسئلة الامتحان؛ غير المجاب خطأ. <b>الدقة بين المحاولات</b> = الصحيحة ÷ الأسئلة المُجابة فقط.</div>
  </section>`;
}

function diagnosisPage(data) {
  const priority = [...data.questions].sort((a,b) => b.review_priority_score-a.review_priority_score).slice(0,8);
  const qrows = priority.map(q => {
    const distractor = q.options.find(o => o.classification === "strong_misconception" || o.classification === "suspicious" || o.classification === "non_functioning");
    const reason = q.review_reasons.length ? q.review_reasons.join("؛ ") : "لا توجد إشارة مراجعة قوية";
    return `<tr><td><b>س${q.order}</b><small>${esc(q.topic)}</small></td><td>${pct(q.final_accuracy)}<small>${label(q.empirical_difficulty)} · n=${q.students_answered}</small></td><td>${q.discrimination === null ? "—" : q.discrimination}<small>${label(q.discrimination_label)} · n=${q.discrimination_sample_size}</small></td><td>${pct(q.retry_rate)}<small>زمن ${seconds(q.response_time.median)}</small></td><td><span class="priority ${q.review_priority.toLowerCase()}">${label(q.review_priority)}</span><small>${esc(reason)}</small>${distractor ? `<small>مشتت ${distractor.order}: ${pct(distractor.percent)} · ${label(distractor.classification)}</small>` : ""}</td></tr>`;
  }).join("");
  const topicRows = data.topics.slice(0,6).map(t => `<tr><td>${esc(t.topic)}<small>${t.questions} سؤال · ${t.participating_students} طالب</small></td><td>${pct(t.first_attempt_accuracy)}</td><td>${pct(t.final_accuracy)}<small>${pp(t.retry_gain)}</small></td><td>${pct(t.retry_rate)}</td><td>${seconds(t.median_response_time)}</td><td>${label(t.confidence)}${t.conclusive ? "" : "<small>دليل غير كاف</small>"}</td></tr>`).join("");
  const clusters = data.misconception_clusters.length ? data.misconception_clusters.slice(0,3).map(c => `<li><b>${esc(c.topic)}</b>: نمط متكرر عبر ${c.questions} أسئلة؛ راجع المفهوم أو صياغة البنود (${label(c.confidence)}).</li>`).join("") : "<li>لا توجد عناقيد مفاهيم خاطئة مستقرة في البيانات الحالية.</li>";
  return `<section class="report-page">${pageHeader(data,2,"تشخيص التدريس وجودة البنود")}
    <h2>أولوية مراجعة الأسئلة</h2><table class="compact-table questions"><thead><tr><th>السؤال</th><th>الصعوبة</th><th>التمييز</th><th>الإعادة/الزمن</th><th>الأولوية ولماذا</th></tr></thead><tbody>${qrows}</tbody></table>
    <h2>الموضوعات / أهداف التعلم — الأضعف أولاً</h2><table class="compact-table"><thead><tr><th>الموضوع</th><th>أول مرة</th><th>نهائي</th><th>إعادة</th><th>وسيط الزمن</th><th>الثقة</th></tr></thead><tbody>${topicRows}</tbody></table>
    <div class="diagnosis-foot"><div><h2>أنماط متكررة</h2><ul>${clusters}</ul></div><p class="method">التمييز: ارتباط point-biserial بين نتيجة البند ودرجة بقية البنود بعد استبعاد البند نفسه. لا حكم عند n&lt;10 أو غياب التباين. الصعوبة: سهل ≥80%، متوسط 50–79.9%، صعب &lt;50%.</p></div>
  </section>`;
}

function supportPage(data) {
  const order = {NEEDS_SUPPORT:0,INCOMPLETE_OR_INSUFFICIENT:1,ON_TRACK:2,STRONG_PERFORMANCE:3};
  const rows = [...data.roster].sort((a,b) => order[a.support_category]-order[b.support_category]).slice(0,14).map(s => `<tr><td>${esc(s.name)}<small>${s.completed ? "مكتمل" : s.started ? "بدأ ولم يكمل" : "لم يبدأ"}</small></td><td>${pct(s.score_percent)}</td><td>${pct(s.attempted_accuracy)}</td><td>${pct(s.first_attempt_accuracy)}</td><td>${s.retries}</td><td><span class="support ${s.support_category.toLowerCase()}">${label(s.support_category)}</span></td><td>${esc(s.primary_issue)}${s.weakest_topics.length ? `<small>${esc(s.weakest_topics.join("، "))}</small>` : ""}</td></tr>`).join("");
  const actions = data.teaching_actions.map(x => `<li>${esc(x)}</li>`).join("");
  const omitted = Math.max(data.roster.length-14,0);
  return `<section class="report-page">${pageHeader(data,3,"الطلاب الذين يحتاجون انتباهاً")}
    <table class="compact-table roster"><thead><tr><th>الطالب</th><th>الدرجة</th><th>دقة المُجاب</th><th>أول مرة</th><th>إعادات</th><th>الدعم</th><th>المشكلة الأساسية</th></tr></thead><tbody>${rows}</tbody></table>
    ${omitted ? `<p class="compact">يظهر هنا أعلى 14 حالة أولوية؛ ${omitted} طالب إضافي متاح في بيانات API الكاملة.</p>` : ""}
    <div class="actions-box"><h2>إجراءات التدريس المقترحة</h2><ol>${actions}</ol></div>
    <div class="privacy"><b>ملاحظة استخدام:</b> التصنيفات إشارات دعم محايدة وليست أحكاماً. النسب غير المستقرة تحمل ثقة منخفضة. التقرير محسوب من بيانات قاعدة البيانات فقط، بلا نموذج لغوي أو أرقام مُنشأة.</div>
  </section>`;
}

function render(data) { sheet.innerHTML = healthPage(data)+diagnosisPage(data)+supportPage(data); document.title=`تحليلات — ${data.exam_title}`; }
if (!requireSession()) throw new Error("not signed in");
if (currentUser()?.role !== "doctor") { sheet.innerHTML='<section class="card"><h2>هذه الصفحة للمحاضرين فقط</h2></section>'; throw new Error("not a doctor"); }
async function load() {
  sheet.innerHTML='<p class="loading">جارِ تحميل التحليلات…</p>';
  try { const query=new URLSearchParams(); if(state.passMark) query.set("pass_mark",state.passMark); const response=await api(`/api/exams/${state.examId}?${query}`); if(!response.ok) throw new Error(await response.text()); render(await response.json()); }
  catch(error){ sheet.innerHTML=`<section class="card"><h2>تعذر تحميل النتائج</h2><p>${esc(error)}</p></section>`; }
}
async function loadExams(){
  const query=new URLSearchParams(); if(state.courseId) query.set("course_id",state.courseId);
  const response=await api(`/api/exams?${query}`); if(!response.ok) throw new Error(await response.text()); const exams=await response.json();
  if(!exams.length){sheet.innerHTML='<section class="card"><h2>لا توجد امتحانات</h2></section>';picker.disabled=true;return;}
  picker.innerHTML=exams.map(e=>`<option value="${e.exam_id}">${esc(e.exam_title)} — ${e.total_questions} سؤال، ${e.students_attempted} طالب</option>`).join("");
  if(!state.examId) state.examId=String(exams[0].exam_id); picker.value=state.examId; load();
}
picker.addEventListener("change",()=>{state.examId=picker.value;load();});
passInput.addEventListener("change",()=>{const value=passInput.value.trim();state.passMark=value===""?"":String(Math.min(Math.max(Number(value),0),100));passInput.value=state.passMark;load();});
document.getElementById("print").addEventListener("click",()=>window.print());
loadExams();
