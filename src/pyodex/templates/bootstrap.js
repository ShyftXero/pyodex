// Boots Pyodide from the extension's local pyodide/ folder, installs the
// pyodex runtime helper and the app's Python files into the virtual FS, then
// runs app/main.py.
import * as pyodexCache from "./cache.js";
// NOTE: pyodide.mjs is imported lazily inside boot(). It captures a reference to
// fetch at import time, so the remote-runtime fetch interception must be
// installed *before* the module loads, or the big binaries go straight to the
// network (and 404, since they are intentionally not bundled).

const status = document.getElementById("pyodex-status");
const setStatus = (text, isError = false) => {
  if (!status) return;
  status.textContent = text;
  status.classList.toggle("error", isError);
};

// Full traceback panel. Python errors are kept verbatim; JS errors also show stack.
const DEV = {{DEV}};
const errPanel = DEV ? document.getElementById("pyodex-error") : null;
function showError(err, context = "error") {
  const text = err?.stack || err?.message || String(err);
  console.error(`[pyodex] ${context}:`, err);
  if (!errPanel) return;
  errPanel.replaceChildren();
  const head = document.createElement("div");
  head.className = "pyodex-err-head";
  head.append(Object.assign(document.createElement("span"), { textContent: `pyodex ${context}` }));
  const btns = document.createElement("div");
  for (const [label, fn] of [
    ["copy", () => navigator.clipboard?.writeText(text)],
    ["close", () => errPanel.classList.remove("show")],
  ]) {
    const b = document.createElement("button");
    b.textContent = label;
    b.addEventListener("click", fn);
    btns.append(b);
  }
  head.append(btns);
  errPanel.append(head, document.createTextNode(text));
  errPanel.classList.add("show");
  setStatus(`${context}: ${(err?.message || String(err)).split("\n")[0]}`, true);
}

async function fetchText(rel) {
  const r = await fetch(chrome.runtime.getURL(rel));
  if (!r.ok) throw new Error(`fetch ${rel}: ${r.status}`);
  return r.text();
}

// Tells the content script this overlay is still in use, so an idle tab can free
// the interpreter. Without this the page cannot know whether Python is active.
const heartbeat = () => {
  try {
    window.parent.postMessage({ pyodex: true, type: "page.heartbeat", payload: Date.now() }, "*");
  } catch {
    /* parent gone */
  }
};
for (const ev of ["pointerdown", "keydown", "focus"]) {
  window.addEventListener(ev, heartbeat, { capture: true, passive: true });
}

// Blob cache bridge: Python fetches through here so digests are enforced and
// per-site opt-outs (options page) are honoured in one place.
window.pyodexCache = {
  fetch: (url, digest) => pyodexCache.cachedFetch(url, digest),
  stats: pyodexCache.stats,
  clearAll: pyodexCache.clearAll,
  noCacheHosts: pyodexCache.noCacheHosts,
  setNoCacheHosts: pyodexCache.setNoCacheHosts,
};
window.pyodexCacheBytes = async (url, digest) => {
  const { data, source } = await pyodexCache.cachedFetch(url, digest);
  return { bytes: new Uint8Array(data), source };
};

// Python reports background-task failures here (see pyodex_runtime._task_done).
window.pyodexReportError = (msg) => showError(new Error(msg), "Python error");

const REMOTE_RUNTIME = {{REMOTE_RUNTIME}};

// With a remote runtime base, the big binaries (wasm + stdlib, ~16 MB) are not in
// the extension package. They cannot be imported as modules from the remote base
// (MV3 forbids remote code), so the loader stays local and only *asset* fetches
// are redirected through the blob cache: first load pays once, later loads are local.
async function remoteRuntime() {
  const spec = JSON.parse(await fetchText("runtime.json"));
  const localBase = chrome.runtime.getURL("pyodide/"); // loader stays bundled
  const fetchImpl = window.fetch;
  window.fetch = async (input, init) => {
    // The loader may pass a string, a URL, or a Request, and may use relative
    // paths — resolve first, then decide by filename.
    let name = null;
    try {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input?.url;
      if (raw) {
        const abs = new URL(raw, location.href);
        if (abs.protocol === "chrome-extension:") name = abs.pathname.split("/").pop();
      }
    } catch {
      /* fall through to the network */
    }
    const digest = name ? spec.files[name] : null;
    if (digest) {
      const url = `${spec.base}/${name}`;
      // The worker populates the cache; we read it back here. Never ship the
      // bytes through messaging — a 10 MB wasm overflows the 64 MiB limit.
      const res = await chrome.runtime.sendMessage({ kind: "pyodex-ensure", url, digest });
      if (!res || !res.ok) throw new Error(`runtime blob refused: ${res?.reason || "unknown"}`);
      const cache = await caches.open("pyodex-blobs-v1");
      const hit = await cache.match(url);
      if (!hit) throw new Error(`cache miss for ${name} after ensure`);
      window.pyodexRuntimeStats = { ...(window.pyodexRuntimeStats || {}), [name]: res.source };
      // WebAssembly.instantiateStreaming requires the wasm MIME type; the cached
      // response carries whatever the origin sent.
      return new Response(hit.body, {
        status: 200,
        headers: { "content-type": name.endsWith(".wasm") ? "application/wasm" : "application/octet-stream" },
      });
    }
    return fetchImpl.apply(window, [input, init]);
  };
  return localBase;
}

async function boot() {
  const indexURL = REMOTE_RUNTIME ? await remoteRuntime() : chrome.runtime.getURL("pyodide/");
  const { loadPyodide } = await import("./pyodide/pyodide.mjs");
  const pyodide = await loadPyodide({ indexURL });
  window.pyodide = pyodide;

  // Runtime helper importable as `pyodex_runtime`.
  pyodide.FS.writeFile("/home/pyodide/pyodex_runtime.py", await fetchText("pyodex_runtime.py"));
  pyodide.FS.writeFile("/home/pyodide/pyodex_natural.py", await fetchText("pyodex_natural.py"));

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
    const caps = await pyodide.runPythonAsync("import pyodex_natural, json; json.dumps(pyodex_natural.install())");
    window.pyodexCaps = JSON.parse(caps);
    console.debug("[pyodex] capabilities", window.pyodexCaps);
  }

  heartbeat();
  setInterval(heartbeat, 30000); // keeps a busy-but-quiet overlay from being reaped

  setStatus("Running main.py…");
  await pyodide.runPythonAsync(await fetchText("app/main.py"));

  // Declarations only *declare*: this calls the functions whose @watch_for /
  // @on_event match the page, and registers the page-side subscriptions.
  await pyodide.runPythonAsync(
    "import asyncio, pyodex_runtime; asyncio.ensure_future(pyodex_runtime.dispatch())"
  );
  status?.classList.add("hidden");
}

boot().catch((err) => {
  // Pyodide wraps Python exceptions in PythonError; its .message holds the traceback.
  const isPy = err && (err.type === "PythonError" || err.constructor?.name === "PythonError");
  showError(err, isPy ? "Python error" : "boot error");
});

// Async errors from app code (asyncio tasks, setTimeout) surface here too.
window.addEventListener("unhandledrejection", (e) => showError(e.reason, "unhandled rejection"));
window.addEventListener("error", (e) => showError(e.error || e.message, "uncaught error"));
