// Service worker: the cache authority for *any* page's Pyodide CDN request.
//
// A page asks for https://cdn.jsdelivr.net/pyodide/v314.0.8/full/pyodide.asm.wasm
// (for example). If this extension is installed, that request can be answered
// from local storage instead of the CDN — but only when the bytes are provably
// the ones that version expects. The expected digest comes from that version's
// pyodide-lock.json, which is the thing that makes "a real Pyodide version" a
// checkable claim rather than a guess.
import { cachedFetch, clearAll, noCacheHosts, setNoCacheHosts, stats } from "./cache.js";

const PYODIDE_URL = /\/pyodide\/v?(\d+\.\d+\.\d+)\/full\/([^/?#]+)$/;

async function expectedDigest(url) {
  const m = PYODIDE_URL.exec(url);
  if (!m) return null;
  const [, version, filename] = m;
  const lockUrl = url.replace(/full\/[^/?#]+$/, "full/pyodide-lock.json");
  try {
    const { data } = await cachedFetch(lockUrl, null);
    const lock = JSON.parse(new TextDecoder().decode(data));
    const pkg = Object.values(lock.packages || {}).find((p) => p.file_name === filename);
    return pkg?.sha256 ?? null;
  } catch {
    return null; // no lockfile -> cannot verify -> caller falls back to the network
  }
}

async function handleBlob(url, explicitDigest = null) {
  const digest = explicitDigest || (await expectedDigest(url));
  if (!digest) return { handled: false, reason: "unverified" };
  const { data, source } = await cachedFetch(url, digest);
  return { handled: true, source, bytes: new Uint8Array(data), digest };
}

// ---- shared engine ------------------------------------------------------- //
let engineReady = false;

async function ensureEngine() {
  // Idempotent: Chrome allows a single offscreen document per extension.
  try {
    const has = await chrome.offscreen.hasDocument();
    if (!has) {
      await chrome.offscreen.createDocument({
        url: "engine.html",
        reasons: ["WORKERS"],
        justification: "Host a single Pyodide interpreter shared by every tab.",
      });
    }
  } catch (e) {
    if (!String(e).includes("Only a single offscreen")) throw e;
  }
}

// Create the host eagerly. Doing it inside a message handler means the first
// request waits on document creation, and the response channel can close while
// an async create is in flight (the attach reply simply never arrives).
chrome.runtime.onInstalled.addListener(() => ensureEngine().catch(() => {}));
chrome.runtime.onStartup.addListener(() => ensureEngine().catch(() => {}));
ensureEngine().catch(() => {});

function askEngine(msg, timeoutMs = 20000) {
  // Plain messages, not ports: an offscreen document is reachable by
  // sendMessage, and a port that silently never connects looks exactly like a
  // slow engine (we lost an hour to that).
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => {
      if (!settled) {
        settled = true;
        resolve(v);
      }
    };
    setTimeout(() => done({ ok: false, error: "engine timeout" }), timeoutMs);
    // Envelope, not spread: spreading after `kind` would overwrite it with the
    // request's own kind, and the engine would drop every message.
    chrome.runtime.sendMessage({ pyodexEngineRequest: true, request: msg }).then(
      (reply) => done(reply || { ok: false, error: "empty engine reply" }),
      (err) => done({ ok: false, error: String(err && err.message) })
    );
  });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.kind === "pyodex-attach") {
    const tabId = sender.tab?.id ?? null;
    if (tabId != null) lastTabId = tabId;
    ensureEngine()
      .then(() => askEngine({ kind: "attach", tabId, url: msg.url }))
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, error: String(e && e.message) }));
    return true;
  }
  if (msg?.kind === "pyodex-ui-event") {
    askEngine({ kind: "ui-event", tabId: sender.tab?.id ?? lastTabId, id: msg.id, event: msg.event })
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, error: String(e && e.message) }));
    return true;
  }
  if (msg?.kind === "pyodex-tab-op") {
    // engine asks the worker to act on a specific tab
    chrome.tabs.sendMessage(msg.tabId, { kind: "pyodex-tab-op", op: msg.op, args: msg.args })
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, error: String(e && e.message) }));
    return true;
  }
  return false;
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.kind === "pyodex-blob") {
    handleBlob(msg.url, msg.digest)
      .then(sendResponse)
      .catch((e) => sendResponse({ handled: false, reason: String(e && e.message) }));
    return true; // async response
  }
  if (msg?.kind === "pyodex-ensure") {
    // Populate the cache and report where the bytes came from. The page then
    // reads Cache Storage directly: shipping a 10 MB wasm through sendMessage
    // blows past the 64 MiB message limit once structured-clone copies are made.
    handleBlob(msg.url, msg.digest)
      .then((r) => sendResponse({ ok: r.handled, source: r.source, reason: r.reason }))
      .catch((e) => sendResponse({ ok: false, reason: String(e && e.message) }));
    return true;
  }
  if (msg?.kind === "pyodex-engine-ready") {
    engineReady = true;
    sendResponse({ ok: true });
    return false;
  }
  if (msg?.kind === "pyodex-shared") {
    ensureEngine()
      .then(() => askEngine({ kind: msg.action || "run", code: msg.code }))
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, error: String(e && e.message) }));
    return true;
  }
  if (msg?.kind === "pyodex-engine-status") {
    askEngine({ kind: "status" }, 8000).then(sendResponse);
    return true;
  }
  if (msg?.kind === "pyodex-stats") {
    sendResponse({ stats, hosts: null });
    return false;
  }
  if (msg?.kind === "pyodex-clear") {
    clearAll().then((n) => sendResponse({ cleared: n }));
    return true;
  }
  if (msg?.kind === "pyodex-hosts") {
    noCacheHosts().then((hosts) => sendResponse({ hosts }));
    return true;
  }
  if (msg?.kind === "pyodex-set-hosts") {
    setNoCacheHosts(msg.hosts).then((hosts) => sendResponse({ hosts }));
    return true;
  }
  return false;
});
