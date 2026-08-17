/*
 * Demo front-end.
 *
 * The video is always loaded whole. An answer only moves the playhead to the
 * segment's start and draws a flag at its end — playback is never interrupted
 * there, so the student can keep watching straight past the flag.
 */

const video = document.getElementById("video");
const timeline = document.getElementById("timeline");
const range = document.getElementById("timeline-range");
const flagEl = document.getElementById("timeline-flag");
const playhead = document.getElementById("timeline-playhead");
const clock = document.getElementById("clock");
const toast = document.getElementById("toast");
const segmentsEl = document.getElementById("segments");
const messages = document.getElementById("messages");
const composer = document.getElementById("composer");
const questionInput = document.getElementById("question");
const sendButton = document.getElementById("send");
const lectureName = document.getElementById("lecture-name");

// Current segment: { start, end, flagged }
let marker = null;

// Last few turns, sent back so follow-up questions make sense
const history = [];

let lectureId = null;
const sessionId =
  crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;


// -------------------------
// Helpers
// -------------------------

function stamp(seconds) {
  const total = Math.max(0, Math.floor(seconds || 0));
  const h = String(Math.floor(total / 3600)).padStart(2, "0");
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const s = String(total % 60).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

async function captureEvent(eventType) {
  if (!lectureId) return;

  try {
    const response = await fetch("/api/events", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        student_id: 1,
        lecture_id: lectureId,
        event_type: eventType,
        video_ts: video.currentTime,
        session_id: sessionId,
      }),
    });

    if (!response.ok) {
      console.error("Failed to capture event:", await response.text());
    }
  } catch (error) {
    console.error("Event capture failed:", error);
  }
}

// -------------------------
// Lecture bootstrap
// -------------------------

async function loadLecture() {

  const response = await fetch("/api/lectures");
  const lectures = await response.json();

  const lecture = lectures.find((item) => item.chunk_count > 0) || lectures[0];

  if (!lecture) {
    lectureName.textContent = "مفيش محاضرات — شغّل python -m rag.ingest الأول";
    return;
  }

  lectureId = lecture.id;
  video.src = lecture.video_url;

  lectureName.textContent =
    `${lecture.title} · ${lecture.chunk_count} مقطع · ${stamp(lecture.duration_ts)}`;

  if (!lecture.has_video) {
    lectureName.textContent += " · (ملف الفيديو مش موجود في data/videos)";
  }
}


// -------------------------
// Timeline + flag
// -------------------------

function drawTimeline() {

  const duration = video.duration;

  if (!duration || Number.isNaN(duration)) {
    return;
  }

  playhead.style.left = `${(video.currentTime / duration) * 100}%`;
  clock.textContent = stamp(video.currentTime);

  if (!marker) {
    range.hidden = true;
    flagEl.hidden = true;
    return;
  }

  const left = (marker.start / duration) * 100;
  const width = Math.max(((marker.end - marker.start) / duration) * 100, 0.6);

  range.style.left = `${left}%`;
  range.style.width = `${width}%`;
  range.hidden = false;

  flagEl.style.left = `${(marker.end / duration) * 100}%`;
  flagEl.hidden = false;
}

function showToast(text) {
  toast.textContent = text;
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.hidden = true; }, 8000);
}

function playSegment(segment, button) {

  marker = { start: segment.start_ts, end: segment.end_ts, flagged: false };

  segmentsEl.querySelectorAll("button").forEach((item) => {
    item.classList.toggle("active", item === button);
  });

  const seek = () => {
    video.currentTime = segment.start_ts;
    video.play().catch(() => { /* autoplay can be blocked; controls still work */ });
    drawTimeline();
  };

  if (video.readyState >= 1) {
    seek();
  } else {
    video.addEventListener("loadedmetadata", seek, { once: true });
  }

  toast.hidden = true;
}

video.addEventListener("timeupdate", () => {

  drawTimeline();

  // The flag is a marker, not a stop: announce it once and let it play on.
  if (marker && !marker.flagged && video.currentTime >= marker.end) {
    marker.flagged = true;
    showToast("🚩 خلص الجزء اللي بيجاوب سؤالك — الفيديو ماشي عادي، كمّل براحتك.");
  }
});

video.addEventListener("play", () => {
  captureEvent("play");
});

video.addEventListener("pause", () => {
  captureEvent("pause");
});

video.addEventListener("seeked", () => {
  captureEvent("seek");
});

