# throwaway: download feed, save gzipped snapshot named by UTC hour
import urllib.request, gzip, pathlib, datetime
out = pathlib.Path.home() / "cs2-snapshots"; out.mkdir(exist_ok=True)
raw = urllib.request.urlopen("https://market.csgo.com/api/v2/prices/USD.json", timeout=60).read()
ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%MZ")
(out / f"{ts}.json.gz").write_bytes(gzip.compress(raw, 9))
