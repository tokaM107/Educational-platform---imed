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

// There is no login yet, so the app acts as one student throughout: events are
// recorded against them and the weekly report is theirs. `python -m scripts.enroll
// list` shows the ids. This is the line that becomes the session user once auth
// lands — until then it must point at a real, enrolled student.
const STUDENT_ID = 2;

// Who this browser is acting as. `?as=1` views the site as another user, which
// is how you see a doctor's notifications until there is a real login.
const VIEWER_ID =
  Number(new URLSearchParams(location.search).get("as")) || STUDENT_ID;

document.getElementById("report-link").href =
  `/static/report.html?student_id=${STUDENT_ID}`;


// -------------------------
// Notifications
// -------------------------
//
// Reports are no longer only a weekly job. Finishing the last lecture of a
// course, or the last question on a lecture, writes one in the background — and
// the student and their doctor are told about it here.
//
// Polled rather than pushed: "tell them next time they are on the site" needs a
// list and an unread count, not a socket, and a poll survives the tab being
// closed while the report was being written.

const NOTIFICATION_POLL_MS = 60000;

const bell = document.getElementById("bell");
const bellBadge = document.getElementById("bell-badge");
const inbox = document.getElementById("inbox");
const inboxList = document.getElementById("inbox-list");

function whenText(iso) {

  const seconds = Math.max((Date.now() - new Date(iso).getTime()) / 1000, 0);

  if (seconds < 90) return "دلوقتي";
  if (seconds < 3600) return `من ${Math.round(seconds / 60)} دقيقة`;
  if (seconds < 86400) return `من ${Math.round(seconds / 3600)} ساعة`;

  return `من ${Math.round(seconds / 86400)} يوم`;
}

function renderInbox(data) {

  bellBadge.textContent = data.unread > 9 ? "9+" : String(data.unread);
  bellBadge.hidden = data.unread === 0;
  bell.classList.toggle("has-unread", data.unread > 0);

  if (!data.items.length) {
    inboxList.innerHTML = '<p class="inbox-empty">مفيش إشعارات لسه.</p>';
    return;
  }

  inboxList.innerHTML = data.items.map((item) => `
    <button type="button" class="inbox-item ${item.read_at ? "" : "unread"}"
            data-id="${item.id}" data-report="${item.report_id || ""}">
      <b>${escapeHtml(item.title)}</b>
      <span class="inbox-body">${escapeHtml(item.body || "")}</span>
      <span class="inbox-when">${escapeHtml(whenText(item.created_at))}</span>
    </button>`).join("");

  inboxList.querySelectorAll(".inbox-item").forEach((node) => {

    node.addEventListener("click", async () => {

      const { id, report } = node.dataset;

      // Mark read first: opening the report is a new tab, and the failure mode
      // to avoid is a notification that stays bold after it has been read.
      try {
        await fetch(`/api/notifications/${id}/read`, { method: "POST" });
      } catch (error) {
        console.error("could not mark notification read:", error);
      }

      if (report) {
        window.open(`/static/report.html?report_id=${report}`, "_blank", "noopener");
      }

      loadNotifications();
    });
  });
}

async function loadNotifications() {

  try {
    const response = await fetch(`/api/notifications?user_id=${VIEWER_ID}&limit=15`);

    if (!response.ok) return;

    renderInbox(await response.json());

  } catch (error) {
    // A missing notification must never disturb the lecture page.
    console.error("could not load notifications:", error);
  }
}

bell.addEventListener("click", () => {
  const open = inbox.hidden;
  inbox.hidden = !open;
  bell.setAttribute("aria-expanded", String(open));
  if (open) loadNotifications();
});

document.addEventListener("click", (event) => {
  if (!inbox.hidden && !inbox.contains(event.target) && event.target !== bell) {
    inbox.hidden = true;
    bell.setAttribute("aria-expanded", "false");
  }
});

document.getElementById("read-all").addEventListener("click", async (event) => {
  event.stopPropagation();
  await fetch(`/api/notifications/read-all?user_id=${VIEWER_ID}`, { method: "POST" });
  loadNotifications();
});

