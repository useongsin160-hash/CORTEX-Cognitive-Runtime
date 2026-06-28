"""Phase 3 STEP 3.1 — validate the similarity distribution measurement.

The measurement script lives at scripts/measure_similarity_distribution.py
and is the sole input the user will use to pick an epinephrine
threshold. This test guards the contract of its outputs.

We invoke the script's `measure()` coroutine directly (not subprocess)
so failures surface with a real Python traceback. The fixture is
module-scoped because the embedder pass over 140 seeds takes ~10 s.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.measure_similarity_distribution import (  # noqa: E402
    HIGH_COMPUTE,
    JSON_PATH,
    LOW_COMPUTE,
    MD_PATH,
    measure,
)

EXPECTED_CATEGORIES = {
    "coding", "game_design", "math_logic", "writing",
    "data_analysis", "system_design", "general",
}


@pytest.fixture(scope="module")
async def payload() -> dict:
    """Run the measurement once for the module."""
    return await measure()


def test_output_files_created():
    """measure() must have written both Markdown and JSON next to itself."""
    assert MD_PATH.exists(), f"missing markdown report at {MD_PATH}"
    assert JSON_PATH.exists(), f"missing json payload at {JSON_PATH}"
    parsed = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    assert "per_category" in parsed
    assert "groups" in parsed


@pytest.mark.asyncio
async def test_payload_covers_all_seven_categories(payload):
    assert set(payload["per_category"].keys()) == EXPECTED_CATEGORIES


@pytest.mark.asyncio
async def test_payload_groups_match_user_spec(payload):
    """HIGH = coding/math_logic/data_analysis/system_design; LOW = the rest."""
    assert set(payload["high_compute"]) == HIGH_COMPUTE
    assert set(payload["low_compute"]) == LOW_COMPUTE
    for cat, data in payload["per_category"].items():
        assert data["group"] == (
            "HIGH_COMPUTE" if cat in HIGH_COMPUTE else "LOW_COMPUTE"
        )


@pytest.mark.asyncio
async def test_all_similarities_in_valid_range(payload):
    """Every recorded cosine sits in [-1, 1] (theoretical range)."""
    for cat, data in payload["per_category"].items():
        for stat_name in ("self_similarity", "margin"):
            stat = data[stat_name]
            assert -1.0 <= stat["min"] <= stat["max"] <= 1.0, (
                f"{cat}/{stat_name} out of range: min={stat['min']} "
                f"max={stat['max']}"
            )
        for other, stat in data["cross_similarity_by_other"].items():
            assert -1.0 <= stat["min"] <= stat["max"] <= 1.0, (
                f"{cat} vs {other} out of range"
            )
        for seed in data["per_seed"]:
            assert -1.0 <= seed["self_sim"] <= 1.0
            assert -1.0 <= seed["nearest_other_sim"] <= 1.0
            assert -1.0 <= seed["margin"] <= 1.0


@pytest.mark.asyncio
async def test_per_category_sample_size_matches_seeds(payload):
    """20 samples per category (10 seeds × en + ko)."""
    for cat, data in payload["per_category"].items():
        assert data["self_similarity"]["n"] == 20, (
            f"{cat} has {data['self_similarity']['n']} samples, expected 20"
        )
        assert len(data["per_seed"]) == 20


@pytest.mark.asyncio
async def test_threshold_candidates_present(payload):
    candidates = payload["threshold_candidates"]
    for key in (
        "conservative_high_p75",
        "balanced_high_p50",
        "aggressive_high_p25",
        "legacy_0_50",
    ):
        assert key in candidates, f"missing threshold candidate {key}"
        for field in ("threshold", "high_coverage", "low_fp_rate",
                      "high_fired", "high_total", "low_fired", "low_total"):
            assert field in candidates[key], (
                f"{key} missing field {field}"
            )


@pytest.mark.asyncio
async def test_confusion_ranking_sorted_ascending(payload):
    pairs = payload["confusion_pairs_smallest_first"]
    assert len(pairs) == 7, "one row per category expected"
    margins = [p["margin"] for p in pairs]
    assert margins == sorted(margins), "ranking must be ascending by margin"


@pytest.mark.asyncio
async def test_threshold_ordering_makes_sense(payload):
    """Aggressive (lowest threshold) must catch ≥ Balanced ≥ Conservative."""
    cs = payload["threshold_candidates"]
    assert cs["aggressive_high_p25"]["threshold"] <= cs["balanced_high_p50"]["threshold"]
    assert cs["balanced_high_p50"]["threshold"] <= cs["conservative_high_p75"]["threshold"]
    assert cs["aggressive_high_p25"]["high_coverage"] >= cs["balanced_high_p50"]["high_coverage"]
    assert cs["balanced_high_p50"]["high_coverage"] >= cs["conservative_high_p75"]["high_coverage"]
