# Cloudflare buffer for the hourly price feeds

A Worker (cron `7 * * * *`) downloads the market.csgo asks and bids feeds and stores the raw JSON in the R2
bucket `cs2-collector-spike`. Your Mac imports them with `cs2 collect pull` (hourly via launchd, which runs
`cs2 collect pull --cf-only` after each collector run) and deletes each file from R2 once its Parquet snapshot
exists. Sales polling stays on the Mac.

Why raw and why a buffer: gzip inside the Worker used 77–184 ms CPU against the free plan's 10 ms; raw
storage uses 7–13 ms (spike S-10, `docs/pipeline/spikes/FINDINGS.md`). Raw files are ~4.9 MB per run
(~117 MB/day), so the Mac's import is what keeps the bucket small.

**Quota guard:** before each run the Worker sums the size of `raw/`; at `MAX_BYTES` (8 GB, 80 % of the free
10 GB) it writes no new files and logs `stopped: quota…` instead. It resumes after the Mac imports.

## Setup (once)
```bash
cd ops/cloudflare
npx wrangler login
npx wrangler r2 bucket create cs2-collector-spike
TOK=$(openssl rand -hex 24)
printf '%s' "$TOK" | npx wrangler secret put TRIGGER_TOKEN
printf 'CF_WORKER_URL=https://<name>.<subdomain>.workers.dev\nCF_WORKER_TOKEN=%s\n' "$TOK" >> ../../.env
npx wrangler deploy
ops/install-launchd.sh          # from the repo root: re-renders the launchd job that imports hourly
```

## Use
- Check it: `curl -H "X-CS2-Token: $CF_WORKER_TOKEN" $CF_WORKER_URL/status` (runs, delays, bytes used, limit).
- The token goes in a header, never in the URL; every route answers 404 without it. `/list`, `/get` and
  `/delete` only touch `raw/` files.
- Tests: `node --test worker.test.mjs`. Python side: `tests/test_cloudflare.py`.
- Stop and remove: `npx wrangler delete`, then delete the bucket in the Cloudflare dashboard.
