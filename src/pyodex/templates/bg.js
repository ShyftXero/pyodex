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

async function handleBlob(url) {
  const digest = await expectedDigest(url);
  if (!digest) return { handled: false, reason: "unverified" };
  const { data, source } = await cachedFetch(url, digest);
  return { handled: true, source, bytes: new Uint8Array(data), digest };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.kind === "pyodex-blob") {
    handleBlob(msg.url)
      .then(sendResponse)
      .catch((e) => sendResponse({ handled: false, reason: String(e && e.message) }));
    return true; // async response
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
