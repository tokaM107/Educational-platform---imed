/*
 * Session and the authenticated API client.
 *
 * Every protected request in this application goes through `api()`. That is the
 * point of the file: the token is attached in one place, refreshed in one place
 * and thrown away in one place, so no page has to remember to do any of it and
 * none of them can disagree about whether somebody is logged in.
 *
 * FastAPI owns the session — the browser never talks to Supabase directly. See
 * app/api/auth.py for why.
 */

const SESSION_KEY = "imed.session";
const LOGIN_PAGE = "/static/login.html";


// -------------------------
// Stored session
// -------------------------
//
// localStorage, which is readable by any script running on this origin — so
// this is only as safe as the app is free of XSS. It is the same exposure
// supabase-js has by default. The access token is short-lived, and logging out
// revokes the refresh token server-side rather than only dropping it here.

function readSession() {

  try {
    return JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
  } catch {
    return null;
  }
}


function writeSession(session) {
  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
}


function clearSession() {
  localStorage.removeItem(SESSION_KEY);
}


function currentUser() {
  return readSession()?.user || null;
}


function accessToken() {
  return readSession()?.access_token || null;
}


// -------------------------
// Login / logout
// -------------------------


async function login(email, password) {

  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

  const body = await response.json().catch(() => ({}));

  if (!response.ok) {
    const error = new Error(body.detail || "تعذّر تسجيل الدخول");
    error.status = response.status;
    throw error;
  }

  writeSession(body);

  return body;
}


/**
 * Ask Supabase to email a recovery code.
 *
 * Resolves the same way whether or not the address has an account — the server
 * answers identically on purpose, so there is nothing here to tell apart.
 */
async function requestPasswordCode(email) {

  const response = await fetch("/api/auth/password/forgot", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });

  const body = await response.json().catch(() => ({}));

  if (!response.ok) {
    const error = new Error(body.detail || "تعذّر إرسال الكود");
    error.status = response.status;
    error.retryAfter = Number(response.headers.get("Retry-After")) || 0;
    throw error;
  }

  return body;
}


/** Hand back the emailed code with a new password. */
async function resetPassword(email, code, newPassword) {

  const response = await fetch("/api/auth/password/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, code, new_password: newPassword }),
  });

  const body = await response.json().catch(() => ({}));

  if (!response.ok) {
    const error = new Error(body.detail || "تعذّر تغيير كلمة المرور");
    error.status = response.status;
    error.retryAfter = Number(response.headers.get("Retry-After")) || 0;
    throw error;
  }

  return body;
}


async function logout() {

  const token = accessToken();

  // Cleared first. Whatever the server says, this browser is logged out, and a
  // failed call must not leave the page holding a session it thinks is live.
  clearSession();

  if (token) {
    try {
      await fetch("/api/auth/logout", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ access_token: token }),
      });
    } catch {
      // Offline, or the token had already expired. Nothing left to do.
    }
  }

  goToLogin();
}


function goToLogin() {

  const next = encodeURIComponent(
    location.pathname + location.search
  );

  location.replace(`${LOGIN_PAGE}?next=${next}`);
}


/** Send an unauthenticated visitor to the login page. Call at page start. */
function requireSession() {

  if (!accessToken()) {
    goToLogin();
    return false;
  }

  return true;
}


// -------------------------
// Refresh
// -------------------------
//
// A page makes several requests at once, so an expired token produces a burst
// of 401s rather than one. `pending` collapses them onto a single refresh —
// without it each failure starts its own, and because Supabase rotates the
// refresh token on use, the second would present one that the first had already
// spent and log the user out for no reason.

let pending = null;


function refreshSession() {

  if (pending) return pending;

  const session = readSession();

  if (!session?.refresh_token) {
    return Promise.resolve(false);
  }

  pending = fetch("/api/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: session.refresh_token }),
  })
    .then(async (response) => {

      if (!response.ok) return false;

      writeSession(await response.json());
      return true;
    })
    .catch(() => false)
    .finally(() => { pending = null; });

  return pending;
}


// -------------------------
// The client
// -------------------------


/**
 * fetch(), with the session attached and 401 handled.
 *
 * Returns the Response, so callers keep using .json(), .ok and .status exactly
 * as they did. Only a 401 is treated as an auth problem: 403 means the server
 * understood who is asking and said no, and logging in again would not change
 * that, so it is handed back to the caller to show.
 */
async function api(path, options = {}) {

  const send = () => {

    const headers = new Headers(options.headers || {});
    const token = accessToken();

    if (token) headers.set("Authorization", `Bearer ${token}`);

    return fetch(path, { ...options, headers });
  };

  let response = await send();

  if (response.status === 401) {

    if (await refreshSession()) {
      response = await send();
    }

    if (response.status === 401) {
      clearSession();
      goToLogin();
    }
  }

  return response;
}


/** JSON convenience: POST a body and read the reply. */
function apiJson(path, body, method = "POST") {

  return api(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}


/**
 * A video source URL carrying the token.
 *
 * A <video> element fetches its own source and no header can be attached to
 * that request, so the token travels in the query string — the one endpoint
 * where it does. See get_current_user_streaming in app/api/deps.py.
 */
function videoUrl(lectureId) {
  return `/api/lectures/${lectureId}/video?access_token=${encodeURIComponent(accessToken() || "")}`;
}
