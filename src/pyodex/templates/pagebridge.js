// ISOLATED-world bridge: relays the MAIN-world shim's questions to the service
// worker. MAIN-world code cannot touch chrome.* APIs; this is the seam.
(() => {
  window.addEventListener("message", async (ev) => {
    if (ev.source !== window) return;
    const msg = ev.data;
    if (!msg || msg.__pyodex !== true || msg.kind !== "pyodex-blob-request") return;

    let reply;
    try {
      const res = await chrome.runtime.sendMessage({ kind: "pyodex-blob", url: msg.url });
      reply = res && res.handled
        ? { handled: true, source: res.source, bytes: res.bytes }
        : { handled: false, reason: res?.reason || "unhandled" };
    } catch (e) {
      reply = { handled: false, reason: String(e && e.message) };
    }
    window.postMessage({ __pyodex: true, kind: "pyodex-blob-result", id: msg.id, ...reply }, "*");
  });
})();
