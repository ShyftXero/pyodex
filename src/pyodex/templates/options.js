// Options UI: CRUD over the per-site cache opt-out list.
import { clearAll, estimate, noCacheHosts, setNoCacheHosts } from "./cache.js";

const rows = document.getElementById("rows");
const empty = document.getElementById("empty");
const status = document.getElementById("status");
const usage = document.getElementById("usage");
const input = document.getElementById("host-input");

const say = (msg) => {
  status.textContent = msg;
  setTimeout(() => (status.textContent = ""), 2500);
};

function scope(pattern) {
  if (pattern === "*") return "everywhere";
  if (pattern.startsWith("*.")) return "domain and subdomains";
  return "exact host";
}

function render(list) {
  rows.replaceChildren();
  empty.style.display = list.length ? "none" : "";
  list.forEach((pattern, i) => {
    const tr = document.createElement("tr");
    const host = document.createElement("td");
    host.textContent = pattern;
    const sc = document.createElement("td");
    sc.className = "hint";
    sc.textContent = scope(pattern);
    const actions = document.createElement("td");
    actions.className = "actions";

    const edit = document.createElement("button");
    edit.textContent = "edit";
    edit.addEventListener("click", async () => {
      const next = prompt("Host pattern", pattern);
      if (!next || next === pattern) return;
      const l = await noCacheHosts();
      l[i] = next;
      await setNoCacheHosts(l);
      await refresh();
      say(`updated ${pattern}`);
    });

    const del = document.createElement("button");
    del.textContent = "remove";
    del.style.marginLeft = "6px";
    del.addEventListener("click", async () => {
      const l = await noCacheHosts();
      l.splice(i, 1);
      await setNoCacheHosts(l);
      await refresh();
      say(`removed ${pattern}`);
    });

    actions.append(edit, del);
    tr.append(host, sc, actions);
    rows.append(tr);
  });
}

async function refresh() {
  render(await noCacheHosts());
  const { usage: used, quota } = await estimate();
  usage.textContent = used
    ? `Cache storage: ${(used / 1048576).toFixed(1)} MB of ${(quota / 1048576).toFixed(0)} MB available (budget 200 MB).`
    : "Cache storage: empty.";
}

document.getElementById("add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const value = input.value.trim();
  if (!value) return;
  const list = await noCacheHosts();
  if (list.includes(value.toLowerCase())) {
    say("already listed");
    return;
  }
  await setNoCacheHosts([...list, value]);
  input.value = "";
  await refresh();
  say(`added ${value} (its cached blobs were purged)`);
});

document.getElementById("clear").addEventListener("click", async () => {
  const n = await clearAll();
  await refresh();
  say(`cleared ${n} cached blob${n === 1 ? "" : "s"}`);
});

document.getElementById("export").addEventListener("click", async () => {
  const data = JSON.stringify({ version: 1, no_cache_hosts: await noCacheHosts() }, null, 2);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([data], { type: "application/json" }));
  a.download = "pyodex-cache-settings.json";
  a.click();
  URL.revokeObjectURL(a.href);
  say("exported");
});

document.getElementById("import").addEventListener("click", () => {
  const picker = document.createElement("input");
  picker.type = "file";
  picker.accept = "application/json";
  picker.addEventListener("change", async () => {
    const file = picker.files?.[0];
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      const list = parsed.no_cache_hosts ?? parsed;
      if (!Array.isArray(list)) throw new Error("expected an array of hosts");
      await setNoCacheHosts(list);
      await refresh();
      say(`imported ${list.length} entries`);
    } catch (err) {
      say(`import failed: ${err.message}`);
    }
  });
  picker.click();
});

refresh();
