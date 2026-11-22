// Per-tab agent: a few hundred bytes of JS, no interpreter. Connects to the shared
// worker and relays DOM operations to the content script in this page.
(() => {
  const params = new URLSearchParams(location.search);
  const agentId = params.get("agent") || String(Math.random()).slice(2);
  const pageUrl = params.get("url") || "";

  const worker = new SharedWorker(chrome.runtime.getURL("engine_worker.js"));
  worker.port.start();

  worker.port.onmessage = (ev) => {
    const msg = ev.data || {};
    if (msg.kind === "error") {
      window.parent.postMessage({ pyodex: true, kind: "agent-error", error: msg.error }, "*");
      return;
    }
    if (msg.kind === "dom") {
      window.parent.postMessage({ pyodex: true, kind: "agent-dom", id: msg.id, op: msg.op, args: msg.args }, "*");
    }
  };

  window.addEventListener("message", (ev) => {
    const msg = ev.data;
    if (!msg || msg.pyodex !== true || msg.kind !== "agent-dom-reply") return;
    worker.port.postMessage({ kind: "dom-reply", id: msg.id, reply: msg.reply });
  });

  worker.port.postMessage({ kind: "attach", agentId, url: pageUrl });
})();
