// Per-tab agent: a few hundred bytes of JS, no interpreter. Connects to the shared
// worker and relays DOM operations to the content script in this page.
(() => {
  const params = new URLSearchParams(location.search);
  const agentId = params.get("agent") || String(Math.random()).slice(2);
  const pageUrl = params.get("url") || "";

  const worker = new SharedWorker(chrome.runtime.getURL("engine_worker.js"));
  worker.port.start();

  const log = (t) => {
    const cur = document.body.dataset.log || "";
    document.body.dataset.log = (cur ? cur + " | " : "") + t;
  };

  // Widgets live in this agent's shadow root; everything else is a page DOM op and
  // is forwarded to the content script (which owns the page).
  let uiRootEl = null;
  function uiRoot() {
    if (uiRootEl) return uiRootEl;
    const host = document.createElement("div");
    host.id = "pyodex-agent-ui";
    host.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647";
    uiRootEl = host.attachShadow({ mode: "open" });
    const css = document.createElement("style");
    css.textContent = `button{pointer-events:auto;position:fixed;padding:10px 16px;font:600 14px system-ui;
      color:#fff;background:#18181b;border:2px solid #f43f5e;border-radius:8px;cursor:pointer}
      .card{pointer-events:auto;position:fixed;background:rgba(24,24,27,.95);color:#fff;border-radius:8px;
      overflow:hidden;font:13px/1.5 system-ui;max-width:300px}
      .card-head{padding:10px 12px;font-weight:600;background:rgba(244,63,94,.14);
      border-bottom:1px solid rgba(255,255,255,.08)}
      .card-body{padding:12px;white-space:pre-wrap}
      .badge{pointer-events:none;position:fixed;padding:4px 10px;border-radius:999px;
      font:600 12px system-ui;color:#fff}
      input{pointer-events:auto;display:block;width:100%;box-sizing:border-box;margin-top:8px;
      padding:8px 10px;border-radius:6px;border:1px solid #3f3f46;background:#18181b;color:#fff}`;
    uiRootEl.append(css);
    document.documentElement.appendChild(host);
    return uiRootEl;
  }

  function uiOp(op, a) {
    const r = uiRoot();
    if (op === "ui_button") {
      const b = document.createElement("button");
      b.id = a.id; b.textContent = a.label;
      b.style.top = (a.top ?? 16) + "px";
      if (a.right != null) b.style.right = a.right + "px"; else b.style.left = (a.left ?? 16) + "px";
      b.addEventListener("click", () => worker.port.postMessage({ kind: "ui-event", id: a.id, event: "click" }));
      r.append(b);
      return { ok: true };
    }
    if (op === "ui_badge") {
      const d = document.createElement("div");
      d.className = "badge"; d.textContent = a.text;
      d.style.top = (a.top ?? 16) + "px";
      d.style.background = a.color || "#f43f5e";
      if (a.right != null) d.style.right = a.right + "px"; else d.style.left = (a.left ?? 16) + "px";
      r.append(d);
      return { ok: true };
    }
    if (op === "ui_card") {
      let c = r.querySelector("#" + CSS.escape(a.id || "pyodex-card"));
      if (!c) {
        c = document.createElement("div");
        c.id = a.id || "pyodex-card"; c.className = "card";
        c.style.top = (a.top ?? 16) + "px"; c.style.left = (a.left ?? 16) + "px";
        c.style.width = (a.width ?? 300) + "px";
        c.append(Object.assign(document.createElement("div"), { className: "card-head" }),
                 Object.assign(document.createElement("div"), { className: "card-body" }));
        r.append(c);
      }
      if (a.title !== undefined) c.querySelector(".card-head").textContent = a.title;
      if (a.text !== undefined) c.querySelector(".card-body").textContent = String(a.text);
      return { ok: true };
    }
    if (op === "ui_input") {
      let i = r.querySelector("#" + CSS.escape(a.id || "pyodex-input"));
      if (!i) { i = document.createElement("input"); i.id = a.id || "pyodex-input";
                if (a.type) i.type = a.type; r.append(i); }
      if (a.placeholder) i.placeholder = a.placeholder;
      return { ok: true };
    }
    return null;
  }

  worker.port.onmessage = (ev) => {
    const msg = ev.data || {};
    if (msg.kind === "log") { log(msg.text); return; }
    if (msg.kind === "attach-reply") { log("attach:" + JSON.stringify(msg.reply).slice(0, 80)); return; }
    if (msg.kind === "error") {
      log("err:" + String(msg.error).slice(0, 80));
      window.parent.postMessage({ pyodex: true, kind: "agent-error", error: msg.error }, "*");
      return;
    }
    if (msg.kind === "dom") {
      if (String(msg.op).startsWith("ui_")) {
        let reply;
        try { reply = { ok: true, payload: uiOp(msg.op, msg.args || {}) }; }
        catch (e) { reply = { ok: false, error: String(e && e.message) }; }
        worker.port.postMessage({ kind: "dom-reply", id: msg.id, reply });
        return;
      }
      window.parent.postMessage({ pyodex: true, kind: "agent-dom", id: msg.id, op: msg.op, args: msg.args }, "*");
    }
    if (msg.kind === "ui-event") {
      // The worker dispatches to Python; nothing to do with the page here.
    }
  };

  window.addEventListener("message", (ev) => {
    const msg = ev.data;
    if (!msg || msg.pyodex !== true || msg.kind !== "agent-dom-reply") return;
    worker.port.postMessage({ kind: "dom-reply", id: msg.id, reply: msg.reply });
  });

  worker.port.postMessage({ kind: "attach", agentId, url: pageUrl });
})();
