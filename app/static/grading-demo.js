const $ = (id) => document.getElementById(id);
let dataset;
let criteria;

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[char]));
const pretty = (value) => escapeHtml(JSON.stringify(value, null, 2));

function setBusy(busy, message = "") {
  document.querySelectorAll("button").forEach((button) => { button.disabled = busy; });
  $("status").textContent = message;
  if (busy) $("error").hidden = true;
}

function showError(error) {
  $("error").hidden = false;
  $("error").textContent = error.message || String(error);
}

async function requestJson(path, body) {
  const response = await api(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : {"Content-Type": "application/json"},
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(JSON.stringify(data.detail || data, null, 2));
  return data;
}

function requestData() {
  return {
    question: $("question").value,
    model_answer: $("model-answer").value,
    student_answer: $("student-answer").value,
    max_points: $("max-points").value
  };
}

function renderStage(target, stage) {
  if (!stage) { $(target).innerHTML = '<span class="empty">Stage did not run.</span>'; return; }
  const parsed = stage.parsed_response || {};
  $(target).innerHTML = `
    <div class="meta"><span><b>Model:</b> ${escapeHtml(stage.model_identifier)}</span>
    <span><b>Prompt:</b> ${escapeHtml(stage.prompt_version)}</span>
    <span><b>Latency:</b> ${stage.latency_ms} ms</span>
    <span><b>Retries:</b> ${stage.retry_count}</span></div>
    ${parsed.needs_review ? '<span class="badge">Needs doctor review</span>' : ""}
    <p><b>Review reason:</b> ${escapeHtml(parsed.review_reason || "None")}</p>
    <details open><summary>Parsed response</summary><pre>${pretty(parsed)}</pre></details>
    <details><summary>Raw structured response</summary><pre>${escapeHtml(stage.raw_response || "Unavailable")}</pre></details>
    ${stage.retry_errors?.length ? `<details><summary>Retry information</summary><pre>${pretty(stage.retry_errors)}</pre></details>` : ""}`;
}

function renderScore(score) {
  if (!score) { $("score-output").innerHTML = '<span class="empty">No finalized score.</span>'; return; }
  $("score-output").innerHTML = `
    ${score.needs_review ? '<span class="badge">Needs doctor review — provisional score</span>' : ""}
    <h3>${escapeHtml(score.score)} / ${escapeHtml(score.max_points)}</h3>
    <table><thead><tr><th>Criterion</th><th>Status</th><th>Weight</th><th>Awarded</th></tr></thead><tbody>
    ${score.score_breakdown.map((row) => `<tr><td>${escapeHtml(row.criterion_id)}</td><td>${escapeHtml(row.status)}</td><td>${escapeHtml(row.weight)}</td><td>${escapeHtml(row.awarded_points)}</td></tr>`).join("")}
    </tbody></table>`;
}

function selectQuestion() {
  const question = dataset.questions.find((item) => item.question_id === $("question-select").value);
  $("case-select").innerHTML = question.student_answers.map((item) => `<option value="${item.case_id}">${escapeHtml(item.case_id)} — ${escapeHtml(item.label)}</option>`).join("");
}

function loadExample() {
  const question = dataset.questions.find((item) => item.question_id === $("question-select").value);
  const answer = question.student_answers.find((item) => item.case_id === $("case-select").value);
  $("question").value = question.question;
  $("model-answer").value = question.model_answer;
  $("student-answer").value = answer.answer;
  $("max-points").value = question.max_points;
}

async function generate() {
  setBusy(true, "Generating criteria…");
  try {
    const data = requestData();
    const result = await requestJson("/api/grading-demo/generate-criteria", {question: data.question, model_answer: data.model_answer});
    criteria = result.parsed_response.criteria;
    renderStage("criteria-output", result);
  } catch (error) { showError(error); } finally { setBusy(false); }
}

async function evaluate() {
  if (!criteria) { showError(new Error("Generate criteria first.")); return; }
  setBusy(true, "Evaluating the student answer…");
  try {
    const data = requestData();
    const result = await requestJson("/api/grading-demo/evaluate-answer", {question: data.question, criteria, student_answer: data.student_answer});
    renderStage("evaluator-output", result);
  } catch (error) { showError(error); } finally { setBusy(false); }
}

async function pipeline() {
  setBusy(true, "Running both LLM stages and deterministic scoring…");
  try {
    const result = await requestJson("/api/grading-demo/grade", requestData());
    renderStage("criteria-output", result.criteria_model);
    renderStage("evaluator-output", result.evaluator_model);
    renderScore(result.deterministic_scoring);
    if (result.error) throw new Error(result.error);
  } catch (error) { showError(error); } finally { setBusy(false); }
}

function renderDataset(result) {
  const m = result.metrics;
  const items = {
    "Evaluated": m.total_cases, "Successful": m.successful_cases, "Failed": m.failed_cases,
    "Review required": m.review_required_cases, "Mean absolute error": m.mean_absolute_error,
    "Exact agreement": m.exact_match_rate, "Within ±0.5": m.within_0_5_rate, "Within ±1": m.within_1_0_rate
  };
  $("dataset-output").innerHTML = `<div class="metrics">${Object.entries(items).map(([label, value]) => `<div class="metric">${escapeHtml(label)}<strong>${escapeHtml(value ?? "N/A")}</strong></div>`).join("")}</div>
  <div style="overflow:auto"><table><thead><tr><th>Question</th><th>Case</th><th>Expected</th><th>Predicted</th><th>Error</th><th>Review</th><th>Failure</th></tr></thead><tbody>
  ${result.cases.map((row) => `<tr><td>${row.question_id}</td><td>${row.case_id}</td><td>${row.expected_score}</td><td>${row.predicted_score ?? "—"}</td><td>${row.absolute_error ?? "—"}</td><td>${row.needs_review ?? "—"}</td><td>${escapeHtml(row.error || "")}</td></tr>`).join("")}</tbody></table></div>`;
}

async function runDataset() {
  setBusy(true, "Running all dataset cases; criteria are generated once per question…");
  try { renderDataset(await requestJson("/api/grading-demo/evaluate-dataset", {})); }
  catch (error) { showError(error); } finally { setBusy(false); }
}

async function init() {
  try {
    dataset = await requestJson("/api/grading-demo/dataset");
    $("question-select").innerHTML = dataset.questions.map((item) => `<option value="${item.question_id}">${item.question_id} — ${escapeHtml(item.question)}</option>`).join("");
    selectQuestion(); loadExample();
  } catch (error) { showError(error); }
}

$("question-select").addEventListener("change", selectQuestion);
$("load-example").addEventListener("click", loadExample);
$("generate").addEventListener("click", generate);
$("evaluate").addEventListener("click", evaluate);
$("pipeline").addEventListener("click", pipeline);
$("run-dataset").addEventListener("click", runDataset);
if (requireSession()) {
  if (currentUser()?.role === "doctor") {
    init();
  } else {
    document.querySelectorAll("button").forEach((button) => {
      button.disabled = true;
    });
    showError(new Error("This internal grading tool is restricted to doctors."));
  }
}
