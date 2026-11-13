# Cloudflare buffer for the hourly price feeds

A Worker (two crons: `7 * * * *` market.csgo asks+bids, `9 * * * *` Waxpeer; one invocation each, because the free plan's 10 ms CPU limit is per invocation) downloads the market.csgo asks and bids feeds and the Waxpeer prices and stores the raw JSON in the R2
bucket `cs2-collector-spike`. Your Mac imports them with `cs2 collect pull` (hourly via launchd, which runs
`cs2 collect pull --cf-only` after each collector run) and deletes each file from R2 once its Parquet snapshot
exists. Sales polling stays on the Mac, and so does Skinport: its Cloudflare zone answers every Worker request
with HTTP 403 `error code: 1005` (the Workers network is banned; measured 2026-11-12..13).

Why raw and why a buffer: gzip inside the Worker used 77–184 ms CPU against the free plan's 10 ms; raw
storage uses 7–13 ms (spike S-10, `docs/pipeline/spikes/FINDINGS.md`). Raw files are ~7 MB per hour
(~170 MB/day), so the Mac's import is what keeps the bucket small; with the Mac off the 8 GB guard
stops new files after about 47 days.

**Quota guard:** before each run the Worker sums the size of `raw/` and starts only if `used + room for the files
the run may add` (5 MB per feed) stays within `MAX_BYTES` (8 GB, 80 % of the free 10 GB), so the
bucket never passes 8 GB. Otherwise it writes nothing and logs `stopped: quota…`. Every import deletes what it
imported, so usage resets; 8 GB is reached only if imports fail for about 47 days in a row.

## Setup (once)
```bash
cd ops/cloudflare
npx wrangler login
npx wrangler r2 bucket create cs2-collector-spike
TOK=$(openssl rand -hex 24)
printf '%s' "$TOK" | npx wrangler secret put TRIGGER_TOKEN
# the leading \n matters: .env may not end with a newline, and without it the URL is glued onto the last key
printf '\nCF_WORKER_URL=https://<name>.<subdomain>.workers.dev\nCF_WORKER_TOKEN=%s\n' "$TOK" >> ../../.env
npx wrangler deploy
ops/install-launchd.sh          # from the repo root: re-renders the launchd job that imports hourly
```

## Use
- Check it: `curl -H "X-CS2-Token: $CF_WORKER_TOKEN" $CF_WORKER_URL/status` (runs, delays, bytes used, limit).
- The token goes in a header, never in the URL; every route answers 404 without it. `/list`, `/get` and
  `/delete` only touch `raw/` files.
- Tests: `node --test worker.test.mjs`. Python side: `tests/test_cloudflare.py`.
- Stop and remove: `npx wrangler delete`, then delete the bucket in the Cloudflare dashboard.
