"""Build and persist category centroids from tests/phase3/seed_queries.json.

Compares three centroid strategies side-by-side so the bilingual_average
trade-off is observable:
  - en_only   : diagnostic, centroid built from `en` text only
  - ko_only   : diagnostic, centroid built from `ko` text only
  - bilingual : production strategy, persisted to data/centroids.npz

Pairs above 0.85 are flagged — that indicates the seed corpus is not
separating the categories enough and needs rework.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.routing.centroid_store import CentroidStore, Strategy  # noqa: E402

SEED_PATH = ROOT / "tests" / "phase3" / "seed_queries.json"
CACHE_PATH = ROOT / "data" / "centroids.npz"
SEPARATION_WARN_THRESHOLD = 0.85


async def _measure(strategy: Strategy, *, persist_to: Path | None = None) -> dict[str, float]:
    """Build centroids under a given strategy, return separation summary."""
    if persist_to is None:
        # Send the cache write to a throw-away path so diagnostic strategies
        # never overwrite the production npz.
        tmp_path = Path(tempfile.mkdtemp()) / f"centroids_{strategy}.npz"
        store = CentroidStore(cache_path=tmp_path)
    else:
        store = CentroidStore(cache_path=persist_to)

    t0 = time.perf_counter()
    await store.build_from_seeds(SEED_PATH, strategy=strategy)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    matrix = await store.separation_matrix()
    sims = list(matrix.values())
    worst = max(sims)
    best = min(sims)
    mean = sum(sims) / len(sims)
    return {
        "elapsed_ms": elapsed_ms,
        "worst": worst,
        "best": best,
        "mean": mean,
        "store": store,
        "matrix": matrix,
    }


async def main() -> None:
    print(f"[centroids] seed   : {SEED_PATH}")
    print(f"[centroids] cache  : {CACHE_PATH}")

    # Wipe stale production cache so the rebuild is observable.
    if CACHE_PATH.exists():
        CACHE_PATH.unlink()
        print("[centroids] previous data/centroids.npz removed")

    print("\n[centroids] strategy comparison ----------------------------------")
    en = await _measure("en_only")
    ko = await _measure("ko_only")
    bi = await _measure("bilingual", persist_to=CACHE_PATH)

    print(f"  en_only    worst={en['worst']:+.4f}  best={en['best']:+.4f}  "
          f"mean={en['mean']:+.4f}  ({en['elapsed_ms']:.0f} ms)")
    print(f"  ko_only    worst={ko['worst']:+.4f}  best={ko['best']:+.4f}  "
          f"mean={ko['mean']:+.4f}  ({ko['elapsed_ms']:.0f} ms)")
    print(f"  bilingual  worst={bi['worst']:+.4f}  best={bi['best']:+.4f}  "
          f"mean={bi['mean']:+.4f}  ({bi['elapsed_ms']:.0f} ms)  [persisted]")

    delta_en_bi = bi["worst"] - en["worst"]
    delta_ko_bi = bi["worst"] - ko["worst"]
    print(f"\n  Δ(bilingual - en_only)  = {delta_en_bi:+.4f}  (must stay within ±0.15)")
    print(f"  Δ(bilingual - ko_only)  = {delta_ko_bi:+.4f}")

    print("\n[centroids] bilingual pairwise cosine (lower = better separation)")
    store = bi["store"]
    matrix = bi["matrix"]
    warnings: list[tuple[str, str, float]] = []
    for (a, b), sim in sorted(matrix.items(), key=lambda kv: -kv[1]):
        marker = "  WARN" if sim >= SEPARATION_WARN_THRESHOLD else ""
        print(f"  {a:<14s} <-> {b:<14s}  sim={sim:+.4f}{marker}")
        if sim >= SEPARATION_WARN_THRESHOLD:
            warnings.append((a, b, sim))

    print()
    for category in store.categories:
        vec = await store.get_centroid(category)
        print(f"  - {category:<14s} dim={vec.shape[0]}  ||v||≈1.0")

    if warnings:
        print(f"\n[centroids] WARNING: {len(warnings)} pair(s) >= {SEPARATION_WARN_THRESHOLD}")
        for a, b, sim in warnings:
            print(f"  re-seed targets: {a} / {b}  (sim={sim:.4f})")
    else:
        print(f"\n[centroids] OK: no pair exceeds {SEPARATION_WARN_THRESHOLD}")


if __name__ == "__main__":
    asyncio.run(main())