loadNotifications();
setInterval(loadNotifications, NOTIFICATION_POLL_MS);


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
        student_id: STUDENT_ID,
        lecture_id: lectureId,
        event_type: eventType,
        video_ts: video.currentTime,
        session_id: sessionId,
      }),
      // The last event of a session is usually tab_hidden as the tab closes;
      // keepalive lets that request outlive the page instead of being dropped.
      keepalive: true,
    });

    if (!response.ok) {
      console.error("Failed to capture event:", await response.text());
    }
  } catch (error) {
    console.error("Event capture failed:", error);
  }
}


// -------------------------
// Engagement tracking
// -------------------------
//
// Play and pause alone cannot tell "watched for half an hour" apart from
// "pressed play and left the room", so a heartbeat goes out every 30 seconds
// while the video is genuinely running, and the page reports when it stops
// being visible.
//
// What visibility can say: this page went hidden. What it cannot say: what the
// student looked at instead. Another tab, a locked screen and a minimised
// window are the same event here — it is time away from the lecture, not
// evidence of anything else.

const HEARTBEAT_MS = 30000;

let heartbeatTimer = null;

function startHeartbeat() {

  // One timer at a time. `play` fires again after every seek and after every
  // segment jump, and stacking intervals would multiply the traffic.
  if (heartbeatTimer !== null) return;

  heartbeatTimer = setInterval(() => {

    // Guard the tick as well as the listeners: a background tab throttles
    // intervals rather than stopping them, and the element can end without a
    // pause we caught.
    if (video.paused || video.ended || document.visibilityState === "hidden") {
      return;
    }

    captureEvent("heartbeat");
  }, HEARTBEAT_MS);
}

function stopHeartbeat() {

  if (heartbeatTimer === null) return;

  clearInterval(heartbeatTimer);
  heartbeatTimer = null;
}

document.addEventListener("visibilitychange", () => {

  if (document.visibilityState === "hidden") {
    stopHeartbeat();
    captureEvent("tab_hidden");
    return;
  }

  captureEvent("tab_visible");

  // A hidden tab does not pause the video element, so playback may well still
  // be running; only restart the timer if it actually is.
  if (!video.paused && !video.ended) {
    startHeartbeat();
  }
});

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

  // The stream is behind the paywall, so say who is watching. This identifies
  // rather than authenticates — see app/api/subscriptions.py.
  video.src = `${lecture.video_url}?student_id=${STUDENT_ID}`;

  lectureName.textContent =
    `${lecture.title} · ${lecture.chunk_count} مقطع · ${stamp(lecture.duration_ts)}`;

  if (!lecture.has_video) {
    lectureName.textContent += " · (ملف الفيديو مش موجود في data/videos)";
  }

  checkAccess(lecture.id);
}

async function checkAccess(lecture) {
  /* A blocked video otherwise just fails to load, which looks like a broken
     page rather than a locked one. Ask first, and say so plainly. */

  try {

    const response = await fetch(
      `/api/subscriptions/access?student_id=${STUDENT_ID}&lecture_id=${lecture}`
    );

    if (!response.ok) return;

    const access = await response.json();

    if (access.allowed) return;

    showToast(
      "🔒 المحاضرة دي محتاجة اشتراك مع المحاضر. اشترك الأول عشان تقدر تتفرج."
    );

  } catch (error) {
    console.error("could not check access:", error);
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
  startHeartbeat();
});

video.addEventListener("pause", () => {

  stopHeartbeat();

  // Reaching the end fires `pause` immediately before `ended`. Recording it
  // would count every finished lecture as one pause the student never made,
  // and the weekly report reads pause counts as a sign of difficulty.
  if (video.ended) return;

  captureEvent("pause");
});

video.addEventListener("seeked", () => {
  captureEvent("seek");
});

video.addEventListener("ended", () => {
  captureEvent("complete");
  stopHeartbeat();
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
