# BasalGanglia full-input measurement (B10)

- generated_at: 2026-06-26T14:00:23Z
- comparisons: 945
- changed (synapse-only != full): 378 (40.0%)
- bg_applied: False

## selected candidate_type distribution

| input | distribution |
|-------|--------------|
| synapse-only | {'swarm_full': 945} |
| full-input | {'swarm_full': 567, 'swarm_minimal': 378} |

## notes

- Full-input values are real-shaped, not invented: pfc_confidence sweeps PFC's own per-step confidences, ne_level is the faithful {0,1} bool surface (NE has no continuous value), rpe counts are plausible recent-window tallies. A change is the real pfc/lc/rpe terms acting through the score weights (0.3/0.1/0.05).
- BG.applied stays False (type hard-lock). B10 fills inputs only; the recommendation is still never consumed. C2 decides apply.
