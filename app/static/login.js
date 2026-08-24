/* Login page, with password recovery.
 *
 * Three panels on one page rather than three pages: the whole flow is
 * sign-in-adjacent, and a recovery that navigates away loses the email the user
 * already typed. `login`, `requestPasswordCode`, `resetPassword` and the session
 * helpers come from auth.js.
 */

const loginHint = document.getElementById("login-hint");
const loginFoot = document.getElementById("login-foot");

const form = document.getElementById("login-form");
const emailInput = document.getElementById("email");
const passwordInput = document.getElementById("password");
const errorBox = document.getElementById("login-error");
const loginNote = document.getElementById("login-note");
const button = document.getElementById("login-button");
const buttonLabel = document.getElementById("login-button-label");

const forgotForm = document.getElementById("forgot-form");
const forgotEmail = document.getElementById("forgot-email");
const forgotError = document.getElementById("forgot-error");
const forgotNote = document.getElementById("forgot-note");
const forgotButton = document.getElementById("forgot-button");
const forgotButtonLabel = document.getElementById("forgot-button-label");

const resetForm = document.getElementById("reset-form");
const resetHint = document.getElementById("reset-hint");
const resetCode = document.getElementById("reset-code");
const resetPasswordInput = document.getElementById("reset-password");
const resetError = document.getElementById("reset-error");
const resetNote = document.getElementById("reset-note");
const resetButton = document.getElementById("reset-button");
const resetButtonLabel = document.getElementById("reset-button-label");
const resendButton = document.getElementById("resend");

const MIN_PASSWORD = 8;

// Carried from step 1 to step 2 so the code is checked against the address it
// was actually sent to, and the user does not type it twice.
let recoveryEmail = "";


// -------------------------
// Panels
// -------------------------

function show(panel) {

  const onLogin = panel === "login";

  loginHint.hidden = !onLogin;
  form.hidden = !onLogin;
  loginFoot.hidden = !onLogin;

  forgotForm.hidden = panel !== "forgot";
  resetForm.hidden = panel !== "reset";

  [errorBox, loginNote, forgotError, forgotNote, resetError, resetNote]
    .forEach((box) => { box.hidden = true; });
}


function fill(box, message) {
  box.textContent = message;
  box.hidden = false;
}


function busy(btn, label, loading, busyText, idleText) {
  btn.disabled = loading;
  label.textContent = loading ? busyText : idleText;
}


/** Turn a thrown API error into something worth reading. */
function explain(error, fallback) {

  if (error.status === 429) {
    const wait = error.retryAfter
      ? ` جرّب تاني بعد ${error.retryAfter} ثانية.`
      : " جرّب تاني بعد شوية.";
    return (error.message || "محاولات كتير.") + wait;
  }

  // 400 and 403 carry a message written for the user; anything else is
  // infrastructure and should not be quoted at them.
  if (error.status === 400 || error.status === 403) {
    return error.message;
  }

  return fallback;
}


// -------------------------
// Where to go after signing in
// -------------------------

function destination() {

  const next = new URLSearchParams(location.search).get("next");

  // Only same-site paths are followed. `next` arrives in the URL, so without
  // this check a crafted link could log somebody in and then hand them to
  // another site — with "//evil.example" being a path to the browser and an
  // origin to the parser.
  if (next && next.startsWith("/") && !next.startsWith("//")) {
    return next;
  }

  return "/";
}


// Already signed in: skip the form rather than asking again.
if (readSession()?.access_token) {
  location.replace(destination());
}


// -------------------------
// Sign in
// -------------------------

