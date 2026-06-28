"""Phase 3 STEP 3.1 — per-category similarity distribution measurement.

Measurement-only. No production module is touched. Produces two output
files under docs/measurements/ so the user can pick an epinephrine
trigger threshold from empirical data rather than the pre-spec 0.50
guess that predates mean-centering.

For every seed (en + ko) we compute:
  - self_similarity: dot(centered_query, own_centroid)
  - cross_similarity: dot(centered_query, every other centroid)
  - margin = self_similarity - max(cross_similarity)

Aggregated per-category and by the HIGH_COMPUTE / LOW_COMPUTE bucket
the user defined for the Epinephrine trigger.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.routing.centroid_store import CACHE_VERSION, CentroidStore  # noqa: E402

SEED_PATH = ROOT / "tests" / "phase3" / "seed_queries.json"
CACHE_PATH = ROOT / "data" / "centroids.npz"
OUT_DIR = ROOT / "docs" / "measurements"
MD_PATH = OUT_DIR / "similarity_distribution_step3_1.md"
JSON_PATH = OUT_DIR / "similarity_distribution_step3_1.json"

HIGH_COMPUTE: frozenset[str] = frozenset({
    "coding", "math_logic", "data_analysis", "system_design",
})
LOW_COMPUTE: frozenset[str] = frozenset({
    "game_design", "writing", "general",
})


def _percentiles(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {}
    arr = np.asarray(values, dtype=float)
    return {
        "n": int(arr.size),
        "min": float(arr.min()),
        "p25": float(np.percentile(arr, 25)),
        "p50": float(np.percentile(arr, 50)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
        "max": float(arr.max()),
        "mean": float(arr.mean()),
        "variance": float(arr.var()),
    }


def _fire_rate(threshold: float, high_sims: list[float], low_sims: list[float]) -> dict:
    high_fired = sum(1 for s in high_sims if s >= threshold)
    low_fired = sum(1 for s in low_sims if s >= threshold)
    return {
        "threshold": threshold,
        "high_fired": high_fired,
        "high_total": len(high_sims),
        "high_coverage": high_fired / len(high_sims) if high_sims else 0.0,
        "low_fired": low_fired,
        "low_total": len(low_sims),
        "low_fp_rate": low_fired / len(low_sims) if low_sims else 0.0,
    }


async def measure() -> dict:
    """Run the measurement, write JSON + Markdown, return the payload."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    store = CentroidStore(cache_path=CACHE_PATH)
    await store.build_from_seeds(SEED_PATH)
    if store.global_mean is None:
        raise RuntimeError("CentroidStore.global_mean missing — cache rebuild required")

    seeds = json.loads(SEED_PATH.read_text(encoding="utf-8"))["categories"]
    categories = list(seeds.keys())
    centroids = {cat: await store.get_centroid(cat) for cat in categories}
    global_mean = store.global_mean

    per_category: dict[str, dict] = {}
    for cat, items in seeds.items():
        self_sims: list[float] = []
        cross_by_other: dict[str, list[float]] = {
            other: [] for other in categories if other != cat
        }
        margins: list[float] = []
        per_seed: list[dict] = []

        for item in items:
            for lang in ("en", "ko"):
                text = item[lang]
                raw_emb = await store.embed_text(text)
                centered = np.asarray(raw_emb, dtype=np.float32) - global_mean
                norm = float(np.linalg.norm(centered))
                if norm > 0.0:
                    centered = centered / norm

                all_sims = {
                    other: float(np.dot(centered, centroids[other]))
                    for other in categories
                }
                self_sim = all_sims[cat]
                self_sims.append(self_sim)

                best_other = max(
                    (k for k in all_sims if k != cat),
                    key=lambda k: all_sims[k],
                )
                best_other_sim = all_sims[best_other]

                for other in cross_by_other:
                    cross_by_other[other].append(all_sims[other])
                margins.append(self_sim - best_other_sim)

                per_seed.append({
                    "id": item.get("id"),
                    "lang": lang,
                    "type": item.get("type"),
                    "self_sim": self_sim,
                    "nearest_other": best_other,
                    "nearest_other_sim": best_other_sim,
                    "margin": self_sim - best_other_sim,
                })

        per_category[cat] = {
            "group": "HIGH_COMPUTE" if cat in HIGH_COMPUTE else "LOW_COMPUTE",
            "self_similarity": _percentiles(self_sims),
            "cross_similarity_by_other": {
                other: _percentiles(values)
                for other, values in cross_by_other.items()
            },
            "margin": _percentiles(margins),
            "per_seed": per_seed,
        }

    # Group aggregates
    high_sims: list[float] = []
    low_sims: list[float] = []
    for c in HIGH_COMPUTE:
        high_sims.extend(p["self_sim"] for p in per_category[c]["per_seed"])
    for c in LOW_COMPUTE:
        low_sims.extend(p["self_sim"] for p in per_category[c]["per_seed"])
    high_agg = _percentiles(high_sims)
    low_agg = _percentiles(low_sims)

    # Confusion pair ranking (smallest margin first)
    confusion_pairs = []
    for cat, data in per_category.items():
        cross_means = {
            other: stat["mean"]
            for other, stat in data["cross_similarity_by_other"].items()
        }
        nearest = max(cross_means, key=lambda k: cross_means[k])
        confusion_pairs.append((
            cat, nearest,
            data["self_similarity"]["mean"] - cross_means[nearest],
        ))
    confusion_pairs.sort(key=lambda x: x[2])

    # Threshold candidates derived from HIGH-bucket self-sim percentiles.
    candidates = {
        "conservative_high_p75": _fire_rate(high_agg["p75"], high_sims, low_sims),
        "balanced_high_p50": _fire_rate(high_agg["p50"], high_sims, low_sims),
        "aggressive_high_p25": _fire_rate(high_agg["p25"], high_sims, low_sims),
        "legacy_0_50": _fire_rate(0.50, high_sims, low_sims),
    }

    measured_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "metadata": {
            "measured_at": measured_at,
            "cache_version": CACHE_VERSION,
            "embedder": "intfloat/multilingual-e5-base",
            "seed_corpus": str(SEED_PATH.relative_to(ROOT)),
            "samples_per_category": len(per_category[categories[0]]["per_seed"]),
        },
        "high_compute": sorted(HIGH_COMPUTE),
        "low_compute": sorted(LOW_COMPUTE),
        "per_category": per_category,
        "groups": {"HIGH_COMPUTE": high_agg, "LOW_COMPUTE": low_agg},
        "confusion_pairs_smallest_first": [
            {"category": a, "nearest_other": b, "margin": m}
            for a, b, m in confusion_pairs
        ],
        "threshold_candidates": candidates,
    }

    JSON_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_markdown(payload)
    return payload


