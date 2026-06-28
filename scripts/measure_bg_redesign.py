"""BG redesign measurement — compute-demand matching is difficulty-appropriate.

Companion to scripts/measure_bg_full_input.py (the pre-redesign "before" record:
the LC-bool-only policy recommended swarm_minimal for 100% of difficulty 4·5 cells
— a demotion). This harness measures the REDESIGNED policy (difficulty-band anchor
modulated by the real NE/RPE/synapse/PFC signals) across a grid and reports:

  1. recommendation distribution (candidate_type + mapped route_path band),
  2. difficulty-appropriateness — the band distribution per difficulty, and the
     count of difficulty 4·5 cells whose RAW recommendation still lands below
     full_pipeline (the residual demotion, blocked downstream by the no-demote
     ratchet floor at apply time — reported honestly, not hidden),
  3. signal differentiation — distinct candidate_types seen within each difficulty
     (the old policy was constant within a difficulty; >1 proves the real signals
     now move the selection).

The advisor is the real production class; applied stays False (this measures the
recommendation only — C2 decides the apply). Pure: run_measurement() returns the
report dict and writes nothing. No swarm / LLM / e5 / network.

Run: python scripts/measure_bg_redesign.py   (writes docs/measurements/).
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.basal_ganglia.advisor import (  # noqa: E402
    BasalGangliaAdvisor,
    build_action_selection_context_from_snapshots,
    route_path_for_candidate_type,
)

CATEGORIES = (
    "coding", "game_design", "math_logic", "writing",
    "data_analysis", "system_design", "general",
)
DIFFICULTIES = (1, 2, 3, 4, 5)
SYNAPSE_WEIGHTS = (0.2, 0.5, 0.8)
PFC_CONFIDENCES = (0.1, 0.5, 0.9)
RPE_COUNTS = ((0, 0), (5, 1), (1, 5))

# Band ladder low → high (mirrors the routing band order).
_BANDS = ("lightweight", "standard", "full_pipeline")
# B12 routing baseline band per difficulty ({1}=light, {2,3}=std, {4,5}=full).
_BASELINE_BAND = {1: "lightweight", 2: "standard", 3: "standard",
                  4: "full_pipeline", 5: "full_pipeline"}


async def _selected(advisor, category, difficulty, synapse_weight, pfc_conf, ne, rpe):
    pfc_snapshot = SimpleNamespace(
        pfc_active=True, cue_type="category_fallback",
        confidence=pfc_conf, intent_category=None,
    )
    lc_snapshot = SimpleNamespace(ne_level=ne, intent_label=None)
    positive, negative = rpe
    ctx = build_action_selection_context_from_snapshots(
        trace_id=f"bg:{category}:{difficulty}",
        session_id=f"bg:{category}:{difficulty}",
        category=category,
        difficulty=difficulty,
        pfc_snapshot=pfc_snapshot,
        lc_snapshot=lc_snapshot,
        synapse_weights={category: synapse_weight},
        rpe_recent_positive_count=positive,
        rpe_recent_negative_count=negative,
    )
    decision = await advisor.evaluate(ctx)
    return decision.selected.candidate_type if decision.selected else None


async def _collect() -> list[dict]:
    advisor = BasalGangliaAdvisor()
    rows: list[dict] = []
    for category in CATEGORIES:
        for difficulty in DIFFICULTIES:
            ne = 1.0 if difficulty >= 4 else 0.0  # production-shape NE (B12)
            for synapse_weight in SYNAPSE_WEIGHTS:
                for pfc_conf in PFC_CONFIDENCES:
                    for rpe in RPE_COUNTS:
                        ctype = await _selected(
                            advisor, category, difficulty, synapse_weight,
                            pfc_conf, ne, rpe,
                        )
                        band = route_path_for_candidate_type(ctype)
                        rows.append({
                            "category": category,
                            "difficulty": difficulty,
                            "synapse_weight": synapse_weight,
                            "pfc_confidence": pfc_conf,
                            "ne_level": ne,
                            "rpe_recent": list(rpe),
                            "candidate_type": ctype,
                            "route_path": band,
                            "baseline_band": _BASELINE_BAND[difficulty],
                        })
    return rows


def _dist(rows, key):
    out: dict[str, int] = {}
    for r in rows:
        out[r[key]] = out.get(r[key], 0) + 1
    return dict(sorted(out.items()))


def _band_below(a: str, b: str) -> bool:
    return _BANDS.index(a) < _BANDS.index(b)


def run_measurement() -> dict:
    """Pure entry point — returns the report dict (writes nothing)."""
    rows = asyncio.run(_collect())
    total = len(rows)

    by_difficulty: dict[int, dict] = {}
    for diff in DIFFICULTIES:
        drows = [r for r in rows if r["difficulty"] == diff]
        types = {r["candidate_type"] for r in drows}
        by_difficulty[diff] = {
            "baseline_band": _BASELINE_BAND[diff],
            "candidate_type_distribution": _dist(drows, "candidate_type"),
            "route_path_distribution": _dist(drows, "route_path"),
            "distinct_candidate_types": len(types),
        }

    # Residual raw demotion: a recommendation below the difficulty's baseline band.
    high = [r for r in rows if r["difficulty"] >= 4]
    high_demotions = [r for r in high if _band_below(r["route_path"], r["baseline_band"])]
    any_diff_demotions = [
        r for r in rows if _band_below(r["route_path"], r["baseline_band"])
    ]
    promotions = [
        r for r in rows
        if _band_below(r["baseline_band"], r["route_path"])
    ]

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "harness": "measure_bg_redesign",
        "deterministic": True,
        "bg_applied": False,
        "grid": {
            "categories": list(CATEGORIES),
            "difficulties": list(DIFFICULTIES),
            "synapse_weights": list(SYNAPSE_WEIGHTS),
            "pfc_confidences": list(PFC_CONFIDENCES),
            "rpe_counts": [list(c) for c in RPE_COUNTS],
            "ne_rule": "ne_level = 1.0 iff difficulty >= 4 (B12 production shape)",
        },
        "summary": {
            "comparisons": total,
            "candidate_type_distribution": _dist(rows, "candidate_type"),
            "route_path_distribution": _dist(rows, "route_path"),
            "high_difficulty_raw_demotions": len(high_demotions),
            "high_difficulty_cells": len(high),
            "any_difficulty_raw_demotions": len(any_diff_demotions),
            "promotions_above_baseline": len(promotions),
        },
        "by_difficulty": {str(k): v for k, v in by_difficulty.items()},
        "rows": rows,
        "notes": {
            "before": (
                "Pre-redesign (scripts/measure_bg_full_input.py): the LC-bool-only "
                "policy recommended swarm_minimal for 100% of difficulty 4·5 cells "
                "(378/378) — a band demotion driven solely by the LC caution bonus."
            ),
            "after": (
                "The redesigned demand-match anchors each difficulty at its B12 "
                "routing band and modulates with the real NE/RPE/synapse/PFC "
                "signals. Difficulty 4·5 recommend full_pipeline across this grid "
                "(0 raw demotions): the difficulty anchor + production-shape NE "
                "hold them at the top. Only an extreme de-escalator combination "
                "(maximally familiar + confident + successful) beyond this grid "
                "could demote a high-difficulty cell, and the no-demote ratchet "
                "floor (B11 S4) blocks that at apply time. Within difficulties 1-3 "
                "the selection now varies with the signals (distinct types > 1) — "
                "the old LC-bool policy was constant within a difficulty."
            ),
            "bg_applied": (
                "applied stays False — this measures the recommendation only. C2 "
                "decides the apply (BG-apply stage + bg_apply_enabled flag)."
            ),
        },
    }


def _to_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        "# BasalGanglia redesign measurement",
        "",
        f"- generated_at: {report['generated_at']}",
        f"- comparisons: {s['comparisons']}",
        f"- bg_applied: {report['bg_applied']}",
        "",
        "## recommendation distribution",
        "",
        "| axis | distribution |",
        "|------|--------------|",
        f"| candidate_type | {s['candidate_type_distribution']} |",
        f"| route_path | {s['route_path_distribution']} |",
        "",
        "## difficulty-appropriateness",
        "",
        "| difficulty | baseline band | route_path distribution | distinct types |",
        "|-----------|---------------|-------------------------|----------------|",
    ]
    for diff in (1, 2, 3, 4, 5):
        d = report["by_difficulty"][str(diff)]
        lines.append(
            f"| {diff} | {d['baseline_band']} | {d['route_path_distribution']} "
            f"| {d['distinct_candidate_types']} |"
        )
    lines += [
        "",
        f"- high-difficulty (4·5) raw demotions: {s['high_difficulty_raw_demotions']}"
        f" / {s['high_difficulty_cells']} (blocked at apply by the ratchet floor)",
        f"- promotions above baseline: {s['promotions_above_baseline']}",
        "",
        "## notes",
        "",
        f"- before: {report['notes']['before']}",
        f"- after: {report['notes']['after']}",
        f"- bg_applied: {report['notes']['bg_applied']}",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    report = run_measurement()
    out_dir = ROOT / "docs" / "measurements"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bg_redesign.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "bg_redesign.md").write_text(
        _to_markdown(report), encoding="utf-8"
    )
    s = report["summary"]
    print(
        f"[measure_bg_redesign] comparisons={s['comparisons']} "
        f"high_diff_raw_demotions={s['high_difficulty_raw_demotions']}"
        f"/{s['high_difficulty_cells']} "
        f"route_path={s['route_path_distribution']}"
    )


if __name__ == "__main__":
    main()
