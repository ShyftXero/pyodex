// Content script: injects a transparent, click-through overlay iframe onto the page.
(() => {
  const FRAME_ID = "pyodex-{{SLUG}}-frame";
  const URL_FILTER = {{URL_FILTER}};

  if (document.getElementById(FRAME_ID)) return;
  if (URL_FILTER && !window.location.href.toLowerCase().includes(URL_FILTER.toLowerCase())) return;
  if (window.top !== window) return; // top frame only

  const iframe = document.createElement("iframe");
  iframe.id = FRAME_ID;
  iframe.src = chrome.runtime.getURL("overlay.html");
  iframe.setAttribute("allowtransparency", "true");

  Object.assign(iframe.style, {
    position: "fixed",
    top: "0",
    left: "0",
    width: "100vw",
    height: "100vh",
    border: "none",
    zIndex: "2147483647",
    pointerEvents: "none",
    background: "transparent",
    colorScheme: "normal",
  });

  document.documentElement.appendChild(iframe);

  const MAX_NODES = 500;
  const desc = (el) => {
    const r = el.getBoundingClientRect();
    return {
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      cls: el.className && typeof el.className === "string" ? el.className : null,
      text: (el.innerText || el.textContent || "").trim().slice(0, 500),
      value: "value" in el ? String(el.value).slice(0, 500) : null,
      href: el.href || null,
      rect: { x: r.x, y: r.y, w: r.width, h: r.height },
      visible: !!(r.width || r.height),
    };
  };

  function runDomOp({ op, sel, value, index = 0, attr }) {
    const q = (s) => Array.from(document.querySelectorAll(s || "*"));
    switch (op) {
      case "query":
        return q(sel).slice(0, MAX_NODES).map(desc);
      case "count":
        return q(sel).length;
      case "html":
        return { html: q(sel).slice(0, MAX_NODES).map((e) => e.outerHTML.slice(0, 20000)) };
      case "text":
        return { text: q(sel).slice(0, MAX_NODES).map((e) => (e.innerText || e.textContent || "").trim()) };
      case "attr":
        return { values: q(sel).slice(0, MAX_NODES).map((e) => e.getAttribute(attr)) };
      case "set_text": {
        const el = q(sel)[index];
        if (!el) throw new Error(`no element for ${sel}[${index}]`);
        el.textContent = String(value);
        return { ok: true };
      }
      case "set_html": {
        const el = q(sel)[index];
        if (!el) throw new Error(`no element for ${sel}[${index}]`);
        el.innerHTML = String(value);
        return { ok: true };
      }
      case "set_attr": {
        const el = q(sel)[index];
        if (!el) throw new Error(`no element for ${sel}[${index}]`);
        if (value === null) el.removeAttribute(attr);
        else el.setAttribute(attr, String(value));
        return { ok: true };
      }
      case "set_value": {
        const el = q(sel)[index];
        if (!el) throw new Error(`no element for ${sel}[${index}]`);
        el.value = String(value);
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return { ok: true };
      }
      case "click": {
        const el = q(sel)[index];
        if (!el) throw new Error(`no element for ${sel}[${index}]`);
        el.click();
        return { ok: true };
      }
      case "scroll_to": {
        const el = q(sel)[index];
        if (!el) throw new Error(`no element for ${sel}[${index}]`);
        el.scrollIntoView({ block: "center", behavior: "smooth" });
        return { ok: true };
      }
      default:
        throw new Error(`unknown dom op ${op}`);
    }
  }

  const subscriptions = new Map();
  function subscribeEvents({ selector, events = ["click"], throttle_ms = 0 }) {
    for (const ev of events) {
      const key = selector + "|" + ev;
      if (subscriptions.has(key)) continue;
      let last = 0;
      const handler = (e) => {
        const now = Date.now();
        if (throttle_ms && now - last < throttle_ms) return;
        last = now;
        const el = e.target instanceof Element ? e.target : null;
        iframe.contentWindow.postMessage(
          { pyodex: true, type: "page.event", payload: { selector, event: ev, target: el ? desc(el) : null } },
          "*"
        );
      };
      document.addEventListener(ev, handler, true);
      subscriptions.set(key, handler);
    }
  }

  // Bridge: overlay <-> page. Overlay posts {pyodex: true, type, payload}; we relay
  // page info back on request so Python can read URL/title without DOM access.
  window.addEventListener("message", (ev) => {
    if (ev.source !== iframe.contentWindow) return;
    const msg = ev.data;
    if (!msg || msg.pyodex !== true) return;

    if (msg.type === "page.info") {
      iframe.contentWindow.postMessage(
        { pyodex: true, type: "page.info", id: msg.id, payload: { url: location.href, title: document.title } },
        "*"
      );
    } else if (msg.type === "page.dom") {
      // Explicit, small op set. No eval: page CSP applies as usual.
      let payload, error = null;
      try {
        payload = runDomOp(msg.payload || {});
      } catch (e) {
        error = String(e && e.message || e);
      }
      iframe.contentWindow.postMessage(
        { pyodex: true, type: "page.dom", id: msg.id, payload, error },
        "*"
      );
    } else if (msg.type === "page.events") {
      // Subscribe once per selector: page-side click/input -> overlay event stream.
      subscribeEvents(msg.payload || {});
    } else if (msg.type === "page.pointer") {
      // Let Python toggle whether the whole overlay captures clicks.
      iframe.style.pointerEvents = msg.payload ? "auto" : "none";
    } else if (msg.type === "page.remove") {
      iframe.remove();
    }
  });
})();
