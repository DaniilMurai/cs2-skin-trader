"""Rebuild the Major rows of docs/pipeline/events.csv from Wikipedia (CC BY-SA), one API call."""
import datetime
import json
import re
import sys
import urllib.parse
import urllib.request

UA = "cs2-skin-trader-research/0.1 (personal research)"
PAGE = "Counter-Strike_Major_Championships"
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July",
                                      "August", "September", "October", "November", "December"], 1)}


def wikitext() -> str:
    q = urllib.parse.urlencode({"action": "parse", "page": PAGE, "prop": "wikitext", "format": "json"})
    req = urllib.request.Request(f"https://en.wikipedia.org/w/api.php?{q}", headers={"User-Agent": UA})
    return json.load(urllib.request.urlopen(req, timeout=60))["parse"]["wikitext"]["*"]


def majors(w: str) -> list[tuple[str, datetime.date, datetime.date]]:
    t = w[w.find('{| class="wikitable'):]
    t = t[:t.find("\n|}")]
    out = []
    for r in t.split("\n|-"):
        m = re.search(r'!\s*scope="row"\s*\|\s*\[\[[^|\]]*\|?([^\]]*)\]\]', r)
        lines = [l for l in r.split("\n") if re.search(r"\b20\d\d\b", l) and re.search("|".join(MONTHS), l)]
        if not m or not lines:
            continue
        d = lines[0]
        year = int(re.findall(r"(20\d\d)", d)[-1])
        md = re.findall(r"(" + "|".join(MONTHS) + r")\s+(\d+)", d)
        start = datetime.date(year, MONTHS[md[0][0]], int(md[0][1]))
        if len(md) > 1:
            end = datetime.date(year, MONTHS[md[1][0]], int(md[1][1]))
        else:
            end = datetime.date(year, start.month, int(re.search(r"[–-]\s*(\d+),", d).group(1)))
        if end < start:  # a range across New Year ("December 30 – January 2, 2025")
            start = start.replace(year=year - 1)
        out.append((m.group(1), start, end))
    return out


if __name__ == "__main__":
    src = f"https://en.wikipedia.org/wiki/{PAGE}"
    w = open(sys.argv[1]).read() if len(sys.argv) > 1 else wikitext()
    for name, start, end in majors(w):
        print(f"{start},major_start,{name},{src}")
        print(f"{end},major_end,{name},{src}")
