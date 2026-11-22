// Brython tier bootstrap (~2 MB, no wasm).
//
// Python implemented in JavaScript: real DOM access, no glue module, no
// ESM-integration flag. That is why this is the third tier that boots today while
// the RustPython wasm artifact does not.
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

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error(`failed to load ${src}`));
    document.head.appendChild(s);
  });
}

async function boot() {
  setStatus("Loading Brython…");
  await loadScript(chrome.runtime.getURL("brython/brython.min.js"));
  window.pyodexCaps = { runtime: "brython", dom: true, packages: false, jspi: false };

  // Brython compiles <script type="text/python"> on demand; the shim is prepended
  // so `import pyodex_br` is unnecessary and works without a stdlib bundle.
  const code = (await text("pyodex_br.py")) + "\n" + (await text("app/main.py"));
  const script = document.createElement("script");
  script.type = "text/python";
  script.textContent = code;
  document.body.appendChild(script);

  setStatus("Running main.py…");
  window.brython({ debug: 0 });
  status?.classList.add("hidden");
}

boot().catch((e) => window.pyodexReportError(String((e && e.message) || e)));
