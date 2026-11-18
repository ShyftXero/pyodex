// Boots Pyodide from the extension's local pyodide/ folder, installs the
// pyext runtime helper and the app's Python files into the virtual FS, then
// runs app/main.py.
import { loadPyodide } from "./pyodide/pyodide.mjs";

const status = document.getElementById("pyext-status");
const setStatus = (text, isError = false) => {
  if (!status) return;
  status.textContent = text;
  status.classList.toggle("error", isError);
};

async function fetchText(rel) {
  const r = await fetch(chrome.runtime.getURL(rel));
  if (!r.ok) throw new Error(`fetch ${rel}: ${r.status}`);
  return r.text();
}

async function boot() {
  const pyodide = await loadPyodide({ indexURL: chrome.runtime.getURL("pyodide/") });
  window.pyodide = pyodide;

  // Runtime helper importable as `pyext_runtime`.
  pyodide.FS.writeFile("/home/pyodide/pyext_runtime.py", await fetchText("pyext_runtime.py"));
  pyodide.FS.writeFile("/home/pyodide/pyext_natural.py", await fetchText("pyext_natural.py"));

  // App files -> /home/pyodide/app, on sys.path so `main.py` can import siblings.
  const files = JSON.parse(await fetchText("app/files.json"));
  pyodide.FS.mkdirTree("/home/pyodide/app");
  for (const rel of files) {
    if (rel === "files.json") continue;
    const parts = rel.split("/");
    if (parts.length > 1) pyodide.FS.mkdirTree("/home/pyodide/app/" + parts.slice(0, -1).join("/"));
    pyodide.FS.writeFile("/home/pyodide/app/" + rel, await fetchText("app/" + rel));
  }
  await pyodide.runPythonAsync(
    "import sys; sys.path[:0] = ['/home/pyodide', '/home/pyodide/app']"
  );

  // Bundled pure-Python wheels (HTTP shims + user packages). Installed into
  // site-packages via micropip-free path: unpack with pyodide.unpackArchive.
  const idx = JSON.parse(await fetchText("wheels/index.json"));
  if (idx.wheels.length) {
    setStatus("Installing packages…");
    // loadPackage accepts wheel URLs and handles wasm32 wheels with shared libs
    // (numpy etc.) as well as pure-Python ones.
    await pyodide.loadPackage(idx.wheels.map((w) => chrome.runtime.getURL("wheels/" + w)), {
      messageCallback: () => {},
    });
  }
  if (idx.http) {
    // urllib3>=2.2 has a native Emscripten backend, so `requests` works as-is.
    // pyodide-http patches urllib.request (XHR, synchronous).
    await pyodide.runPythonAsync("import pyodide_http; pyodide_http.patch_urllib()");
  }

  if (idx.natural) {
    // socket/ssl/input/sleep/persistent-fs/pip shims; each self-gates on detected capability.
    setStatus("Installing Python shims…");
    const caps = await pyodide.runPythonAsync("import pyext_natural, json; json.dumps(pyext_natural.install())");
    window.pyextCaps = JSON.parse(caps);
    console.debug("[pyext] capabilities", window.pyextCaps);
  }

  setStatus("Running main.py…");
  await pyodide.runPythonAsync(await fetchText("app/main.py"));
  status?.classList.add("hidden");
}

boot().catch((err) => {
  console.error("[pyext] boot error:", err);
  setStatus("Python error: " + (err?.message || err), true);
});
