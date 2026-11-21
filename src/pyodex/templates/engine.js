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

// The worker owns the lifecycle; it connects here once per routed request.
chrome.runtime.onConnect.addListener((port) => {
  port.onMessage.addListener(async (msg) => {
    port.postMessage(await handle(msg));
  });
});

// Announce readiness so the worker can stop re-creating this document.
chrome.runtime.sendMessage({ kind: "pyodex-engine-ready", bootId: BOOT_ID }).catch(() => {});
