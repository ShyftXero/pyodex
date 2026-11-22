// Shared worker: ONE Pyodide for every tab.
//
// Chosen over an offscreen document because (a) a shared worker is genuinely one
// instance for the whole browser and (b) it is observable — offscreen documents
// are not even executed by the Chromium the harness runs on.
//
// A worker cannot talk to content scripts, so each tab contributes a tiny agent
// iframe that relays DOM operations over its port.

// NOTE: a SharedWorker of an extension origin has NO chrome.* APIs (verified:
// typeof chrome === "undefined"). Everything here resolves against the worker's
// own location instead, and errors are reported over the port, not to storage.
const here = new URL(".", self.location.href).href;
const asset = (rel) => new URL(rel, here).href;

let pyodidePromise = null;
let appStarted = false;
const ports = new Map(); // agentId -> port
const pending = new Map(); // requestId -> resolve
let seq = 0;

async function engine() {
  if (!pyodidePromise) {
    pyodidePromise = (async () => {
      const { loadPyodide } = await import("./pyodide/pyodide.mjs");
      const py = await loadPyodide({ indexURL: asset("pyodide/") });
      const read = async (p) => (await fetch(asset(p))).text();
      py.FS.writeFile("/home/pyodide/pyodex_runtime.py", await read("pyodex_runtime.py"));
      py.FS.writeFile("/home/pyodide/pyodex_natural.py", await read("pyodex_natural.py"));
      await py.runPythonAsync("import sys; sys.path[:0] = ['/home/pyodide']");
      globalThis.pyodide = py;
      return py;
    })();
  }
  return pyodidePromise;
}

function tabOp(agentId, op, args) {
  const port = ports.get(agentId);
  if (!port) return Promise.resolve({ ok: false, error: "no agent" });
  const id = ++seq;
  return new Promise((resolve) => {
    pending.set(id, resolve);
    port.postMessage({ kind: "dom", id, op, args: args || {} });
    setTimeout(() => {
      if (pending.delete(id)) resolve({ ok: false, error: "dom timeout" });
    }, 20000);
  });
}

globalThis.pyodexEngine = {
  currentTabId: () => [...ports.keys()].pop() ?? null,
  tabOp,
};

async function attach(agentId, url) {
  const py = await engine();
  if (!appStarted) {
    appStarted = true;
    const code = await (await fetch(asset("app/main.py"))).text();
    await py.runPythonAsync(code);
  }
  try {
    await py.runPythonAsync(
      `import pyodex_runtime as _r; _r._engine_attached(${JSON.stringify(agentId)}, ${JSON.stringify(url || "")})`
    );
    return { ok: true };
  } catch (e) {
    const err = String((e && e.message) || e).slice(0, 400);
    for (const p of ports.values()) p.postMessage({ kind: "error", error: err });
    return { ok: false, error: err };
  }
}

onconnect = (event) => {
  const port = event.ports[0];
  port.onmessage = async (ev) => {
    const msg = ev.data || {};
    if (msg.kind === "attach") {
      ports.set(msg.agentId, port);
      port.postMessage({ kind: "attach-reply", reply: await attach(msg.agentId, msg.url) });
      return;
    }
    if (msg.kind === "dom-reply") {
      const resolve = pending.get(msg.id);
      if (resolve) {
        pending.delete(msg.id);
        resolve(msg.reply || { ok: false, error: "empty reply" });
      }
    }
  };
};