form.addEventListener("submit", async (event) => {

  event.preventDefault();
  errorBox.hidden = true;

  const email = emailInput.value.trim();
  const password = passwordInput.value;

  if (!email || !password) {
    fill(errorBox, "اكتب البريد الإلكتروني وكلمة المرور.");
    return;
  }

  busy(button, buttonLabel, true, "بيسجّل دخولك…", "دخول");

  try {

    await login(email, password);
    location.replace(destination());

  } catch (error) {

    // 401 is a wrong email or password; 403 is a real Supabase account with no
    // application user behind it, which the student cannot fix by retrying and
    // should not be told to.
    if (error.status === 403) {
      fill(errorBox, "الحساب ده مش مربوط بحساب على المنصة. كلّم الإدارة.");
    } else if (error.status === 401) {
      fill(errorBox, "البريد الإلكتروني أو كلمة المرور غلط.");
    } else {
      fill(errorBox, explain(error, "في مشكلة في الاتصال. جرّب تاني بعد شوية."));
    }

    passwordInput.value = "";
    passwordInput.focus();

  } finally {
    busy(button, buttonLabel, false, "بيسجّل دخولك…", "دخول");
  }
});


// -------------------------
// Step 1 — ask for a code
// -------------------------

document.getElementById("show-forgot").addEventListener("click", () => {
  show("forgot");
  // Carry over whatever they had already typed.
  forgotEmail.value = emailInput.value.trim();
  forgotEmail.focus();
});

document.querySelectorAll("[data-back]").forEach((node) => {
  node.addEventListener("click", () => {
    show("login");
    emailInput.focus();
  });
});


async function sendCode(email, onDone) {

  await requestPasswordCode(email);

  recoveryEmail = email;
  onDone();
}


forgotForm.addEventListener("submit", async (event) => {

  event.preventDefault();
  forgotError.hidden = true;
  forgotNote.hidden = true;

  const email = forgotEmail.value.trim();

  if (!email) {
    fill(forgotError, "اكتب بريدك الإلكتروني.");
    return;
  }

  busy(forgotButton, forgotButtonLabel, true, "بيبعت…", "ابعت الكود");

  try {

    await sendCode(email, () => {
      show("reset");
      resetHint.textContent = `بعتنا كود لـ ${email}. اكتبه هنا مع كلمة المرور الجديدة.`;
      resetCode.focus();
    });

  } catch (error) {
    fill(forgotError, explain(error, "مش قادرين نبعت الكود دلوقتي. جرّب تاني."));

  } finally {
    busy(forgotButton, forgotButtonLabel, false, "بيبعت…", "ابعت الكود");
  }
});


// -------------------------
// Step 2 — code plus new password
// -------------------------

resendButton.addEventListener("click", async () => {

  resetError.hidden = true;
  resendButton.disabled = true;

  try {
    await requestPasswordCode(recoveryEmail);
    fill(resetNote, "بعتنا كود جديد. شوف بريدك.");

  } catch (error) {
    fill(resetError, explain(error, "مش قادرين نبعت الكود تاني دلوقتي."));

  } finally {
    // Held down for a while: the server budget for resends is small, and a
    // button that can be hammered spends it in seconds.
    setTimeout(() => { resendButton.disabled = false; }, 30000);
  }
});


resetForm.addEventListener("submit", async (event) => {

  event.preventDefault();
  resetError.hidden = true;

  const code = resetCode.value.trim();
  const password = resetPasswordInput.value;

  if (!code) {
    fill(resetError, "اكتب الكود اللي وصلك.");
    return;
  }

  // Checked here as well as on the server, so the user is told before spending
  // the code — a rejected password would otherwise burn a single-use code.
  if (password.length < MIN_PASSWORD) {
    fill(resetError, `كلمة المرور لازم تكون ${MIN_PASSWORD} حروف على الأقل.`);
    resetPasswordInput.focus();
    return;
  }

  busy(resetButton, resetButtonLabel, true, "بيغيّر…", "غيّر كلمة المرور");

  try {

    await resetPassword(recoveryEmail, code, password);

    show("login");
    emailInput.value = recoveryEmail;
    fill(loginNote, "تم تغيير كلمة المرور. سجّل دخولك بيها دلوقتي.");
    passwordInput.focus();

  } catch (error) {
    fill(resetError, explain(error, "مش قادرين نغيّر كلمة المرور. جرّب تاني."));
    resetCode.focus();

  } finally {
    busy(resetButton, resetButtonLabel, false, "بيغيّر…", "غيّر كلمة المرور");
  }
});
