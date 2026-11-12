// Hourly buffer for the market.csgo price feeds. The Worker only downloads and stores the raw bytes in
// R2 (no parsing, no compression: gzip costs 77-184 ms CPU against the free plan's 10 ms, S-10); the Mac
// imports each file with `cs2 collect pull` and then deletes it here.
const FEEDS = {
  asks: { url: "https://market.csgo.com/api/v2/prices/USD.json" },
  bids: { url: "https://market.csgo.com/api/v2/prices/orders/USD.json" },
  waxpeer: { url: "https://api.waxpeer.com/v1/prices?game=csgo&minified=1" },
  // Only the tradable view: the full list differs by 0.3 % of items and would double the 15 MB raw size.
  // Skinport answers 406 unless `br` is accepted.
  skinport: { url: "https://api.skinport.com/v1/items?app_id=730&currency=USD&tradable=true",
              headers: { "Accept-Encoding": "br" } },
};
const RAW = "raw/";
const DEFAULT_MAX_BYTES = 8e9; // 80 % of R2's free 10 GB
// One cron per group: the free plan's 10 ms CPU limit applies per invocation, so feeds in one run add up.
const CRON_FEEDS = { "7 * * * *": ["asks", "bids"], "9 * * * *": ["waxpeer"], "11 * * * *": ["skinport"] };
// A run is only started if the files it may add still fit under the limit (the largest feed is Skinport, ~15.5 MB).
const FEED_MARGIN = { skinport: 20e6 };
const DEFAULT_MARGIN = 5e6;

export async function usageBytes(bucket) {
  let total = 0, cursor;
  do {
    const page = await bucket.list({ prefix: RAW, cursor, limit: 1000 });
    for (const o of page.objects) total += o.size;
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  return total;
}

async function fetchFeed(env, kind, iso) {
  const started = Date.now();
  const out = { kind, status: null, bytes: 0, fetch_ms: 0, error: null };
  try {
    const feed = FEEDS[kind];
    const r = await fetch(feed.url, { headers: { "User-Agent": "cs2trader-collector", ...feed.headers } });
    out.status = r.status;
    const body = await r.arrayBuffer();
    out.fetch_ms = Date.now() - started;
    out.bytes = body.byteLength;
    if (r.status === 200) {
      await env.BUCKET.put(`${RAW}${kind}/day=${iso.slice(0, 10)}/${iso}.json`, body);
    } else {
      await env.BUCKET.put(`errors/${kind}/${iso}.txt`, `HTTP ${r.status}\n${new TextDecoder().decode(body.slice(0, 300))}`);
    }
  } catch (e) {
    out.error = String(e);
  }
  return out;
}

export async function collect(env, scheduledMs, now = new Date(), kinds = Object.keys(FEEDS)) {
  const iso = now.toISOString().replace(/[:.]/g, "-");
  const maxBytes = Number(env.MAX_BYTES || DEFAULT_MAX_BYTES);
  const used = await usageBytes(env.BUCKET);
  const entry = { scheduled_at: new Date(scheduledMs).toISOString(), ran_at: now.toISOString(),
                  delay_s: (now.getTime() - scheduledMs) / 1000, used_bytes: used, results: [] };
  const need = kinds.reduce((n, k) => n + (FEED_MARGIN[k] || DEFAULT_MARGIN), 0);
  if (used + need > maxBytes) {
    entry.stopped = "quota: raw files use " + used + " bytes, limit " + maxBytes + "; import them with `cs2 collect pull`";
  } else {
    for (const kind of kinds) entry.results.push(await fetchFeed(env, kind, iso));
  }
  await env.BUCKET.put(`log/${iso}.json`, JSON.stringify(entry));
  return entry;
}

async function status(env) {
  const list = await env.BUCKET.list({ prefix: "log/", limit: 1000 });
  const entries = [];
  for (const o of list.objects.slice(-100)) entries.push(await (await env.BUCKET.get(o.key)).json());
  const delays = entries.map((e) => e.delay_s).sort((a, b) => a - b);
  const pick = (p) => (delays.length ? delays[Math.min(delays.length - 1, Math.floor(p * delays.length))] : null);
  return {
    runs: entries.length,
    all_ok: entries.filter((e) => !e.stopped && e.results.every((r) => r.status === 200)).length,
    stopped: entries.filter((e) => e.stopped).length,
    delay_s: { p50: pick(0.5), p90: pick(0.9), max: pick(1) },
    used_bytes: await usageBytes(env.BUCKET), max_bytes: Number(env.MAX_BYTES || DEFAULT_MAX_BYTES),
    last: entries.slice(-3),
  };
}

const json = (obj, status = 200) => Response.json(obj, { status });
const rawKey = (k) => typeof k === "string" && k.startsWith(RAW) && !k.includes("..");

export async function handle(request, env) {
  const url = new URL(request.url);
  // The token travels in a header, never in the URL (URLs end up in logs).
  const sent = request.headers.get("X-CS2-Token") || "";
  if (!env.TRIGGER_TOKEN || sent !== env.TRIGGER_TOKEN) return new Response("not found", { status: 404 });
  if (url.pathname === "/status") return json(await status(env));
  if (url.pathname === "/list") {
    const page = await env.BUCKET.list({ prefix: RAW, cursor: url.searchParams.get("cursor") || undefined, limit: 500 });
    return json({ objects: page.objects.map((o) => ({ key: o.key, size: o.size, uploaded: o.uploaded })),
                  cursor: page.truncated ? page.cursor : null });
  }
  if (url.pathname === "/get") {
    const key = url.searchParams.get("key");
    if (!rawKey(key)) return json({ error: "bad key" }, 400);
    const obj = await env.BUCKET.get(key);
    return obj ? new Response(obj.body, { headers: { "Content-Type": "application/json" } }) : json({ error: "gone" }, 404);
  }
  if (url.pathname === "/delete" && request.method === "POST") {
    const keys = ((await request.json()).keys || []).filter(rawKey).slice(0, 100);
    await env.BUCKET.delete(keys);
    return json({ deleted: keys.length });
  }
  if (url.pathname === "/run" && request.method === "POST") return json(await collect(env, Date.now()));
  return new Response("not found", { status: 404 });
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(collect(env, event.scheduledTime, new Date(), CRON_FEEDS[event.cron] || Object.keys(FEEDS)));
  },
  fetch: handle,
};
