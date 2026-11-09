from collections import Counter
from pathlib import Path
from typing import Any

from cs2trader.backfill import feed_universe
from cs2trader.models import Category
from cs2trader.sources.items_db import classify, fetch_items_db
from cs2trader.store import Store


def update_items(store: Store, names: list[str] | None, cache_dir: Path) -> dict[str, Any]:
    """Classify the latest asks universe plus every fetched name and store the result.

    `names` overrides the asks universe (the steam_fetch_log names are still added).
    """
    base = names if names is not None else feed_universe(store)
    logged = [r[0] for r in store.db.execute("SELECT name FROM steam_fetch_log").fetchall()]
    all_names = sorted(set(base) | set(logged))
    infos = classify(all_names, fetch_items_db(cache_dir))
    store.upsert_items(infos)
    counts = Counter(str(i.category) for i in infos)
    unknown = [i.market_hash_name for i in infos if i.category == Category.UNKNOWN]
    return {"n": len(infos), "counts": dict(counts), "unknown": len(unknown),
            "unknown_share": len(unknown) / len(infos) if infos else 0.0, "unknown_names": unknown[:20]}
