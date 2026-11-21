// The shared engine: one Pyodide interpreter per browser, not per tab.
//
// This document is offscreen and long-lived, so sockets, caches and Python state
// survive tab churn. Tabs never load wasm; they ask the service worker, which
// routes here. DOM work stays in the tab (see docs/shared-engine.md) — this half
// is the interpreter, which is where the memory actually goes.
const BOOT_ID = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
let pyodidePromise = null;
let runs = 0;

async function engine() {
  if (!pyodidePromise) {
    pyodidePromise = (async () => {
      const { loadPyodide } = await import("./pyodide/pyodide.mjs");
      const py = await loadPyodide({ indexURL: chrome.runtime.getURL("pyodide/") });
      py.FS.writeFile("/home/pyodide/pyodex_runtime.py", await (await fetch(chrome.runtime.getURL("pyodex_runtime.py"))).text());
      py.FS.writeFile("/home/pyodide/pyodex_natural.py", await (await fetch(chrome.runtime.getURL("pyodex_natural.py"))).text());
      await py.runPythonAsync("import sys; sys.path[:0] = ['/home/pyodide']");
      globalThis.pyodide = py;
      return py;
    })();
  }
  return pyodidePromise;
}

async function handle(msg) {
  if (msg.kind === "status") {
    return { ok: true, bootId: BOOT_ID, runs, ready: !!pyodidePromise };
  }
  if (msg.kind === "attach") return dispatchAttach(msg.tabId, msg.url);
  if (msg.kind === "ui-event") return dispatchUiEvent(msg.tabId, msg.id, msg.event);
  if (msg.kind === "run") {
    const py = await engine();
    runs += 1;
    try {
      const value = await py.runPythonAsync(msg.code);
      return { ok: true, value: value === undefined ? null : String(value), bootId: BOOT_ID };
    } catch (e) {
      return { ok: false, error: String(e && e.message || e).slice(0, 800), bootId: BOOT_ID };
    }
  }
  return { ok: false, error: `unknown engine message: ${msg.kind}` };
}

// ---- per-tab sessions ---------------------------------------------------- //
// One interpreter, many tabs: the app's main.py runs once and receives an
// `on_attach` callback per tab, with a Page bound to that tab.
const tabs = new Map(); // tabId -> url
let appStarted = false;

async function startApp(py) {
  const code = await (await fetch(chrome.runtime.getURL("app/main.py"))).text();
  await py.runPythonAsync(code);
}

const pending = []; // tabs that attached before the app was ready

function recordError(where, e) {
  const text = `${where}: ${String(e && e.message || e).slice(0, 1200)}`;
  console.error("[pyodex]", text);
  // The offscreen document has no console a human will look at; persist instead.
  chrome.storage.local.set({ __pyodexEngineError: text }).catch(() => {});
}

async function castAttach(py, tabId, url) {
  await py.runPythonAsync(
    `import pyodex_runtime as _r; _r._engine_attached(${tabId}, ${JSON.stringify(url || "")})`
  );
}

async function startOnce() {
  const py = await engine();
  await startApp(py);
  while (pending.length) {
    const [tabId, url] = pending.shift();
    await castAttach(py, tabId, url);
  }
}

function dispatchAttach(tabId, url) {
  if (tabId == null) return Promise.resolve({ ok: false, error: "no tab id" });
  tabs.set(tabId, url);
  // Respond immediately: booting Pyodide takes seconds, and an async response
  // that outlives the message channel is dropped silently (we saw exactly that).
  if (!appStarted) {
    appStarted = true;
    pending.push([tabId, url]);
    startOnce().catch((e) => recordError("engine app", e));
  } else {
    engine()
      .then((py) => castAttach(py, tabId, url))
      .catch((e) => recordError("attach", e));
  }
  return Promise.resolve({ ok: true, bootId: BOOT_ID, pending: true });
}

globalThis.pyodexEngine = {
  bootId: BOOT_ID,
  currentTabId: () => [...tabs.keys()].pop() ?? null,
  tabOp: (tabId, op, args) =>
    chrome.runtime.sendMessage({ kind: "pyodex-tab-op", tabId, op, args: args ?? {} }),
  onUiEvent: (id) => {
    // Python registers a handler; the worker forwards clicks here.
    globalThis.__pyodexUiHandlers = globalThis.__pyodexUiHandlers || {};
    return id;
  },
};

async function dispatchUiEvent(tabId, id, event) {
  const py = await engine();
  try {
    await py.runPythonAsync(
      `import pyodex_runtime as _r; _r._engine_ui_event(${JSON.stringify(id)}, ${JSON.stringify(event)})`
    );
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e && e.message || e).slice(0, 600) };
  }
}

// The worker routes requests here. Plain messages, not ports: an offscreen
// document is reachable by sendMessage, and port lifecycles between SW and
// offscreen contexts are easy to get wrong (a port that silently never connects
// looks exactly like a slow engine).
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg?.pyodexEngineRequest) return false;
  handle(msg.request)
    .then(sendResponse)
    .catch((e) => sendResponse({ ok: false, error: String(e && e.message || e) }));
  return true; // async response
});

// Announce readiness so the worker can stop re-creating this document.
chrome.runtime.sendMessage({ kind: "pyodex-engine-ready", bootId: BOOT_ID }).catch(() => {});
