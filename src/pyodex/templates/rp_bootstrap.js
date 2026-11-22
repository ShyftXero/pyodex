// RustPython tier bootstrap (~5 MB).
//
// Between MicroPython and Pyodide: a wider stdlib than MicroPython, no CPython
// fidelity, and no DOM bridge of its own — JS values are handed in explicitly
// through `vars`, which is what pyodex_rp uses to reach the overlay.
import * as rp from "./rustpython/rustpython_wasm.js";

// The package has no glue module (it expects wasm ESM integration), and the wasm
// imports its helpers from this very module — so we instantiate it here and hand
// the instance back.
async function bootWasm() {
  const bytes = await (await fetch(url("rustpython/rustpython_wasm_bg.wasm"))).arrayBuffer();
  const mod = await WebAssembly.compile(bytes);
  const imports = {};
  for (const imp of WebAssembly.Module.imports(mod)) {
    imports[imp.module] = imports[imp.module] || {};
    const fn = rp[imp.name];
    if (typeof fn !== "function") throw new Error(`missing wasm import ${imp.name}`);
    imports[imp.module][imp.name] = fn;
  }
  // With a pre-compiled Module this resolves to an Instance, not {instance}.
  const instance = await WebAssembly.instantiate(mod, imports);
  rp.__pyodex_setWasm(instance.exports);
  rp.__pyodex_start();
}

const status = document.getElementById("pyodex-status");
const errPanel = document.getElementById("pyodex-error");

const setStatus = (text, isError = false) => {
  if (!status) return;
  status.textContent = String(text);
  status.classList.toggle("error", !!isError);
};

window.pyodexReportError = (msg) => {
  console.error("[pyodex]", msg);
  if (errPanel) {
    errPanel.textContent = String(msg);
    errPanel.classList.add("show");
  }
  setStatus(String(msg).slice(0, 120), true);
};

// Sandboxed pages have no chrome.* APIs, so resolve against our own URL instead.
const url = (rel) =>
  typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.getURL
    ? chrome.runtime.getURL(rel)
    : new URL(rel, location.href).href;

async function text(rel) {
  const r = await fetch(url(rel));
  if (!r.ok) throw new Error(`fetch ${rel}: ${r.status}`);
  return r.text();
}

async function boot() {
  setStatus("Loading RustPython…");
  await bootWasm();
  window.pyodexCaps = { runtime: "rustpython", dom: false, packages: false, jspi: false };

  const vars = {
    // The only surface RustPython gets: enough to paint a status line and read
    // the document through explicit calls. No proxies, by design.
    set_status: (msg) => setStatus(msg),
    query_count: (sel) => document.querySelectorAll(String(sel)).length,
    element_text: (sel) => {
      const el = document.querySelector(String(sel));
      return el ? el.textContent : "";
    },
    set_attr: (sel, name, value) => {
      const el = document.querySelector(String(sel));
      if (el) el.setAttribute(String(name), String(value));
      return !!el;
    },
  };

  setStatus("Running main.py…");
  // One compilation unit: RustPython's pyEval has no module loader for us, so the
  // shim is concatenated ahead of the app.
  const source = (await text("pyodex_rp.py")) + "\n" + (await text("app/main.py"));
  try {
    rp.pyEval(source, { vars, stdout: (line) => console.log("[rp]", line) });
  } catch (e) {
    const msg = String((e && e.message) || e);
    const missing = /No module named '([^']+)'/.exec(msg);
    if (missing) {
      throw new Error(
        `RustPython tier: module '${missing[1]}' does not exist here. ` +
          "This tier has no `js` module and no `pyodex_runtime`; use the `pyodex_rp` " +
          "helpers (status, count, text_of, set_attr) or switch to runtime = \"pyodide\"."
      );
    }
    throw e;
  }
  status?.classList.add("hidden");
}

boot().catch((e) => window.pyodexReportError(String((e && e.message) || e)));
