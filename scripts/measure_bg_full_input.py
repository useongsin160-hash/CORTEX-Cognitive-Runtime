"""B10 measurement — BasalGanglia recommendation: synapse-only vs full input.

After B10 plumbs PFC confidence, LC ne_level ({0,1}), and the RPE recent counts
into the BG advisory, BG runs on its full score (synapse 0.4 + pfc 0.3 + rpe 0.05
+ lc 0.1) instead of synapse-only. This harness measures the consequence: for a
grid of cells it compares BG's selected candidate_type with synapse-only inputs
(pfc/lc/rpe absent — the pre-B10 reality) vs full inputs (the post-B10 values),
and reports the agreement rate + the distribution shift.

The full-input values are REAL-shaped, not invented: pfc_confidence sweeps PFC's
own per-step confidences, ne_level is the faithful {0,1} bool surface, and the
RPE counts are plausible recent-window tallies. A shift here is the real signals
acting through the score weights — the evidence C2 needs before flipping
applied=True. The advisor is the real production class; applied stays False.

Run: python scripts/measure_bg_full_input.py  (writes docs/measurements/).
Pure: run_measurement() returns the report dict and writes nothing.
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
)

CATEGORIES = (
    "coding", "game_design", "math_logic", "writing",
    "data_analysis", "system_design", "general",
)
DIFFICULTIES = (1, 2, 3, 4, 5)
SYNAPSE_WEIGHTS = (0.3, 0.5, 0.7)
# PFC's real per-step confidences (general/category-ish/strong-cue).
PFC_CONFIDENCES = (0.1, 0.6, 0.9)
# Plausible recent-window RPE sign tallies (neutral / positive-leaning / negative).
RPE_COUNTS = ((0, 0), (3, 1), (1, 3))


async def _selected_type(advisor, category, difficulty, synapse_weight,
                         *, pfc_conf, ne, rpe):
    pfc_snapshot = (
        None if pfc_conf is None
        else SimpleNamespace(
            pfc_active=True, cue_type="category_fallback",
            confidence=pfc_conf, intent_category=None,
        )
    )
    lc_snapshot = None if ne is None else SimpleNamespace(
        ne_level=ne, intent_label=None,
    )
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


async def _collect() -> dict:
    advisor = BasalGangliaAdvisor()
    rows: list[dict] = []
    for category in CATEGORIES:
        for difficulty in DIFFICULTIES:
            for synapse_weight in SYNAPSE_WEIGHTS:
                # synapse-only baseline (pre-B10): pfc/lc/rpe absent.
                base = await _selected_type(
                    advisor, category, difficulty, synapse_weight,
                    pfc_conf=None, ne=None, rpe=(0, 0),
                )
                for pfc_conf in PFC_CONFIDENCES:
                    for rpe in RPE_COUNTS:
                        ne = 1.0 if difficulty >= 4 else 0.0
                        full = await _selected_type(
                            advisor, category, difficulty, synapse_weight,
                            pfc_conf=pfc_conf, ne=ne, rpe=rpe,
                        )
                        rows.append({
                            "category": category,
                            "difficulty": difficulty,
                            "synapse_weight": synapse_weight,
                            "pfc_confidence": pfc_conf,
                            "ne_level": ne,
                            "rpe_recent": list(rpe),
                            "synapse_only_type": base,
                            "full_input_type": full,
                            "changed": base != full,
                        })
    return {"rows": rows}


def _distribution(rows, key):
    out: dict[str, int] = {}
    for r in rows:
        out[r[key]] = out.get(r[key], 0) + 1
    return dict(sorted(out.items()))


def run_measurement() -> dict:
    """Pure entry point — returns the report dict (writes nothing)."""
    data = asyncio.run(_collect())
    rows = data["rows"]
    changed = sum(1 for r in rows if r["changed"])
    total = len(rows)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "harness": "measure_bg_full_input",
        "deterministic": True,
        "bg_applied": False,
        "grid": {
            "categories": list(CATEGORIES),
            "difficulties": list(DIFFICULTIES),
            "synapse_weights": list(SYNAPSE_WEIGHTS),
            "pfc_confidences": list(PFC_CONFIDENCES),
            "rpe_counts": [list(c) for c in RPE_COUNTS],
        },
        "summary": {
            "comparisons": total,
            "changed": changed,
            "change_rate": round(changed / total, 4) if total else 0.0,
            "synapse_only_distribution": _distribution(rows, "synapse_only_type"),
            "full_input_distribution": _distribution(rows, "full_input_type"),
        },
        "rows": rows,
        "notes": {
            "honesty": (
                "Full-input values are real-shaped, not invented: pfc_confidence "
                "sweeps PFC's own per-step confidences, ne_level is the faithful "
                "{0,1} bool surface (NE has no continuous value), rpe counts are "
                "plausible recent-window tallies. A change is the real pfc/lc/rpe "
                "terms acting through the score weights (0.3/0.1/0.05)."
            ),
            "bg_applied": (
                "BG.applied stays False (type hard-lock). B10 fills inputs only; "
                "the recommendation is still never consumed. C2 decides apply."
            ),
        },
    }


def _to_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        "# BasalGanglia full-input measurement (B10)",
        "",
        f"- generated_at: {report['generated_at']}",
        f"- comparisons: {s['comparisons']}",
        f"- changed (synapse-only != full): {s['changed']} "
        f"({s['change_rate'] * 100:.1f}%)",
        f"- bg_applied: {report['bg_applied']}",
        "",
        "## selected candidate_type distribution",
        "",
        "| input | distribution |",
        "|-------|--------------|",
        f"| synapse-only | {s['synapse_only_distribution']} |",
        f"| full-input | {s['full_input_distribution']} |",
        "",
        "## notes",
        "",
        f"- {report['notes']['honesty']}",
        f"- {report['notes']['bg_applied']}",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    report = run_measurement()
    out_dir = Path(__file__).resolve().parents[1] / "docs" / "measurements"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bg_full_input.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "bg_full_input.md").write_text(
        _to_markdown(report), encoding="utf-8"
    )
    s = report["summary"]
    print(
        f"[measure_bg_full_input] comparisons={s['comparisons']} "
        f"changed={s['changed']} ({s['change_rate'] * 100:.1f}%)"
    )


if __name__ == "__main__":
    main()
