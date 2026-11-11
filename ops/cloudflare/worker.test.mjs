import assert from "node:assert/strict";
import { test } from "node:test";
import { collect, handle, usageBytes } from "./worker.js";

class FakeBucket {
  constructor() { this.m = new Map(); }
  async put(k, v) { this.m.set(k, typeof v === "string" ? new TextEncoder().encode(v).buffer : v); }
  async get(k) { const v = this.m.get(k); return v ? { body: v, json: async () => JSON.parse(new TextDecoder().decode(v)) } : null; }
  async delete(keys) { for (const k of [].concat(keys)) this.m.delete(k); }
  async list({ prefix, cursor, limit }) {
    const all = [...this.m].filter(([k]) => k.startsWith(prefix)).map(([key, v]) => ({ key, size: v.byteLength, uploaded: "t" }));
    const start = cursor ? Number(cursor) : 0;
    const objects = all.slice(start, start + limit);
    return { objects, truncated: start + limit < all.length, cursor: String(start + limit) };
  }
}
const feedBody = (n) => new Uint8Array(n).buffer;
const withFetch = (status, size, fn) => async () => {
  const real = globalThis.fetch;
  globalThis.fetch = async () => ({ status, arrayBuffer: async () => feedBody(size) });
  try { await fn(); } finally { globalThis.fetch = real; }
};

test("a run stores both feeds and a log entry", withFetch(200, 1000, async () => {
  const env = { BUCKET: new FakeBucket() };
  const e = await collect(env, Date.now());
  assert.equal(e.results.length, 2);
  assert.equal([...env.BUCKET.m.keys()].filter((k) => k.startsWith("raw/")).length, 2);
  assert.equal([...env.BUCKET.m.keys()].filter((k) => k.startsWith("log/")).length, 1);
}));

test("stops adding files at the quota and says so", withFetch(200, 1000, async () => {
  const env = { BUCKET: new FakeBucket(), MAX_BYTES: "2500" };
  await collect(env, Date.now(), new Date("2026-01-01T00:07:00Z"));
  const second = await collect(env, Date.now(), new Date("2026-01-01T01:07:00Z"));
  assert.equal(second.results.length, 2);          // 2000 < 2500: still room
  const third = await collect(env, Date.now(), new Date("2026-01-01T02:07:00Z"));
  assert.match(third.stopped, /quota/);
  assert.equal(third.results.length, 0);
  assert.equal(await usageBytes(env.BUCKET), 4000);
}));

test("usage counts every page of the listing", async () => {
  const b = new FakeBucket();
  for (let i = 0; i < 2500; i++) await b.put(`raw/asks/${i}`, feedBody(10));
  assert.equal(await usageBytes(b), 25000);
});

test("a non-200 feed is logged, not stored as data", withFetch(503, 10, async () => {
  const env = { BUCKET: new FakeBucket() };
  await collect(env, Date.now());
  assert.equal([...env.BUCKET.m.keys()].filter((k) => k.startsWith("raw/")).length, 0);
  assert.equal([...env.BUCKET.m.keys()].filter((k) => k.startsWith("errors/")).length, 2);
}));

const req = (path, init = {}, token = "secret") =>
  new Request("https://x.test" + path, { ...init, headers: token ? { "X-CS2-Token": token } : {} });

test("every route needs the header token; the URL token is ignored", async () => {
  const env = { BUCKET: new FakeBucket(), TRIGGER_TOKEN: "secret" };
  assert.equal((await handle(req("/status", {}, null), env)).status, 404);
  assert.equal((await handle(req("/status", {}, "wrong"), env)).status, 404);
  assert.equal((await handle(new Request("https://x.test/status?token=secret"), env)).status, 404);
  assert.equal((await handle(req("/status"), env)).status, 200);
  assert.equal((await handle(req("/status", {}, "secret"), { BUCKET: env.BUCKET })).status, 404); // no secret configured
});

test("list, get and delete only touch raw/ files", async () => {
  const env = { BUCKET: new FakeBucket(), TRIGGER_TOKEN: "secret" };
  await env.BUCKET.put("raw/asks/day=1/a.json", feedBody(5));
  await env.BUCKET.put("log/x.json", "{}");
  const listed = await (await handle(req("/list"), env)).json();
  assert.deepEqual(listed.objects.map((o) => o.key), ["raw/asks/day=1/a.json"]);
  assert.equal((await handle(req("/get?key=log/x.json"), env)).status, 400);
  assert.equal((await handle(req("/get?key=raw/../log/x.json"), env)).status, 400);
  assert.equal((await handle(req("/get?key=raw/asks/day=1/a.json"), env)).status, 200);
  const del = await handle(req("/delete", { method: "POST", body: JSON.stringify({ keys: ["log/x.json", "raw/asks/day=1/a.json"] }) }), env);
  assert.equal((await del.json()).deleted, 1);
  assert.ok(env.BUCKET.m.has("log/x.json"));
});
