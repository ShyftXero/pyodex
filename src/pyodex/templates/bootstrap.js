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

// --------------------------------------------------------------------------- //
// Markup bindings: <form pyodex-submit.prevent="connect">
//
// The attribute is data, never code. MV3 forbids inline JavaScript and refuses to
// relax that policy, so an `onclick` can never run; but a name resolved against the
// Python handler registry can. Modifiers are the vocabulary Alpine and Livewire
// already use (.prevent .stop .once .self .window) so there is nothing new to learn.
// --------------------------------------------------------------------------- //
const MODIFIERS = new Set(["prevent", "stop", "once", "self", "window"]);

function markupBindings(el) {
  const found = [];
  for (const attr of Array.from(el.attributes)) {
    if (!attr.name.startsWith("pyodex-")) continue;
    const [event, ...mods] = attr.name.slice("pyodex-".length).split(".");
    const name = (attr.value || "").trim();
    if (event && name) found.push({ event, mods, name });
  }
  return found;
}

async function bindMarkup(pyodide) {
  let api, names;
  try {
    api = pyodide.globals.get("sys").modules.get("pyodex");
    if (!api || !api._dispatch) return;
    names = new Set(JSON.parse(api._handler_names()));
  } catch (err) {
    console.warn("[pyodex] markup bindings skipped:", err);
    return;
  }
  const dispatch = api._dispatch;
  const bound = new WeakSet();

  const bindOne = (el) => {
    if (bound.has(el)) return;
    bound.add(el);
    for (const { event, mods, name } of markupBindings(el)) {
      const flags = new Set(mods);
      for (const flag of flags) {
        if (!MODIFIERS.has(flag)) console.warn(`[pyodex] unknown modifier .${flag} on ${event}`);
      }
      // The build checks this too; the fallback covers markup rendered at runtime.
      if (!names.has(name)) {
        console.warn(`[pyodex] no handler named "${name}" for ${event} — declare @handler in Python`);
        continue;
      }
      const listener = (e) => {
        if (flags.has("self") && e.target !== el) return;
        if (flags.has("prevent")) e.preventDefault();
        if (flags.has("stop")) e.stopPropagation();
        dispatch(name, e);
      };
      (flags.has("window") ? window : el).addEventListener(event, listener, {
        once: flags.has("once"),
      });
    }
  };

  const scan = (root) => {
    if (!root || root.nodeType !== 1) return;
    bindOne(root);
    for (const el of root.querySelectorAll("*")) bindOne(el);
  };

  scan(document.body);
  // Markup a plugin renders later takes the same path.
  new MutationObserver((records) => {
    for (const record of records) for (const node of record.addedNodes) scan(node);
  }).observe(document.body, { childList: true, subtree: true });
}

// MutationObserver fires as a microtask. If that microtask runs while Python is
// executing, calling back into Python re-enters the interpreter and stalls boot.
// Deferring to a timer moves the call to a point where Python is idle.
window.pyodexDefer = (fn) => {
  let batch = [];
  let queued = false;
  return (records) => {
    // MutationObserver empties its own queue before calling us, so the records
    // have to be carried across the timer by hand.
    if (records && records.length) for (const record of records) batch.push(record);
    if (queued) return;
    queued = true;
    setTimeout(() => {
      const deliver = batch;
      batch = [];
      queued = false;
      try {
        fn(deliver);
      } catch (err) {
        console.error("[pyodex] deferred callback failed:", err);
      }
    }, 0);
  };
};

const extensionRuntime = globalThis.chrome?.runtime;
const assetURL = (rel) => new URL(rel, import.meta.url).href;
const bundledAssets = new Set({{BUNDLED_ASSETS}});
let workerPolicy;

function bundledAssetURL(rel) {
  if (!bundledAssets.has(rel) || !/^[\w./-]+$/.test(rel) ||
      rel.split("/").some((part) => !part || part === "." || part === "..")) {
    throw new Error(`Declare a package-relative file in [extension].assets: ${rel}`);
  }
  return assetURL(rel);
}

function bundledModuleURL(rel) {
  if (!/\.m?js$/.test(rel)) throw new Error(`Expected a bundled JavaScript module: ${rel}`);
  return bundledAssetURL(rel);
}

function workerScriptURL(rel) {
  if (!globalThis.trustedTypes) return bundledModuleURL(rel);
  workerPolicy ??= trustedTypes.createPolicy("pyodex-packaged-worker", {
    createScriptURL: bundledModuleURL,
  });
  return workerPolicy.createScriptURL(rel);
}

async function fetchText(rel) {
  const r = await fetch(assetURL(rel));
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
  const indexURL = REMOTE_RUNTIME ? await remoteRuntime() : assetURL("pyodide/");
  const { loadPyodide } = await import("./pyodide/pyodide.mjs");
  const pyodide = await loadPyodide({ indexURL });
  window.pyodide = pyodide;

  // The app-facing module is `pyodex`; keep the original runtime name for older apps.
  for (const file of ["pyodex.py", "pyodex_runtime.py", "pyodex_natural.py"]) {
    pyodide.FS.writeFile("/home/pyodide/" + file, await fetchText(file));
  }

  pyodide.registerJsModule("_pyodex_assets", {
    assetURL: bundledAssetURL,
    importModule: (rel) => import(bundledModuleURL(rel)),
    workerScriptURL,
  });

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
    await pyodide.loadPackage(idx.wheels.map((w) => assetURL("wheels/" + w)), {
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

  // Inferred rather than injected: MV3's CSP forbids inline <script>, so the popup
  // page cannot set a flag that way.
  const entry = window.__PYODEX_ENTRY__ || (
    location.pathname.endsWith("/popup.html") ? "popup.py" :
    location.pathname.endsWith("/sidepanel.html") ? "panel.py" : "main.py");
  setStatus(`Running ${entry}…`);
  await pyodide.runPythonAsync(await fetchText("app/" + entry));
  await pyodide.runPythonAsync(`
import sys as _pyodex_sys
_pyodex_api = _pyodex_sys.modules.get("pyodex")
if _pyodex_api is not None:
    await _pyodex_api._run_start()
`);

  await bindMarkup(pyodide);
  // Readiness, for tooling and tests: Python is loaded, handlers are bound. Static
  // markup can exist long before this, so "the page has elements" proves nothing.
  document.documentElement.dataset.pyodexReady = "1";

  // Page declarations need the extension's content-script bridge. An IWA
  // runs standalone, with no host tab or extension runtime.
  if (extensionRuntime?.getURL) {
    await pyodide.runPythonAsync(
      "import asyncio, pyodex_runtime; asyncio.ensure_future(pyodex_runtime.dispatch())"
    );
  }
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
