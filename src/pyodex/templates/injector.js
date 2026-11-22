// Content script: puts a launcher button on matching pages and boots the
// Python overlay only when it is clicked.
//
// With 40+ tabs open, booting Pyodide in every matching tab is the wrong
// default: each tab would pay a wasm compile plus its own heap (tens of MB).
// Idle tabs therefore cost nothing here — no iframe, no interpreter — and the
// interpreter is released again after `idle_timeout` seconds without activity.
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

(() => {
  if ({{ENGINE_UI}}) return; // engine mode: no iframe, no per-tab interpreter
  const FRAME_ID = "pyodex-{{SLUG}}-frame";
  const LAUNCHER_ID = "pyodex-{{SLUG}}-launcher";
  const URL_FILTER = {{URL_FILTER}};
  const CEILING = {{BOOT_MODE}};      // "user" | "visible" | "auto" (config ceiling)
  const IDLE_MS = {{IDLE_TIMEOUT_MS}};
  const REAP_HIDDEN_MS = {{REAP_HIDDEN_MS}};
  const MAX_LIVE = {{MAX_LIVE}};
  const TRIGGERS = {{TRIGGERS}};   // declarative boot conditions: [{url?, selector?}]
  const LABEL = {{LABEL}};
  const RANK = { user: 0, visible: 1, auto: 2 };

  if (document.getElementById(FRAME_ID) || document.getElementById(LAUNCHER_ID)) return;
  if (URL_FILTER && !window.location.href.toLowerCase().includes(URL_FILTER.toLowerCase())) return;
  if (window.top !== window) return; // top frame only

  let iframe = null;
  let idleTimer = null;
  let hiddenTimer = null;
  let host = null;
  let btn = null;
  let mode = CEILING;
  const pageKey = Math.random().toString(36).slice(2);

  // ---- interpreter budget ---------------------------------------------- //
  // Best-effort lease in storage.session: an "auto"/"visible" build cannot
  // quietly hold an interpreter in every one of 40 tabs.
  async function budgetOk() {
    if (!MAX_LIVE) return true;
    try {
      const { pyodexLive = {} } = await chrome.storage.session.get("pyodexLive");
      const now = Date.now();
      for (const k of Object.keys(pyodexLive)) {
        if (now - pyodexLive[k] > 900000) delete pyodexLive[k]; // stale lease
      }
      await chrome.storage.session.set({ pyodexLive });
      return Object.keys(pyodexLive).length < MAX_LIVE;
    } catch {
      return true; // storage.session unavailable: never block the app
    }
  }

  async function claimSlot() {
    if (!MAX_LIVE) return;
    try {
      const { pyodexLive = {} } = await chrome.storage.session.get("pyodexLive");
      pyodexLive[pageKey] = Date.now();
      await chrome.storage.session.set({ pyodexLive });
    } catch { /* best effort */ }
  }

  function releaseSlot() {
    if (!MAX_LIVE) return;
    chrome.storage.session
      .get("pyodexLive")
      .then(({ pyodexLive = {} }) => {
        delete pyodexLive[pageKey];
        return chrome.storage.session.set({ pyodexLive });
      })
      .catch(() => {});
  }

  // ---- launcher -------------------------------------------------------- //
  function makeLauncher() {
    host = document.createElement("div");
    host.id = LAUNCHER_ID;
    // Shadow DOM: page CSS cannot restyle or break the button. "open" keeps it
    // inspectable for DevTools and the Chrome test harness; isolation is the same.
    const root = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = `
      :host { all: initial; }
      .wrap { position: fixed; top: 16px; left: 16px; z-index: 2147483647; }
      button {
        pointer-events: auto; cursor: pointer;
        padding: 10px 16px; font: 600 14px system-ui, -apple-system, sans-serif;
        color: #fff; background: #18181b; border: 2px solid #f43f5e; border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0,0,0,.25); transition: transform .1s ease, background .2s ease;
      }
      button:hover:not(:disabled) { background: #27272a; transform: scale(1.03); }
      button:disabled { opacity: .7; cursor: wait; }
    `;
    const wrap = document.createElement("div");
    wrap.className = "wrap";
    btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = LABEL;
    btn.addEventListener("click", onActivate);
    wrap.append(btn);
    root.append(style, wrap);
    document.documentElement.appendChild(host);
  }

  function showLauncher() {
    if (!host) makeLauncher();
    host.style.display = "";
    btn.disabled = false;
    btn.textContent = LABEL;
  }

  function hideLauncher() {
    if (host) host.style.display = "none";
  }

  // ---- overlay lifecycle ----------------------------------------------- //
  function onActivate() {
    if (iframe) return;
    btn.disabled = true;
    btn.textContent = "Loading Python…";
    boot();
  }

  function boot() {
    iframe = document.createElement("iframe");
    iframe.id = FRAME_ID;
    iframe.src = chrome.runtime.getURL("{{OVERLAY_PAGE}}");
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
    hideLauncher();
    claimSlot();
    resetIdle();
  }

  function teardown() {
    if (!iframe) return;
    iframe.remove();
    iframe = null;
    clearTimeout(idleTimer);
    releaseSlot();
    showLauncher(); // next click boots a fresh interpreter
  }

  function resetIdle() {
    if (!IDLE_MS) return;
    clearTimeout(idleTimer);
    idleTimer = setTimeout(teardown, IDLE_MS);
  }

  // ---- content-script <-> overlay bridge -------------------------------- //
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

  window.addEventListener("message", (ev) => {
    if (!iframe || ev.source !== iframe.contentWindow) return;
    const msg = ev.data;
    if (!msg || msg.pyodex !== true) return;

    if (msg.type === "page.heartbeat") {
      resetIdle();
    } else if (msg.type === "page.keepalive") {
      // Python asked to stay resident for N ms (long task, open connection).
      const ms = Number(msg.payload) || 0;
      if (ms > 0) {
        clearTimeout(idleTimer);
        idleTimer = setTimeout(teardown, ms);
      }
    } else if (msg.type === "page.info") {
      iframe.contentWindow.postMessage(
        { pyodex: true, type: "page.info", id: msg.id, payload: { url: location.href, title: document.title } },
        "*"
      );
    } else if (msg.type === "page.dom") {
      let payload, error = null;
      try {
        payload = runDomOp(msg.payload || {});
      } catch (e) {
        error = String(e && e.message || e);
      }
      iframe.contentWindow.postMessage({ pyodex: true, type: "page.dom", id: msg.id, payload, error }, "*");
    } else if (msg.type === "page.events") {
      subscribeEvents(msg.payload || {});
    } else if (msg.type === "page.pointer") {
      iframe.style.pointerEvents = msg.payload ? "auto" : "none";
    } else if (msg.type === "page.boot") {
      // Runtime request from Python. Bounded by the configured ceiling: an app
      // can hand work back or ask for more, but never beyond the build's policy.
      const want = String(msg.payload || "auto");
      if (RANK[want] !== undefined && RANK[want] <= RANK[CEILING]) {
        mode = want;
        start();
      }
    } else if (msg.type === "page.release") {
      teardown();
    } else if (msg.type === "page.remove") {
      teardown();
    }
  });

  // ---- declarative triggers -------------------------------------------- //
  // Lets a tool that must react on every page (a password manager watching for
  // login forms) boot Python without a click, while pages that match nothing
  // still cost nothing.
  function globMatch(haystack, pattern) {
    if (!pattern) return true;
    if (!pattern.includes("*")) return haystack.toLowerCase().includes(pattern.toLowerCase());
    const re = new RegExp("^" + pattern.split("*").map((p) => p.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join(".*") + "$", "i");
    return re.test(haystack);
  }

  // Structured rules from @urlmatch, classified at build time so the semantics
  // are predictable: host / prefix / regex, each documented in the README.
  function urlRulesMatch(rules) {
    if (!rules || !rules.length) return true;
    const href = location.href;
    const host = location.hostname.toLowerCase();
    for (const r of rules) {
      if (r.kind === "regex") {
        try {
          if (new RegExp(r.pattern, "i").test(href)) return true;
        } catch {
          /* invalid pattern: ignore this rule, never throw into the page */
        }
        continue;
      }
      if (r.kind === "prefix") {
        if (href.toLowerCase().startsWith(String(r.pattern).toLowerCase())) return true;
        continue;
      }
      const p = String(r.pattern || "").toLowerCase();
      if (p === "*") return true;
      if (p.startsWith("*.")) {
        const base = p.slice(2);
        if (host === base || host.endsWith("." + base)) return true; // Chrome semantics
        continue;
      }
      if (host === p) return true;
    }
    return false;
  }

  function triggerMatched() {
    if (!TRIGGERS.length) return false;
    for (const t of TRIGGERS) {
      if (t.url_rules) {
        if (!urlRulesMatch(t.url_rules)) continue;
      } else if (!globMatch(location.href, t.url)) {
        continue;
      }
      if (t.selector && !document.querySelector(t.selector)) continue;
      return true;
    }
    return false;
  }

  let triggerObserver = null;

  function watchTriggers() {
    if (triggerMatched()) {
      boot();
      return;
    }
    let queued = false;
    const check = () => {
      queued = false;
      if (iframe) return;
      if (triggerMatched()) {
        triggerObserver && triggerObserver.disconnect();
        boot();
      }
    };
    triggerObserver = new MutationObserver(() => {
      if (queued) return;
      queued = true;
      setTimeout(check, 250); // coalesce DOM churn
    });
    triggerObserver.observe(document.documentElement, { childList: true, subtree: true });
    showLauncher(); // manual escape hatch while nothing matches
  }

  // ---- start ------------------------------------------------------------ //
  async function start() {
    if (mode === "user") {
      showLauncher();
      return;
    }
    if (mode === "trigger") {
      watchTriggers();
      return;
    }
    if (mode === "visible" && document.visibilityState !== "visible") {
      showLauncher(); // boots when the tab becomes visible
      return;
    }
    if (mode !== "trigger" && !(await budgetOk())) {
      // Over budget applies to automatic boots only; a trigger means the page
      // actually needs us, so it is not subject to the cap.
      showLauncher();
      return;
    }
    if (!iframe) boot();
  }

  document.addEventListener("visibilitychange", () => {
    if (mode === "user") return;
    if (document.visibilityState === "hidden") {
      if (!REAP_HIDDEN_MS) return;
      clearTimeout(hiddenTimer);
      hiddenTimer = setTimeout(() => {
        if (document.visibilityState === "hidden") teardown(); // reclaim memory
      }, REAP_HIDDEN_MS);
    } else {
      clearTimeout(hiddenTimer);
      if (!iframe) start(); // tab is back: bring Python back
    }
  });

  start();
})();

// ---- engine mode ---------------------------------------------------------- //
// With ui = "engine" there is no overlay iframe at all: Python lives in the
// shared engine and drives this page through the ops below. The tab therefore
// holds no interpreter, which is the whole point.
(() => {
  const ENGINE_MODE = {{ENGINE_UI}};
  if (!ENGINE_MODE) return;

  const HOST_ID = "pyodex-{{SLUG}}-ui";
  let host = null;
  let root = null;

  function uiRoot() {
    if (root) return root;
    host = document.createElement("div");
    host.id = HOST_ID;
    host.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647";
    root = host.attachShadow({ mode: "open" });
    const sheet = document.createElement("style");
    sheet.textContent = `button{pointer-events:auto;position:fixed;padding:10px 16px;font:600 14px system-ui,sans-serif;
      color:#fff;background:#18181b;border:2px solid #f43f5e;border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,.25);cursor:pointer}
      .panel{pointer-events:auto;position:fixed;background:rgba(24,24,27,.95);color:#fff;padding:12px;border-radius:8px;
      font:14px system-ui,sans-serif;white-space:pre-wrap}`;
    root.append(sheet);
    document.documentElement.appendChild(host);
    return root;
  }

  function op(name, args) {
    const r = uiRoot();
    if (name === "ui_button") {
      const b = document.createElement("button");
      b.id = args.id;
      b.textContent = args.label;
      b.style.top = `${args.top ?? 16}px`;
      b.style.left = `${args.left ?? 16}px`;
      b.addEventListener("click", () => {
        chrome.runtime.sendMessage({ kind: "pyodex-ui-event", id: args.id, event: "click" });
      });
      r.append(b);
      return { ok: true };
    }
    if (name === "ui_panel") {
      let el = r.querySelector(`#${CSS.escape(args.id)}`);
      if (!el) {
        el = document.createElement("div");
        el.id = args.id;
        el.className = "panel";
        el.style.top = `${args.top ?? 64}px`;
        el.style.left = `${args.left ?? 16}px`;
        r.append(el);
      }
      if (args.text !== undefined) el.textContent = String(args.text);
      return { ok: true };
    }
    throw new Error(`unknown ui op ${name}`);
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.kind !== "pyodex-tab-op") return false;
    let payload;
    try {
      payload = msg.op?.startsWith("ui_")
        ? op(msg.op, msg.args || {})
        : runDomOp({ ...(msg.args || {}), op: msg.op });
      sendResponse({ ok: true, payload });
    } catch (e) {
      sendResponse({ ok: false, error: String(e && e.message) });
    }
    return false;
  });

  // The agent iframe holds the SharedWorker connection; this page holds the DOM.
  const agentId = "a" + String(Math.random()).slice(2, 10);
  const agent = document.createElement("iframe");
  agent.src = chrome.runtime.getURL("agent.html") + "?agent=" + encodeURIComponent(agentId) +
              "&url=" + encodeURIComponent(location.href);
  Object.assign(agent.style, { position: "fixed", width: "0", height: "0", border: "none",
                               opacity: "0", pointerEvents: "none" });
  document.documentElement.appendChild(agent);

  window.addEventListener("message", (ev) => {
    if (ev.source !== agent.contentWindow) return;
    const msg = ev.data;
    if (!msg || msg.pyodex !== true || msg.kind !== "agent-dom") return;
    let reply;
    try {
      reply = { ok: true, payload: runDomOp({ ...(msg.args || {}), op: msg.op }) };
    } catch (e) {
      reply = { ok: false, error: String(e && e.message) };
    }
    agent.contentWindow.postMessage({ pyodex: true, kind: "agent-dom-reply", id: msg.id, reply }, "*");
  });

  // Announce this tab so the engine can address it. The reply is kept on window
  // so failures are diagnosable from DevTools (and by the test harness).
  // Retry on BOTH failure shapes: a rejected send (worker still waking) and an
  // {ok:false} reply (engine still booting). Retrying only on rejection is a bug:
  // the first attach is what the engine uses to start the app, and losing it is
  // invisible from the page.
  (function attach(attempt) {
    const retry = () => {
      if (attempt < 10) setTimeout(() => attach(attempt + 1), 200 * (attempt + 1));
    };
    try {
      chrome.storage.local.set({ __cs: "sending attempt " + attempt }).catch(() => {});
    } catch (e) {
      document.documentElement.dataset.csErr = String(e && e.message);
    }
    chrome.runtime
      .sendMessage({ kind: "pyodex-attach", url: location.href })
      .then((r) => {
        window.__pyodexAttach = r;
        if (!r || !r.ok) retry();
      })
      .catch((e) => {
        window.__pyodexAttach = { ok: false, error: String(e && e.message) };
        retry();
      });
  })(0);
})();
