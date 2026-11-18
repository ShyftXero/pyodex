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
    } else if (msg.type === "page.pointer") {
      // Let Python toggle whether the whole overlay captures clicks.
      iframe.style.pointerEvents = msg.payload ? "auto" : "none";
    } else if (msg.type === "page.remove") {
      iframe.remove();
    }
  });
})();
