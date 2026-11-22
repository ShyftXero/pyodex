// RustPython tier bootstrap (~5 MB).
//
// Between MicroPython and Pyodide: a wider stdlib than MicroPython, no CPython
// fidelity, and no DOM bridge of its own — JS values are handed in explicitly
// through `vars`, which is what pyodex_rp uses to reach the overlay.
import init, { pyEval } from "./rustpython/rustpython_wasm.js";

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

async function text(rel) {
  const r = await fetch(chrome.runtime.getURL(rel));
  if (!r.ok) throw new Error(`fetch ${rel}: ${r.status}`);
  return r.text();
}

async function boot() {
  setStatus("Loading RustPython…");
  await init(chrome.runtime.getURL("rustpython/rustpython_wasm_bg.wasm"));
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
  pyEval(await text("pyodex_rp.py"), { vars });
  pyEval(await text("app/main.py"), { vars, stdout: (line) => console.log("[rp]", line) });
  status?.classList.add("hidden");
}

boot().catch((e) => window.pyodexReportError(String((e && e.message) || e)));
