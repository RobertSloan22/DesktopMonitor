// Activity Monitor — Web Tracker (Chrome / Edge / Brave, Manifest V3)
//
// Every ~interval seconds, if a browser window is focused and you're not idle,
// this reports the active tab's URL + title to the Activity Monitor server.
//
// Configuration precedence (later wins):
//   built-in fallback  <  defaults.json (team)  <  user options  <  managed policy
// So a team lead sets defaults.json, individuals can tweak via the options page,
// and an IT admin can hard-enforce settings via managed storage (optional).

const BUILTIN = { host: "localhost", port: 8777, interval: 30, enabled: true };

async function readDefaultsJson() {
  try {
    const r = await fetch(chrome.runtime.getURL("defaults.json"));
    const d = await r.json();
    return {
      host: d.host, port: d.port, interval: d.interval, enabled: d.enabled,
    };
  } catch (e) {
    return {};
  }
}

async function readManaged() {
  try {
    return (await chrome.storage.managed.get(
      ["host", "port", "interval", "enabled"])) || {};
  } catch (e) {
    return {};
  }
}

function clean(obj) {
  // drop undefined keys so they don't clobber lower-precedence values
  const o = {};
  for (const k of ["host", "port", "interval", "enabled"]) {
    if (obj[k] !== undefined && obj[k] !== null && obj[k] !== "") o[k] = obj[k];
  }
  return o;
}

async function getConfig() {
  const team = clean(await readDefaultsJson());
  const { config: user } = await chrome.storage.local.get("config");
  const managed = clean(await readManaged());
  const cfg = { ...BUILTIN, ...team, ...clean(user || {}), ...managed };
  cfg.port = parseInt(cfg.port, 10) || 8777;
  cfg.interval = Math.max(15, parseInt(cfg.interval, 10) || 30);
  return cfg;
}

function serverUrl(c) {
  return `http://${c.host}:${c.port}/api/browser`;
}

async function setupAlarm() {
  const c = await getConfig();
  await chrome.alarms.clear("heartbeat");
  if (c.enabled) {
    // Chrome enforces a ~30s minimum in production; dev allows lower (with a warning).
    chrome.alarms.create("heartbeat", { periodInMinutes: c.interval / 60 });
  }
}

chrome.runtime.onInstalled.addListener(async (details) => {
  await setupAlarm();
  if (details.reason === "install") {
    chrome.runtime.openOptionsPage();   // let the user confirm/configure on install
  }
});
chrome.runtime.onStartup.addListener(setupAlarm);

// React to option changes immediately (re-arm the alarm with the new interval).
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.config) setupAlarm();
  if (area === "managed") setupAlarm();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "heartbeat") heartbeat();
});

// Snappier data: also report immediately on tab/window switches.
chrome.tabs.onActivated.addListener(() => heartbeat());
chrome.windows.onFocusChanged.addListener((winId) => {
  if (winId !== chrome.windows.WINDOW_ID_NONE) heartbeat();
});

async function setStatus(ok, detail) {
  await chrome.storage.local.set({
    status: { ok, detail, when: new Date().toLocaleTimeString() },
  });
}

async function heartbeat() {
  const c = await getConfig();
  if (!c.enabled) return;
  try {
    const state = await chrome.idle.queryState(60);
    if (state !== "active") return;

    const win = await chrome.windows.getLastFocused({ populate: false });
    if (!win || !win.focused) return;

    const [tab] = await chrome.tabs.query({ active: true, windowId: win.id });
    if (!tab || !tab.url || !/^https?:/i.test(tab.url)) return;

    const res = await fetch(serverUrl(c), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: tab.url, title: tab.title || "", interval: c.interval }),
    });
    await setStatus(res.ok, res.ok ? `sent to ${c.host}:${c.port}` : `server returned ${res.status}`);
  } catch (e) {
    await setStatus(false, `cannot reach ${c.host}:${c.port} (is the monitor running?)`);
  }
}
