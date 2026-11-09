import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from cs2trader.errors import SchemaError, SourceUnavailable
from cs2trader.models import Category, ItemInfo

ITEMS_DB_URL = "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/all.json"
CACHE_FILE = "items_all.json"

_WEAR = re.compile(r" \((Factory New|Minimal Wear|Field-Tested|Well-Worn|Battle-Scarred)\)$")
_PREFIX = re.compile(r"^(StatTrak™ |Souvenir )")
_STAR_PREFIX = re.compile(r"^★ (StatTrak™ )?")

_SIMPLE = {
    "sticker": Category.STICKER, "sticker_slab": Category.STICKER, "graffiti": Category.GRAFFITI,
    "patch": Category.PATCH, "keychain": Category.CHARM, "agent": Category.AGENT,
    "music_kit": Category.MUSIC_KIT,
}
_CRATE_TYPES = {
    "Case": Category.CASE, "Souvenir": Category.SOUVENIR_PACKAGE, "Music Kit Box": Category.MUSIC_KIT_BOX,
    "Pins": Category.CAPSULE, "Souvenir Highlight": Category.CAPSULE, "Graffiti": Category.CAPSULE,
}


def fetch_items_db(cache_dir: Path) -> list[dict[str, Any]]:
    """Raw ByMykel records; the 78 MB file is downloaded at most once per UTC day."""
    path = cache_dir / CACHE_FILE
    today = datetime.now(UTC).date()
    if not (path.exists() and datetime.fromtimestamp(path.stat().st_mtime, UTC).date() == today):
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        try:
            with httpx.stream("GET", ITEMS_DB_URL, timeout=120, follow_redirects=True) as r:
                r.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
        except httpx.HTTPError as e:
            tmp.unlink(missing_ok=True)
            if not path.exists():
                raise SourceUnavailable("items_db", type(e).__name__) from e
        else:
            tmp.replace(path)
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise SchemaError("items_db", f"cache {path} is not valid JSON") from e
    return list(data.values()) if isinstance(data, dict) else list(data)


def _kind(rec: Mapping[str, Any]) -> str:
    # ids look like "skin-e757fd7191f9_0", "crate-1210", "collection-set-community-3"
    return str(rec.get("id", "")).split("-")[0]


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    # formats vary: "2014/06/11", "2015-8-10"
    try:
        y, m, d = (int(x) for x in re.split(r"[/-]", s))
        return date(y, m, d)
    except ValueError:
        return None


def _build_index(db: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Mapping[str, Any]], dict[str, date]]:
    by_name: dict[str, Mapping[str, Any]] = {}
    skin_dates: dict[str, date] = {}
    nameless: list[Mapping[str, Any]] = []
    for rec in db:
        kind = _kind(rec)
        if kind == "collection":
            d = _parse_date(rec.get("release_date"))
            if d:
                for s in rec.get("contains", []):
                    sid = s.get("id")
                    if sid and (sid not in skin_dates or d < skin_dates[sid]):
                        skin_dates[sid] = d
            continue
        mhn = rec.get("market_hash_name")
        if mhn:
            by_name.setdefault(mhn, rec)
        else:
            nameless.append(rec)
    # DB leaves market_hash_name null for stickers, music kits and some crates; the name field matches instead
    for rec in nameless:
        name = rec.get("name")
        if not name:
            continue
        by_name.setdefault(name, rec)
        if _kind(rec) == "music_kit":
            by_name.setdefault("Music Kit | " + name, rec)
    return by_name, skin_dates


def _from_record(rec: Mapping[str, Any]) -> Category:
    kind = _kind(rec)
    if kind == "skin":
        group = (rec.get("category") or {}).get("name")
        return Category.KNIFE if group == "Knives" else Category.GLOVE if group == "Gloves" else Category.WEAPON_SKIN
    if kind == "crate":
        ctype = rec.get("type")
        if ctype and "Capsule" in ctype:
            return Category.CAPSULE
        return _CRATE_TYPES.get(ctype, Category.OTHER)
    return _SIMPLE.get(kind, Category.OTHER)


def _from_name(name: str) -> Category | None:
    base = _PREFIX.sub("", name)
    if base.startswith("★"):
        return Category.GLOVE if re.search(r"Gloves|Wraps", base) else Category.KNIFE
    if name.startswith("Sticker |"):
        return Category.STICKER
    if name.startswith(("Sealed Graffiti |", "Graffiti |")):
        return Category.GRAFFITI
    if name.startswith("Patch |"):
        return Category.PATCH
    # Souvenir Charm highlights are missing from the DB (S-04)
    if name.startswith(("Charm |", "Souvenir Charm |")):
        return Category.CHARM
    if "Music Kit Box" in name:
        return Category.MUSIC_KIT_BOX
    if "Music Kit" in name:
        return Category.MUSIC_KIT
    if "Souvenir Package" in name:
        return Category.SOUVENIR_PACKAGE
    if re.search(r"Sticker Capsule|Autograph Capsule|Patch Capsule|Pins Capsule|Graffiti Box", name):
        return Category.CAPSULE
    if re.search(r"\bCase\b", name):
        return Category.CASE
    if " | " in name and _WEAR.search(name):
        return Category.WEAPON_SKIN
    if re.search(r"Key$|Pass$|Tag$|Tool|Pin$| Coin$|Viewer", name):
        return Category.OTHER
    return None


def classify(names: Sequence[str], db: Sequence[Mapping[str, Any]]) -> list[ItemInfo]:
    by_name, skin_dates = _build_index(db)
    out: list[ItemInfo] = []
    for name in names:
        base = _WEAR.sub("", name)
        stattrak = "StatTrak™" in name.split(" | ")[0]
        souvenir = name.startswith("Souvenir ") and not name.startswith("Souvenir Charm")
        rec = next((by_name[c] for c in (name, base, _PREFIX.sub("", base), _STAR_PREFIX.sub("★ ", base))
                    if c in by_name), None)
        if rec is not None:
            kind = _kind(rec)
            weapon = (rec.get("weapon") or {}).get("name") if kind == "skin" else None
            release = (skin_dates.get(rec.get("skin_id")) if kind == "skin"
                       else _parse_date(rec.get("first_sale_date")) if kind == "crate" else None)
            out.append(ItemInfo(name, _from_record(rec), "db", weapon, (rec.get("rarity") or {}).get("name"),
                                stattrak, souvenir, release))
            continue
        cat = _from_name(name)
        out.append(ItemInfo(name, cat or Category.UNKNOWN, "rule" if cat else "none", None, None,
                            stattrak, souvenir, None))
    return out
