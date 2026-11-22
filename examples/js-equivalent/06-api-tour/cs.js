// Launcher + DOM agent. chrome.* lives in the extension page (tour.html), exactly as
// it does in the Python demo, so the comparison is architectural apples-to-apples.
(() => {
  const host = document.createElement("div");
  host.id = "js-tour-host";
  host.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647";
  const root = host.attachShadow({ mode: "open" });
  const css = document.createElement("style");
  css.textContent = `button{pointer-events:auto;position:fixed;top:16px;right:16px;padding:10px 16px;
    font:600 14px system-ui;color:#fff;background:#18181b;border:2px solid #f43f5e;border-radius:8px;
    cursor:pointer}`;
  root.append(css);
  const btn = document.createElement("button");
  btn.id = "tour-btn";
  btn.textContent = "Run tour";
  root.append(btn);
  document.documentElement.appendChild(host);

  let frame = null;
  const readyAt = performance.now();
  window.__jsTourReadyAt = readyAt;

  btn.addEventListener("click", () => {
    if (frame) return;
    frame = document.createElement("iframe");
    frame.src = chrome.runtime.getURL("tour.html");
    Object.assign(frame.style, {
      position: "fixed", inset: "0", width: "100vw", height: "100vh",
      border: "none", background: "transparent", zIndex: 2147483646,
    });
    document.documentElement.appendChild(frame);
  });

  // DOM operations requested by the extension page.
  window.addEventListener("message", (ev) => {
    if (!frame || ev.source !== frame.contentWindow) return;
    const msg = ev.data || {};
    if (msg.kind === "dom") {
      const input = document.querySelector("input[name=user]") || document.querySelector("input");
      let payload = {};
      if (input) {
        input.value = "tour@example.com";
        payload = {
          value: input.value,
          count: document.querySelectorAll("input").length,
          text: (document.querySelector("h1")?.textContent || "").slice(0, 30),
        };
        document.body.setAttribute("data-tour", "ran");
      }
      frame.contentWindow.postMessage({ kind: "dom-result", payload }, "*");
    } else if (msg.kind === "done") {
      document.documentElement.dataset.jsTour = JSON.stringify({ ok: msg.ok, ms: Math.round(msg.ms) });
    }
  });
})();
