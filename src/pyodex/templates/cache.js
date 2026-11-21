// pyodex blob cache.
//
// Pyodide runtimes and wheels are large and immutable per version, so they are
// the ideal cache citizens — provided nothing stale is ever served. A cached
// entry is keyed by URL and only served when its sha256 matches the digest the
// caller expects. A mismatch is a miss plus eviction, never a silent serve.
//
// Opt-out is per site: a list of host patterns in chrome.storage.local. Caching
// is ON by default; a matching host opts out and its entries are purged.
//
// The opt-out list is authored by the user (options page) and read here. It
// deliberately does not stop the app from running — only from caching.

const STORE_KEY = "pyodex_no_cache_hosts";
const CACHE_NAME = "pyodex-blobs-v1";
const BUDGET_BYTES = 200 * 1024 * 1024;

export const stats = { hits: 0, misses: 0, evictions: 0, optedOut: 0, bytes: 0 };

// ---------------------------------------------------------------- host rules

function hostMatches(pattern, host) {
  const p = String(pattern || "").trim().toLowerCase();
  if (!p) return false;
  if (p === "*") return true;
  if (p.startsWith("*.")) {
    const base = p.slice(2);
    return host === base || host.endsWith("." + base);
  }
  if (p.includes("*")) {
    const rx = new RegExp("^" + p.split("*").map((s) => s.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join(".*") + "$");
    return rx.test(host);
  }
  return host === p;
}

export async function noCacheHosts() {
  const { [STORE_KEY]: list = [] } = await chrome.storage.local.get(STORE_KEY);
  return list;
}

export async function setNoCacheHosts(list) {
  const clean = [...new Set((list || []).map((s) => String(s).trim().toLowerCase()).filter(Boolean))];
  await chrome.storage.local.set({ [STORE_KEY]: clean });
  await purgeOptedOut(clean);
  return clean;
}

export async function purgeOptedOut(hosts = null) {
  const list = hosts || (await noCacheHosts());
  const cache = await caches.open(CACHE_NAME);
  const keys = await cache.keys();
  let removed = 0;
  for (const req of keys) {
    let host = "";
    try {
      host = new URL(req.url).hostname;
    } catch {
      continue;
    }
    if (list.some((p) => hostMatches(p, host))) {
      await cache.delete(req);
      removed++;
    }
  }
  stats.evictions += removed;
  return removed;
}

export async function hostAllowed(url) {
  let host;
  try {
    host = new URL(url).hostname.toLowerCase();
  } catch {
    return true;
  }
  const list = await noCacheHosts();
  const blocked = list.some((p) => hostMatches(p, host));
  if (blocked) stats.optedOut++;
  return !blocked;
}

// ------------------------------------------------------------------- digests

function toHex(buffer) {
  return [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sha256Hex(data) {
  const buf = data instanceof ArrayBuffer ? data : await new Response(data).arrayBuffer();
  return toHex(await crypto.subtle.digest("SHA-256", buf));
}

// --------------------------------------------------------------------- cache

async function cacheKey(url) {
  // Version-independent: the digest decides freshness, not the URL.
  return new Request(url, { mode: "cors", credentials: "omit" });
}

export async function estimate() {
  try {
    const { usage = 0, quota = 0 } = await navigator.storage.estimate();
    return { usage, quota };
  } catch {
    return { usage: 0, quota: 0 };
  }
}

async function evictUntilUnderBudget(incoming) {
  const { usage } = await estimate();
  if (!usage || usage + incoming <= BUDGET_BYTES) return;
  const cache = await caches.open(CACHE_NAME);
  const keys = await cache.keys();
  for (const req of keys) {
    await cache.delete(req);
    stats.evictions++;
    const now = await estimate();
    if (now.usage + incoming <= BUDGET_BYTES) return;
  }
}

/**
 * Fetch `url`, serving a cached copy only when its digest matches `digest`
 * (hex sha256, optional). Returns { data: ArrayBuffer, source: "cache"|"network" }.
 */
export async function cachedFetch(url, digest = null, { siteUrl = null } = {}) {
  const allowed = await hostAllowed(url);
  const cache = await caches.open(CACHE_NAME);
  const key = await cacheKey(url);

  if (allowed) {
    const hit = await cache.match(key);
    if (hit) {
      const buf = await hit.arrayBuffer();
      if (!digest || (await sha256Hex(buf)) === digest.toLowerCase()) {
        stats.hits++;
        return { data: buf, source: "cache" };
      }
      // Wrong content for the expected version: never serve it.
      await cache.delete(key);
      stats.evictions++;
    }
  }

  const res = await fetch(url, { credentials: "omit" });
  if (!res.ok) throw new Error(`fetch ${url}: ${res.status}`);
  const buf = await res.arrayBuffer();
  const actual = await sha256Hex(buf);
  if (digest && actual !== digest.toLowerCase()) {
    throw new Error(`digest mismatch for ${url}: expected ${digest}, got ${actual}`);
  }

  if (allowed) {
    await evictUntilUnderBudget(buf.byteLength);
    await cache.put(key, new Response(buf, { headers: { "x-pyodex-digest": actual } }));
    stats.bytes += buf.byteLength;
  }
  stats.misses++;
  return { data: buf, source: "network" };
}

export async function clearAll() {
  const cache = await caches.open(CACHE_NAME);
  const keys = await cache.keys();
  for (const k of keys) await cache.delete(k);
  stats.evictions += keys.length;
  return keys.length;
}