def _write_markdown(payload: dict) -> None:
    high_agg = payload["groups"]["HIGH_COMPUTE"]
    low_agg = payload["groups"]["LOW_COMPUTE"]
    cat_order = sorted(HIGH_COMPUTE) + sorted(LOW_COMPUTE)

    lines: list[str] = [
        "# Similarity Distribution Measurement — STEP 3.1",
        "",
        "## Purpose",
        "에피네프린 threshold 산정을 위한 실측 데이터 수집.",
        "mean-centering 좌표계의 실제 similarity 분포 확인.",
        "",
        "## Measurement Setup",
        f"- 측정 일시: {payload['metadata']['measured_at']}",
        f"- 임베더: {payload['metadata']['embedder']}",
        f"- 정규화: global-mean centering (CACHE_VERSION {payload['metadata']['cache_version']})",
        f"- 측정 대상: {payload['metadata']['seed_corpus']} "
        f"(70 시드 × en/ko = {payload['metadata']['samples_per_category'] * len(cat_order)} 샘플)",
        f"- HIGH_COMPUTE = {', '.join(sorted(HIGH_COMPUTE))}",
        f"- LOW_COMPUTE = {', '.join(sorted(LOW_COMPUTE))}",
        "",
        "## Per-Category Self-Similarity Distribution",
        "",
        "| Category | n | min | p25 | p50 | p75 | p90 | max | mean | variance |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for cat in cat_order:
        s = payload["per_category"][cat]["self_similarity"]
        lines.append(
            f"| {cat} | {s['n']} | {s['min']:+.4f} | {s['p25']:+.4f} | "
            f"{s['p50']:+.4f} | {s['p75']:+.4f} | {s['p90']:+.4f} | "
            f"{s['max']:+.4f} | {s['mean']:+.4f} | {s['variance']:.4f} |"
        )

    lines += [
        "",
        "## Cross-Similarity (Confusion Risk)",
        "",
        "각 카테고리의 self_mean vs 가장 가까운 다른 카테고리 centroid mean.",
        "margin = self_mean − nearest_other_mean. 음수면 분류 오류.",
        "",
        "| Category | self_mean | nearest_other | nearest_other_mean | margin |",
        "|---|---|---|---|---|",
    ]
    for cat in cat_order:
        data = payload["per_category"][cat]
        cross_means = {
            other: stat["mean"]
            for other, stat in data["cross_similarity_by_other"].items()
        }
        nearest = max(cross_means, key=lambda k: cross_means[k])
        self_mean = data["self_similarity"]["mean"]
        nm = cross_means[nearest]
        lines.append(
            f"| {cat} | {self_mean:+.4f} | {nearest} | {nm:+.4f} | "
            f"{self_mean - nm:+.4f} |"
        )

    lines += [
        "",
        "## HIGH_COMPUTE vs LOW_COMPUTE",
        "",
        "| Group | n | min | p25 | p50 | p75 | p90 | mean |",
        "|---|---|---|---|---|---|---|---|",
        f"| HIGH ({', '.join(sorted(HIGH_COMPUTE))}) | {high_agg['n']} | "
        f"{high_agg['min']:+.4f} | {high_agg['p25']:+.4f} | "
        f"{high_agg['p50']:+.4f} | {high_agg['p75']:+.4f} | "
        f"{high_agg['p90']:+.4f} | {high_agg['mean']:+.4f} |",
        f"| LOW ({', '.join(sorted(LOW_COMPUTE))}) | {low_agg['n']} | "
        f"{low_agg['min']:+.4f} | {low_agg['p25']:+.4f} | "
        f"{low_agg['p50']:+.4f} | {low_agg['p75']:+.4f} | "
        f"{low_agg['p90']:+.4f} | {low_agg['mean']:+.4f} |",
        "",
        "## Confusion Pair Ranking (smallest margin first)",
        "",
        "| Rank | Category | Nearest Other | Margin |",
        "|---|---|---|---|",
    ]
    for i, pair in enumerate(payload["confusion_pairs_smallest_first"], start=1):
        lines.append(
            f"| {i} | {pair['category']} | {pair['nearest_other']} | "
            f"{pair['margin']:+.4f} |"
        )

    lines += [
        "",
        "## Threshold Candidates (HIGH_COMPUTE seed self-sim percentiles)",
        "",
        "HIGH coverage = 실제 HIGH 시드 중 self_sim ≥ threshold 비율.",
        "LOW FP rate = LOW 시드가 (자기 centroid 대비) threshold 통과 비율 — 발동 오인 위험 대용 지표.",
        "",
        "| Candidate | Threshold | HIGH coverage | LOW FP rate |",
        "|---|---|---|---|",
    ]
    label_order = [
        ("conservative_high_p75", "Conservative (HIGH p75)"),
        ("balanced_high_p50", "Balanced (HIGH p50)"),
        ("aggressive_high_p25", "Aggressive (HIGH p25)"),
        ("legacy_0_50", "Legacy (pre-mean-centering 0.50)"),
    ]
    for key, label in label_order:
        c = payload["threshold_candidates"][key]
        lines.append(
            f"| {label} | {c['threshold']:+.4f} | "
            f"{c['high_fired']}/{c['high_total']} = {c['high_coverage']:.1%} | "
            f"{c['low_fired']}/{c['low_total']} = {c['low_fp_rate']:.1%} |"
        )

    bal = payload["threshold_candidates"]["balanced_high_p50"]
    cons = payload["threshold_candidates"]["conservative_high_p75"]
    agg = payload["threshold_candidates"]["aggressive_high_p25"]
    leg = payload["threshold_candidates"]["legacy_0_50"]

    lines += [
        "",
        "## Threshold Recommendation",
        "",
        "사용자 결정 필요 사항:",
        "- 어느 threshold 후보를 채택할지",
        "- HIGH/LOW 카테고리별 threshold 차등 적용 여부",
        "",
        "trade-off 요약:",
        "",
        f"- **Conservative (HIGH p75 = {cons['threshold']:+.4f})** — "
        f"진짜 HIGH 쿼리의 {cons['high_coverage']:.0%}만 발동, "
        f"LOW FP {cons['low_fp_rate']:.0%}. 비용 절감 우선.",
        f"- **Balanced (HIGH p50 = {bal['threshold']:+.4f})** — "
        f"HIGH {bal['high_coverage']:.0%} / LOW FP {bal['low_fp_rate']:.0%}. "
        f"일반적 시작점.",
        f"- **Aggressive (HIGH p25 = {agg['threshold']:+.4f})** — "
        f"HIGH {agg['high_coverage']:.0%} / LOW FP {agg['low_fp_rate']:.0%}. "
        f"미발동 위험 최소.",
        "",
        f"legacy 0.50 비교: HIGH {leg['high_coverage']:.0%} / "
        f"LOW FP {leg['low_fp_rate']:.0%}.",
        "",
        "## References",
        "- 설계 문서: Neuromodulators 섹션 (에피네프린 발동 조건)",
        "- ADR-001 (latency budget under multilingual-e5-base)",
        "- STEP 1.5 closeout (mean-centering rationale)",
        "",
        f"Raw per-seed measurements: `{JSON_PATH.name}`",
    ]
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _main() -> None:
    payload = await measure()
    high = payload["groups"]["HIGH_COMPUTE"]
    low = payload["groups"]["LOW_COMPUTE"]
    worst = payload["confusion_pairs_smallest_first"][0]
    print(
        f"[measure] HIGH n={high['n']}  p25={high['p25']:+.4f}  "
        f"p50={high['p50']:+.4f}  p75={high['p75']:+.4f}  mean={high['mean']:+.4f}"
    )
    print(
        f"[measure] LOW  n={low['n']}  p25={low['p25']:+.4f}  "
        f"p50={low['p50']:+.4f}  p75={low['p75']:+.4f}  mean={low['mean']:+.4f}"
    )
    print(
        f"[measure] worst confusion: {worst['category']} ↔ "
        f"{worst['nearest_other']}  margin={worst['margin']:+.4f}"
    )
    for key in ("conservative_high_p75", "balanced_high_p50",
                "aggressive_high_p25", "legacy_0_50"):
        c = payload["threshold_candidates"][key]
        print(
            f"[measure] {key:24s}  t={c['threshold']:+.4f}  "
            f"HIGH cov={c['high_coverage']:.1%}  LOW fp={c['low_fp_rate']:.1%}"
        )
    print(f"[measure] wrote {MD_PATH.relative_to(ROOT)}")
    print(f"[measure] wrote {JSON_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(_main())
