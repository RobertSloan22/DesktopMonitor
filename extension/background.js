// Activity Monitor — Web Tracker (Chrome / Edge / Brave, Manifest V3)
//
// Every ~30s, if a browser window is focused and you're not idle, this reports
// the active tab's URL + title to the local Activity Monitor server. If the
// server isn't running, the request silently fails — nothing breaks.

const SERVER = "http://localhost:8777/api/browser";
const INTERVAL_SEC = 30;           // matches the heartbeat period below
const IDLE_AFTER_SEC = 60;         // treat as idle after this much inactivity

// Service workers sleep; chrome.alarms reliably wakes us on schedule.
function ensureAlarm() {
  chrome.alarms.create("heartbeat", { periodInMinutes: INTERVAL_SEC / 60 });
}
chrome.runtime.onInstalled.addListener(ensureAlarm);
chrome.runtime.onStartup.addListener(ensureAlarm);

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "heartbeat") heartbeat();
});

// Also report immediately when you switch tabs/windows, for snappier data.
chrome.tabs.onActivated.addListener(() => heartbeat());
chrome.windows.onFocusChanged.addListener((winId) => {
  if (winId !== chrome.windows.WINDOW_ID_NONE) heartbeat();
});

async function heartbeat() {
  try {
    const state = await chrome.idle.queryState(IDLE_AFTER_SEC);
    if (state !== "active") return;

    const win = await chrome.windows.getLastFocused({ populate: false });
    if (!win || !win.focused) return;          // browser not in foreground

    const [tab] = await chrome.tabs.query({ active: true, windowId: win.id });
    if (!tab || !tab.url || !/^https?:/i.test(tab.url)) return;

    await fetch(SERVER, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: tab.url,
        title: tab.title || "",
        interval: INTERVAL_SEC,
      }),
    });
  } catch (e) {
    // Server offline or tab unavailable — ignore until next heartbeat.
  }
}
