# BasalGanglia redesign measurement

- generated_at: 2026-06-27T05:07:00Z
- comparisons: 945
- bg_applied: False

## recommendation distribution

| axis | distribution |
|------|--------------|
| candidate_type | {'fallback': 28, 'swarm_full': 434, 'swarm_minimal': 294, 'tier_1_5_augment': 189} |
| route_path | {'full_pipeline': 434, 'lightweight': 217, 'standard': 294} |

## difficulty-appropriateness

| difficulty | baseline band | route_path distribution | distinct types |
|-----------|---------------|-------------------------|----------------|
| 1 | lightweight | {'lightweight': 161, 'standard': 28} | 3 |
| 2 | standard | {'full_pipeline': 28, 'lightweight': 28, 'standard': 133} | 3 |
| 3 | standard | {'full_pipeline': 28, 'lightweight': 28, 'standard': 133} | 3 |
| 4 | full_pipeline | {'full_pipeline': 189} | 1 |
| 5 | full_pipeline | {'full_pipeline': 189} | 1 |

- high-difficulty (4·5) raw demotions: 0 / 378 (blocked at apply by the ratchet floor)
- promotions above baseline: 84

## notes

- before: Pre-redesign (scripts/measure_bg_full_input.py): the LC-bool-only policy recommended swarm_minimal for 100% of difficulty 4·5 cells (378/378) — a band demotion driven solely by the LC caution bonus.
- after: The redesigned demand-match anchors each difficulty at its B12 routing band and modulates with the real NE/RPE/synapse/PFC signals. Difficulty 4·5 recommend full_pipeline across this grid (0 raw demotions): the difficulty anchor + production-shape NE hold them at the top. Only an extreme de-escalator combination (maximally familiar + confident + successful) beyond this grid could demote a high-difficulty cell, and the no-demote ratchet floor (B11 S4) blocks that at apply time. Within difficulties 1-3 the selection now varies with the signals (distinct types > 1) — the old LC-bool policy was constant within a difficulty.
- bg_applied: applied stays False — this measures the recommendation only. C2 decides the apply (BG-apply stage + bg_apply_enabled flag).
