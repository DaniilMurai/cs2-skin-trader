# S-10 — can a Cloudflare Worker be the hourly collector?

Questions (yes/no + facts):
1. Does market.csgo answer a Cloudflare Worker (status 200, ~2.5 MB) or does it block Cloudflare's IPs?
2. How punctual is the cron (`delay_s` in each log entry), and are runs ever skipped?
3. Does the work fit the free plan's CPU limit (dashboard: Workers & Pages → cs2-collector-spike →
   Metrics → CPU time; the free limit is ~10 ms per run, as far as I recall)? Run once with
   `COMPRESS = "0"`, then with `"1"`.
4. Storage per day in R2 (raw 2 feeds × 24 h ≈ 120 MB/day uncompressed, ≈ 13 MB/day gzipped).

Only the price feeds (asks, bids) are in scope; sales polling (240 requests per run) is a separate question.

## Steps (you, about 10 minutes; nothing here uses a card unless Cloudflare asks to enable R2)
```bash
cd docs/pipeline/spikes/10-cloudflare-collector
npx wrangler login                                   # opens the browser; no token is pasted anywhere
npx wrangler r2 bucket create cs2-collector-spike    # R2 may ask for a payment method; the free tier stays free
npx wrangler secret put TRIGGER_TOKEN                # type any long random string (guards /run and /status)
npx wrangler deploy                                  # prints https://cs2-collector-spike.<you>.workers.dev
curl "https://cs2-collector-spike.<you>.workers.dev/run?token=<your token>"     # one run right now
```
A good first answer: `results` shows `status: 200` and `bytes` ≈ 2.5 million for `asks` and a size for `bids`.
Then leave it for 24–48 h and read the verdict data:
```bash
curl "https://cs2-collector-spike.<you>.workers.dev/status?token=<your token>"
```
It returns the number of runs, how many were fully OK, the delay percentiles in seconds and the
last three entries. Read the CPU time from the dashboard (Metrics tab). Then set `COMPRESS = "1"`,
`npx wrangler deploy` again and repeat the CPU reading.

Delete everything afterwards (or keep it, it is free): `npx wrangler delete`, and the R2 bucket in the dashboard.
