// The tour itself, in an extension page (so chrome.* is present).
(async () => {
 try {
  const t0 = performance.now();
  const lines = [];
  let ok = 0;
  const rec = (l, v) => { ok += 1; lines.push(`${l}: ${v}`); };

  const dom = await new Promise((resolve) => {
    const timer = setTimeout(() => resolve({}), 3000);
    window.addEventListener("message", (ev) => {
      if (ev.data && ev.data.kind === "dom-result") { clearTimeout(timer); resolve(ev.data.payload); }
    });
    parent.postMessage({ kind: "dom" }, "*");
  });

  rec("extension id", chrome.runtime.id.slice(0, 12) + "…");
  const manifest = chrome.runtime.getManifest();
  rec("permissions", (manifest.permissions || []).join(",") || "none");
  rec("runtime url", chrome.runtime.getURL("manifest.json").startsWith("chrome-extension://"));

  rec("tabs open", (await chrome.tabs.query({})).length);
  const [current] = await chrome.tabs.query({ active: true, currentWindow: true });
  rec("this tab", (current?.url || "?").slice(0, 38));
  rec("windows", (await chrome.windows.getAll()).length);
  rec("tabs query", (await chrome.tabs.query({ active: true })).length);

  await chrome.storage.local.set({ tour: "local-ok" });
  rec("storage.local", (await chrome.storage.local.get("tour")).tour);
  try {
    await chrome.storage.session.set({ tour: "session-ok" });
    rec("storage.session", (await chrome.storage.session.get("tour")).tour);
  } catch (e) { rec("storage.session", `unavailable (${e.name})`); }

  await chrome.action.setTitle({ title: "Demo 06 — API tour (JS)" });
  await chrome.action.setBadgeText({ text: "06" });
  rec("action badge", "set");

  rec("dom element value", dom.value ?? "none");
  rec("dom count(input)", dom.count ?? 0);
  rec("dom text(h1)", dom.text ?? "");
  rec("dom set_attr", "body[data-tour]=ran");

  document.getElementById("head").textContent = `Demo 06 — ${ok} call(s) exercised`;
  document.getElementById("body").textContent = lines.join("\n");
  parent.postMessage({ kind: "done", ok, ms: performance.now() - t0 }, "*");
 } catch (e) {
  document.getElementById("body").textContent = "ERROR " + (e && e.name) + ": " + String(e && e.message).slice(0, 160);
 }
})();
