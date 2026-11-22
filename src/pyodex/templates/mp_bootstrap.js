// MicroPython tier bootstrap.
//
// ~2 MB instead of ~55 MB, so this is the tier that can afford to be present in
// every tab. It is not CPython: no third-party packages, a subset of the stdlib,
// and its own `js` bridge. pyodex_mp.py provides the small API surface an
// in-page tier needs (status, elements, events).
import { loadMicroPython } from "./micropython/micropython.mjs";

const status = document.getElementById("pyodex-status");
const setStatus = (text, isError = false) => {
  if (!status) return;
  status.textContent = text;
  status.classList.toggle("error", isError);
};
const errPanel = document.getElementById("pyodex-error");
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
  setStatus("Loading MicroPython…");
  const mp = await loadMicroPython();
  window.micropython = mp;
  window.pyodexCaps = { runtime: "micropython", dom: true, packages: false, jspi: false };

  // The shim must exist as a *module*: executing it as stdin defines nothing that
  // `import pyodex_mp` can find.
  const shim = await text("pyodex_mp.py");
  if (mp.FS && mp.FS.writeFile) {
    mp.FS.writeFile("/pyodex_mp.py", shim, { encoding: "utf8" });
  } else {
    throw new Error("MicroPython build exposes no FS; cannot install pyodex_mp");
  }
  setStatus("Running main.py…");
  await mp.runPythonAsync(await text("app/main.py"));
  status?.classList.add("hidden");
}

boot().catch((e) => window.pyodexReportError(String((e && e.message) || e)));
