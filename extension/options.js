// Options page logic: shows the effective config, saves per-user overrides,
// tests the connection, and requests host permission for non-localhost servers.

const BUILTIN = { host: "localhost", port: 8777, interval: 30, enabled: true };
const $ = (id) => document.getElementById(id);

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

function isLocal(host) {
  return host === "localhost" || host === "127.0.0.1";
}
function msg(text, ok) {
  const m = $("msg");
  m.textContent = text;
  m.className = "msg " + (ok ? "ok" : "err");
}

async function load() {
  const team = await readDefaults();
  const managed = await readManaged();
  const { config: user } = await chrome.storage.local.get("config");
  const eff = { ...BUILTIN, ...team, ...clean(user || {}), ...managed };

  $("host").value = eff.host;
  $("port").value = eff.port;
  $("interval").value = eff.interval;
  $("enabled").checked = !!eff.enabled;

  if (Object.keys(team).length) {
    $("teamBanner").style.display = "block";
    $("teamBanner").textContent =
      `Team default server: ${team.host ?? BUILTIN.host}:${team.port ?? BUILTIN.port}. ` +
      `You can override it here for yourself.`;
  }
  if (Object.keys(managed).length) {
    msg("Some settings are enforced by your organization's policy and can't be changed.", true);
  }
}

async function ensurePermission(host) {
  if (isLocal(host)) return true;          // covered by manifest host_permissions
  const origin = `http://${host}/*`;
  const has = await chrome.permissions.contains({ origins: [origin] });
  if (has) return true;
  return chrome.permissions.request({ origins: [origin] });   // needs user gesture (button)
}

async function save() {
  const cfg = {
    host: $("host").value.trim() || "localhost",
    port: parseInt($("port").value, 10) || 8777,
    interval: Math.max(15, parseInt($("interval").value, 10) || 30),
    enabled: $("enabled").checked,
  };
  const granted = await ensurePermission(cfg.host);
  if (!granted) {
    msg(`Permission to reach http://${cfg.host} was denied — it won't be able to send data.`, false);
    return;
  }
  await chrome.storage.local.set({ config: cfg });
  msg("Saved.", true);
}

async function test() {
  const host = $("host").value.trim() || "localhost";
  const port = parseInt($("port").value, 10) || 8777;
  if (!(await ensurePermission(host))) {
    msg(`Permission to reach http://${host} was denied.`, false);
    return;
  }
  try {
    const r = await fetch(`http://${host}:${port}/api/days`, { method: "GET" });
    if (r.ok) msg(`✓ Connected to ${host}:${port}. The monitor is running.`, true);
    else msg(`Reached ${host}:${port} but it returned ${r.status}.`, false);
  } catch (e) {
    msg(`✗ Could not reach ${host}:${port}. Is the Activity Monitor running there?`, false);
  }
}

async function reset() {
  await chrome.storage.local.remove("config");
  await load();
  msg("Reset to team defaults.", true);
}

$("save").addEventListener("click", save);
$("test").addEventListener("click", test);
$("reset").addEventListener("click", reset);
load();
