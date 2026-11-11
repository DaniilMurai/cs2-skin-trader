// Throwaway spike S-10: can a Cloudflare Worker cron fetch the market.csgo price feeds hourly and keep
// the raw bytes in R2? Nothing is parsed here; the Mac converts raw files to Parquet later.
const FEEDS = {
  asks: "https://market.csgo.com/api/v2/prices/USD.json",
  bids: "https://market.csgo.com/api/v2/prices/orders/USD.json",
};

async function gzip(buf) {
  const stream = new Blob([buf]).stream().pipeThrough(new CompressionStream("gzip"));
  return new Response(stream).arrayBuffer();
}

async function collectOne(env, kind, iso, scheduledMs) {
  const started = Date.now();
  const out = { kind, status: null, bytes: 0, stored_bytes: 0, fetch_ms: 0, error: null };
  try {
    const r = await fetch(FEEDS[kind], { headers: { "User-Agent": "cs2trader-collector-spike" } });
    out.status = r.status;
    const body = await r.arrayBuffer();
    out.fetch_ms = Date.now() - started;
    out.bytes = body.byteLength;
    const day = iso.slice(0, 10);
    if (r.status === 200) {
      const compress = env.COMPRESS === "1";
      const data = compress ? await gzip(body) : body;
      out.stored_bytes = data.byteLength;
      await env.BUCKET.put(`raw/${kind}/day=${day}/${iso}${compress ? ".json.gz" : ".json"}`, data, {
        customMetadata: { fetched_at: new Date().toISOString(), scheduled_at: new Date(scheduledMs).toISOString() },
      });
    } else {
      const head = new TextDecoder().decode(body.slice(0, 300));
      await env.BUCKET.put(`errors/${kind}/${iso}.txt`, `HTTP ${r.status}\n${head}`);
    }
  } catch (e) {
    out.error = String(e);
  }
  return out;
}

async function collect(env, scheduledMs) {
  const ran = new Date();
  const iso = ran.toISOString().replace(/[:.]/g, "-");
  const results = [];
  for (const kind of Object.keys(FEEDS)) results.push(await collectOne(env, kind, iso, scheduledMs));
  const entry = { scheduled_at: new Date(scheduledMs).toISOString(), ran_at: ran.toISOString(),
                  delay_s: (ran.getTime() - scheduledMs) / 1000, results };
  await env.BUCKET.put(`log/${iso}.json`, JSON.stringify(entry));
  return entry;
}

async function status(env) {
  const list = await env.BUCKET.list({ prefix: "log/", limit: 1000 });
  const entries = [];
  for (const o of list.objects.slice(-100)) entries.push(await (await env.BUCKET.get(o.key)).json());
  const delays = entries.map((e) => e.delay_s).sort((a, b) => a - b);
  const ok = entries.filter((e) => e.results.every((r) => r.status === 200)).length;
  const pick = (p) => (delays.length ? delays[Math.min(delays.length - 1, Math.floor(p * delays.length))] : null);
  return { runs: entries.length, all_ok: ok, delay_s: { p50: pick(0.5), p90: pick(0.9), max: pick(1) },
           statuses: [...new Set(entries.flatMap((e) => e.results.map((r) => r.status)))],
           last: entries.slice(-3) };
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(collect(env, event.scheduledTime));
  },
  // Manual trigger and report; disabled unless the TRIGGER_TOKEN secret is set.
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!env.TRIGGER_TOKEN || url.searchParams.get("token") !== env.TRIGGER_TOKEN) {
      return new Response("not found", { status: 404 });
    }
    if (url.pathname === "/run") return Response.json(await collect(env, Date.now()));
    if (url.pathname === "/status") return Response.json(await status(env));
    return new Response("not found", { status: 404 });
  },
};
