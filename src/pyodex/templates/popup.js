// Popup: a status view over the cache, and a door to the options page.
import { estimate, noCacheHosts } from "./cache.js";

const cache = await caches.open("pyodex-blobs-v1");
const keys = await cache.keys();
document.getElementById("entries").textContent = String(keys.length);

const { usage } = await estimate();
document.getElementById("usage").textContent = usage ? `${(usage / 1048576).toFixed(1)} MB` : "empty";

const hosts = await noCacheHosts();
document.getElementById("hosts").textContent = hosts.length ? hosts.join(", ") : "none";

document.getElementById("options").addEventListener("click", () => chrome.runtime.openOptionsPage());
