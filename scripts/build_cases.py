"""Rebuild the case rows of docs/pipeline/events.csv from a hand-kept third-party table (S-09).
Only dates are taken; the table's text is not copied."""
import base64
import datetime
import json
import re
import subprocess
import sys

REPO = "azzaazeel/Next-js-Blog-Boilerplate-master"
PATH = "data-raw/CS2%20Container%20Drop%20Pool.md"
SRC = f"https://github.com/{REPO}/blob/main/{PATH}"


def table() -> str:
    out = subprocess.run(["gh", "api", f"repos/{REPO}/contents/{PATH}"], capture_output=True, text=True, check=True)
    return base64.b64decode(json.loads(out.stdout)["content"]).decode()


def day(s: str) -> datetime.date | None:
    s = s.strip().replace("June", "Jun").replace("July", "Jul")
    try:
        return datetime.datetime.strptime(s, "%Y, %d %b").date()
    except ValueError:
        return None


def rows(text: str):
    for line in text.splitlines():
        cols = line.split("\t")
        if len(cols) < 2 or not (released := day(cols[1])):
            continue
        name = cols[0].strip()
        if not name.endswith(("Case", "Terminal")):
            continue  # sticker capsules in the table have no pool dates
        yield released, "case_release", name
        if len(cols) > 2 and (to_rare := day(cols[2])):
            yield to_rare, "case_to_rare_pool", name
        if len(cols) > 3 and (ended := day(cols[3])):
            yield ended, "case_discontinued", name


if __name__ == "__main__":
    text = open(sys.argv[1]).read() if len(sys.argv) > 1 else table()
    for d, kind, name in sorted(set(rows(text))):
        print(f"{d},{kind},{name},{SRC}")
