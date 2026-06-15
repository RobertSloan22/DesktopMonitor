// Popup: show last send status, the current target server, and a quick toggle.

const BUILTIN = { host: "localhost", port: 8777, interval: 30, enabled: true };

function clean(o) {
  const r = {};
  for (const k of ["host", "port", "interval", "enabled"])
    if (o && o[k] !== undefined && o[k] !== null && o[k] !== "") r[k] = o[k];
  return r;
}
async function readDefaults() {
  try { return clean(await (await fetch(chrome.runtime.getURL("defaults.json"))).json()); }
  catch (e) { return {}; }
}
async function readManaged() {
  try { return clean(await chrome.storage.managed.get(["host", "port", "interval", "enabled"])); }
  catch (e) { return {}; }
}

async function effective() {
  const team = await readDefaults();
  const managed = await readManaged();
  const { config: user } = await chrome.storage.local.get("config");
  return { ...BUILTIN, ...team, ...clean(user || {}), ...managed };
}

async function render() {
  const cfg = await effective();
  const { status } = await chrome.storage.local.get("status");

  document.getElementById("enabled").checked = !!cfg.enabled;
  document.getElementById("target").innerHTML =
    `Reporting to <code>${cfg.host}:${cfg.port}</code> every ${cfg.interval}s`;

  const dot = document.getElementById("dot");
  const state = document.getElementById("state");
  const detail = document.getElementById("detail");

  if (!cfg.enabled) {
    dot.className = "dot"; state.textContent = "Paused";
    detail.textContent = "Tracking is turned off.";
  } else if (!status) {
    dot.className = "dot"; state.textContent = "Waiting…";
    detail.textContent = "No data sent yet. Browse a site for a moment.";
  } else {
    dot.className = "dot " + (status.ok ? "ok" : "err");
    state.textContent = status.ok ? "Connected" : "Not reaching server";
    detail.textContent = `${status.detail} · ${status.when}`;
  }
}

document.getElementById("enabled").addEventListener("change", async (e) => {
  const { config: user } = await chrome.storage.local.get("config");
  await chrome.storage.local.set({ config: { ...(user || {}), enabled: e.target.checked } });
  render();
});
document.getElementById("opts").addEventListener("click", () => chrome.runtime.openOptionsPage());

render();