video.addEventListener("ended", () => {
  captureEvent("complete");
});

video.addEventListener("loadedmetadata", drawTimeline);

timeline.addEventListener("click", (event) => {

  if (!video.duration) return;

  const box = timeline.getBoundingClientRect();
  const ratio = (event.clientX - box.left) / box.width;

  video.currentTime = Math.min(Math.max(ratio, 0), 1) * video.duration;
});


// -------------------------
// Chat
// -------------------------

function addMessage(role, html, extraClass = "") {

  const node = document.createElement("div");
  node.className = `msg ${role} ${extraClass}`.trim();
  node.innerHTML = html;

  messages.appendChild(node);
  messages.scrollTop = messages.scrollHeight;

  return node;
}

function renderAnswer(node, data) {

  // Turn [1] — and grouped forms like [1, 2] — into buttons that jump to that
  // exact moment in the video.
  const answer = escapeHtml(data.answer).replace(
    /\[([\d\s,،]+)\]/g,
    (match, group) =>
      group
        .split(/[,،]/)
        .map((part) => part.trim())
        .filter(Boolean)
        .map((index) => `<span class="cite" data-cite="${index}">[${index}]</span>`)
        .join("")
  );

  const paragraphs = answer
    .split(/\n+/)
    .filter((line) => line.trim())
    .map((line) => `<p>${line}</p>`)
    .join("");

  let html = paragraphs || `<p>${answer}</p>`;

  if (data.notice) {
    html += `<p class="notice">⚠️ ${escapeHtml(data.notice)}</p>`;
  }

  if (data.citations.length) {

    const sources = data.citations
      .map(
        (citation) => `
          <div class="source">
            <b>[${citation.index}]</b>
            ${stamp(citation.start_ts)} – ${stamp(citation.end_ts)}
            (distance ${citation.distance})<br>
            ${escapeHtml(citation.text.slice(0, 260))}…
          </div>`
      )
      .join("");

    html += `
      <details class="sources">
        <summary>مقاطع المحاضرة اللي الإجابة اتبنت عليها (${data.citations.length})</summary>
        ${sources}
      </details>`;
  }

  node.innerHTML = html;
  node.classList.toggle("ungrounded", !data.grounded || Boolean(data.notice));

  // Citation click -> seek to that chunk
  node.querySelectorAll(".cite").forEach((element) => {
    element.addEventListener("click", () => {
      const citation = data.citations[Number(element.dataset.cite) - 1];
      if (citation) {
        playSegment({ start_ts: citation.start_ts, end_ts: citation.end_ts }, null);
      }
    });
  });
}

function renderSegments(segments) {

  segmentsEl.innerHTML = "";

  segments.forEach((segment, index) => {

    const button = document.createElement("button");
    button.type = "button";
    button.textContent =
      `${index === 0 ? "▶ الجزء الأهم" : "▶ جزء كمان"} · ` +
      `${segment.start_label} 🚩 ${segment.end_label}`;

    button.addEventListener("click", () => playSegment(segment, button));

    segmentsEl.appendChild(button);
  });

  if (segments.length) {
    playSegment(segments[0], segmentsEl.firstChild);
  }
}

async function ask(question) {

  addMessage("student", `<p>${escapeHtml(question)}</p>`);

  const pending = addMessage("tutor", '<p class="typing">بدور في المحاضرة…</p>');

  sendButton.disabled = true;

  try {

    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: question,
        lecture_id: lectureId,
        history: history.slice(-6),
      }),
    });

    if (!response.ok) {
      throw new Error(await response.text());
    }

    const data = await response.json();

    renderAnswer(pending, data);
    renderSegments(data.segments);

    history.push({ role: "student", content: question });
    history.push({ role: "tutor", content: data.answer });

  } catch (error) {
    pending.innerHTML = `<p>حصلت مشكلة في الاتصال بالسيرفر:<br>${escapeHtml(String(error))}</p>`;
  } finally {
    sendButton.disabled = false;
    questionInput.focus();
  }
}

composer.addEventListener("submit", (event) => {

  event.preventDefault();

  const question = questionInput.value.trim();

  if (!question) return;

  questionInput.value = "";
  ask(question);
});

document.getElementById("suggestions").addEventListener("click", (event) => {

  if (event.target.tagName === "BUTTON") {
    ask(event.target.textContent.trim());
  }
});


loadLecture();
