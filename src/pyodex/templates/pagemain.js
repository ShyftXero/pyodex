// MAIN-world shim: answers Pyodide CDN fetches from the extension's cache.
//
// Runs at document_start so Pyodide's loader sees a patched fetch. Only URLs on
// the configured hosts are considered; everything else goes straight through.
// A miss or a refusal silently falls back to the original fetch, so a page never
// breaks because of us — worst case it behaves exactly as it would have.
(() => {
  const HOSTS = {{PAGE_CACHE_HOSTS}};
  if (!HOSTS.length) return;
  if (window.__pyodexCached) return;
  window.__pyodexCached = true;

  const original = window.fetch;
  const pending = new Map();
  let seq = 0;

  window.addEventListener("message", (ev) => {
    if (ev.source !== window) return;
    const msg = ev.data;
    if (!msg || msg.__pyodex !== true || msg.kind !== "pyodex-blob-result") return;
    const resolve = pending.get(msg.id);
    if (!resolve) return;
    pending.delete(msg.id);
    resolve(msg);
  });

  function ask(url) {
    return new Promise((resolve) => {
      const id = ++seq;
      pending.set(id, resolve);
      window.postMessage({ __pyodex: true, kind: "pyodex-blob-request", id, url }, "*");
      setTimeout(() => {
        if (pending.delete(id)) resolve({ handled: false, reason: "timeout" });
      }, 8000);
    });
  }

  window.fetch = async function (input, init) {
    let url = null;
    try {
      url = typeof input === "string" ? input : input instanceof URL ? input.href : input?.url;
    } catch {
      /* ignore */
    }
    if (url) {
      let host = null;
      let absolute = url;
      try {
        const parsed = new URL(url, location.href);
        host = parsed.hostname;
        absolute = parsed.href; // the worker has no document base to resolve against
      } catch {
        /* ignore */
      }
      const candidate = host && HOSTS.some((h) => (h.startsWith("*.") ? host.endsWith(h.slice(1)) : host === h));
      if (candidate) {
        const res = await ask(absolute);
        if (res && res.handled && res.bytes) {
          return new Response(res.bytes, {
            status: 200,
            headers: { "content-type": "application/wasm", "x-pyodex-source": res.source || "cache" },
          });
        }
      }
    }
    return original.apply(this, arguments);
  };
})();
