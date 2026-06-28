"""B6 — 35-cell learning trajectory harness tests (faithful + latent 2-pass).

Guards: determinism, isolation (no production wiring / no LLM-e5), the BG raw-only
contract (NO agreement rate is ever emitted — C interprets), the difficulty-label
neutrality boundary (the core manipulation-boundary proof), the faithful-pass
promote inertness (current safety reality), and the latent-pass promote → ratchet
lock → decay release mechanism.
"""
from __future__ import annotations

import ast
import inspect

import pytest

import scripts.measure_3mode_ablation as harness
from app.api.schemas.context import CATEGORIES


def _imported_modules() -> set[str]:
    """Module names actually imported by the harness (AST, not string match —
    docstrings explaining what it AVOIDS must not produce false positives)."""
    tree = ast.parse(inspect.getsource(harness))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module)
    return mods


_VALID_CANDIDATE_TYPES = {
    "swarm_full", "swarm_minimal", "tier_1_5_augment", "fallback",
}
_VALID_PATHS = {"lightweight", "standard", "full_pipeline"}


@pytest.fixture(scope="module")
def report() -> dict:
    return harness.run_measurement()


def _all_keys(obj) -> set[str]:
    """Recursively collect every dict KEY (not values) in the report."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys |= _all_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            keys |= _all_keys(item)
    return keys


def _cells(report, pass_name=None, category=None):
    out = report["cells"]
    if pass_name is not None:
        out = [c for c in out if c["pass"] == pass_name]
    if category is not None:
        out = [c for c in out if c["category"] == category]
    return out


def _final_weight(cell):
    for p in reversed(cell["trajectory"]):
        if p["weight"] is not None:
            return p["weight"]
    return None


# --- determinism -----------------------------------------------------------


def test_deterministic_across_runs() -> None:
    a = harness.run_measurement()
    b = harness.run_measurement()
    a.pop("generated_at")
    b.pop("generated_at")
    assert a == b, "harness must be deterministic (identical output minus timestamp)"


# --- grid completeness (B12 full 1-5 range + 2 passes) ---------------------


def test_grid_and_cell_count(report) -> None:
    assert report["passes"] == ["faithful", "latent"]
    assert report["grid"]["difficulties"] == [1, 2, 3, 4, 5]  # full B12 range (old harness was 1-3)
    assert set(report["grid"]["categories"]) == set(CATEGORIES)
    # 2 passes x 7 categories x 5 difficulties.
    assert len(report["cells"]) == 2 * len(CATEGORIES) * 5
    for pass_name in ("faithful", "latent"):
        seen = {(c["category"], c["difficulty"]) for c in _cells(report, pass_name)}
        for cat in CATEGORIES:
            for d in (1, 2, 3, 4, 5):
                assert (cat, d) in seen
    # gate stays at production values (never relaxed by the harness).
    assert report["grid"]["params"]["min_confidence"] == 0.5
    assert report["grid"]["params"]["min_abs_prediction_error"] == 0.3


# --- BG raw-only contract (the core C0-spirit guard) -----------------------


def test_no_agreement_rate_anywhere(report) -> None:
    """B6 records raw pairs only — it must NEVER emit a compressed agreement metric.
    (Mapping candidate_type<->path and computing agreement is C's job.)"""
    keys = _all_keys(report)
    for forbidden in ("agreement_rate", "agreement", "bg_agreement", "match",
                      "agree", "bg_level", "routing_level"):
        assert forbidden not in keys, f"forbidden compressed metric key: {forbidden!r}"


def test_bg_observations_are_raw_side_by_side(report) -> None:
    obs = report["bg_observations"]
    assert len(obs) == len(CATEGORIES) * 5
    for o in obs:
        assert o["bg_recommended"] is None or o["bg_recommended"] in _VALID_CANDIDATE_TYPES
        assert o["routing_chose"] in _VALID_PATHS
        # no per-observation agreement/match flag.
        assert "match" not in o and "agreement" not in o


# --- isolation: no production wiring, no LLM/e5/network --------------------


def test_harness_does_not_import_production_app() -> None:
    mods = _imported_modules()
    assert "app.main" not in mods, "harness must not import production app wiring"
    assert not any(m == "app.main" or m.startswith("app.main.") for m in mods)


def test_harness_has_no_llm_or_embedder_or_network() -> None:
    mods = _imported_modules()
    forbidden_substrings = (
        "embedder", "sentence_transformers", "chromadb", "httpx",
        "openai", "anthropic", "torch", "transformers",
    )
    for m in mods:
        for bad in forbidden_substrings:
            assert bad not in m, f"harness must be LLM/e5/network-free, imports {m!r}"


def test_report_declares_isolation_and_determinism(report) -> None:
    assert report["deterministic"] is True
    assert report["production_behavior_change"] == 0
    assert "RAW ONLY" in report["notes"]["bg"]
    # the latent caveat must be present (fidelity boundary).
    assert "NOT production" in report["notes"]["latent_caveat"]


# --- difficulty-label neutrality (the manipulation-boundary proof) ---------


def test_neutrality_all_identical(report) -> None:
    """Cells differing ONLY in difficulty (same category → same archetype/outcome)
    must have bit-identical weight trajectories. Identical ⟹ difficulty selected
    only the cell address, never the delta — the boundary holds."""
    checks = report["neutrality_checks"]
    # one check per (pass, category).
    assert len(checks) == 2 * len(CATEGORIES)
    for n in checks:
        assert n["difficulties_compared"] == [1, 2, 3, 4, 5]
        assert n["identical_trajectory"] is True, (
            f"difficulty leaked into reward for {n['pass']}/{n['category']}"
        )


# --- faithful pass: B13 restored — promote fires, partial stays sub-gate -----


def test_faithful_promote_now_fires(report) -> None:
    """B13 proof: with the restored production reward source a clean, well-grounded
    cell now crosses the gate and rises above seed (it was inert before B13)."""
    seed = report["grid"]["params"]["seed_weight"]
    clean = [c for c in _cells(report, "faithful") if c["archetype"] == "clean"]
    assert clean
    for c in clean:
        fw = _final_weight(c)
        assert fw is not None and fw > seed, (
            f"faithful clean cell did not promote: {c['session']}"
        )
    # routing promotes too: the active route moves off the B12-native baseline.
    active = [m for m in report["mode_isolation"]
              if m["pass"] == "faithful" and m["cell"] == "coding:2" and m["mode"] == "active"]
    assert active and "full_pipeline" in active[0]["route_path_per_step"]
    # observe / dry_run still never move the path (weight unchanged).
    for mode in ("observe", "dry_run"):
        m = [x for x in report["mode_isolation"]
             if x["pass"] == "faithful" and x["cell"] == "coding:2" and x["mode"] == mode][0]
        assert m["distinct_paths"] == ["standard"]


def test_faithful_partial_stays_sub_gate(report) -> None:
    """Indiscriminate-praise guard: a 'neutral' cell (pipeline ran but no grounded
    context / clean finish) must NOT cross the gate — stays at seed."""
    neutral = [c for c in _cells(report, "faithful") if c["archetype"] == "neutral"]
    assert neutral
    for c in neutral:
        assert all(p["weight"] is None for p in c["trajectory"]), (
            f"faithful neutral cell unexpectedly mutated: {c['session']}"
        )


def test_faithful_demote_works(report) -> None:
    """Negative outcomes clear the gate — failing cells demote below seed. With
    B13 confidence restored this fires through the real production source."""
    seed = report["grid"]["params"]["seed_weight"]
    failing = [c for c in _cells(report, "faithful") if c["archetype"] == "failing"]
    assert failing
    for c in failing:
        fw = _final_weight(c)
        assert fw is not None and fw < seed, f"failing cell did not demote: {c['session']}"


# --- latent pass: promote → ratchet lock → decay release -------------------


def test_latent_promotes_routing(report) -> None:
    """The harness transfer source clears the gate, so a clean low-difficulty cell
    rises and its route promotes off the B12-native baseline (only in active)."""
    active = [m for m in report["mode_isolation"]
              if m["pass"] == "latent" and m["cell"] == "coding:2" and m["mode"] == "active"]
    assert active and "full_pipeline" in active[0]["route_path_per_step"]
    # observe / dry_run never move the path (weight never changes).
    for mode in ("observe", "dry_run"):
        m = [x for x in report["mode_isolation"]
             if x["pass"] == "latent" and x["cell"] == "coding:2" and x["mode"] == mode][0]
        assert m["distinct_paths"] == ["standard"]


def test_latent_decay_releases_low_diff_floor(report) -> None:
    """A promoted low-difficulty cell's floor is released one band by decay
    (demote restored), while a high-difficulty cell stays protected at its
    B12-native baseline (full_pipeline)."""
    rd = {r["cell"]: r for r in report["ratchet_decay"] if r["pass"] == "latent"}
    low = rd["coding:2"]
    assert low["floor_after_rise"] == "full_pipeline"   # promoted + ratchet-locked
    assert low["released_floor"] == "standard"          # decay released one band
    high = rd["coding:5"]
    assert high["baseline_band"] == "full_pipeline"
    assert high["released_floor"] == "full_pipeline"    # baseline-exempt (protected)


# --- schema for C consumption ----------------------------------------------


def test_schema_top_level_fields(report) -> None:
    for key in ("generated_at", "harness", "grid", "passes", "cells",
                "mode_isolation", "ratchet_decay", "neutrality_checks",
                "bg_observations", "notes"):
        assert key in report
    assert set(report["grid"]) == {"categories", "difficulties", "archetype_by_category", "params"}
    for c in report["cells"]:
        for field in ("pass", "session", "category", "difficulty", "archetype",
                      "baseline_band", "seed_weight", "trajectory"):
            assert field in c
        for p in c["trajectory"]:
            assert set(p) == {"step", "trace_id", "outcome", "prediction_error", "weight"}
