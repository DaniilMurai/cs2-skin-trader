# Collector operations

## GitHub Actions (main, hourly)

`.github/workflows/collect.yml` runs `cs2 collect run --host gha` at :17 every hour and pushes
new Parquet files to the private repo `cs2-skin-data` (same tree as `data/collector/`).

One-time setup:
1. Push this repo to GitHub (done 2026-11-09: public, github.com/DaniilMurai/cs2-skin-trader).
2. Create the private repo `cs2-skin-data` under the same owner with one initial commit.
3. Create a fine-grained token with Contents: read and write on `cs2-skin-data` only.
4. In this repo, Settings → Secrets and variables → Actions, add `DATA_REPO_TOKEN` (the token)
   and `MARKETCSGO_API_KEY`.
5. Actions → collect → Run workflow once to check.

Locally, `uv run cs2 collect pull` clones or pulls `cs2-skin-data` into `data/collector/`.

## Mac launchd (gap filler)

The plist is rendered from `com.cs2trader.collect.plist.template` (repo path and `uv` location are
filled in at install time, so no personal paths are committed). Runs `cs2 collect run --kinds asks,bids --host mac` at :47 every hour; a run missed during sleep
fires once after wake. Logs: `data/logs/collect-mac.log`.

```bash
ops/install-launchd.sh
```

Remove:

```bash
launchctl bootout gui/$(id -u)/com.cs2trader.collect
```
