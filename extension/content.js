// Activity Monitor — sensitive-field guard.
//
// Detects when focus enters a password / one-time-code field and tells the
// background worker, so the desktop keystroke-text logger suppresses capture
// while you type there. It NEVER reads, stores, or transmits field VALUES —
// only a single boolean ("the focused field is sensitive: yes/no").

function isSensitive(el) {
  if (!el || el.tagName !== "INPUT") return false;
  const type = (el.type || "").toLowerCase();
  if (type === "password") return true;

  const ac = (el.autocomplete || el.getAttribute("autocomplete") || "")
    .toLowerCase();
  if (/current-password|new-password|one-time-code/.test(ac)) return true;

  // Sites that don't set type=password but clearly collect a secret/OTP.
  const hint = `${el.name || ""} ${el.id || ""} ` +
    `${el.getAttribute("aria-label") || ""}`.toLowerCase();
  if (/\b(otp|2fa|passcode|password|verification[ -]?code)\b/.test(hint)) {
    return true;
  }
  return false;
}

let current = false;
function report(sensitive) {
  if (sensitive === current) return;       // only report transitions
  current = sensitive;
  try {
    chrome.runtime.sendMessage({ type: "field", sensitive });
  } catch (e) {
    /* background may be asleep; the desktop TTL backstops this */
  }
}

document.addEventListener("focusin", (e) => report(isSensitive(e.target)), true);
document.addEventListener("focusout", () => report(false), true);

// Catch an autofocused password field present at load time.
report(isSensitive(document.activeElement));
