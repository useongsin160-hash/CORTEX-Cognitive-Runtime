"""
CORTEX 3.0 - Phase 2 Synapse Unit Test Suite
50 inputs, hit/miss rate report.
Run: python test_phase2_synapse.py
"""

import sys, random, time
sys.path.insert(0, ".")

PASS_COUNT = 0
FAIL_COUNT = 0

def check(label, condition, detail=""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  [PASS] {label}")
    else:
        FAIL_COUNT += 1
        print(f"  [FAIL] {label}  <- {detail}")

print("\n=== CORTEX 3.0 - Phase 2 Synapse Unit Tests ===\n")

from cortex.pipeline.synapse import SynapseState, get_synapse, release_synapse, get_all_synapse_stats, AGENT_CATEGORY_MAP

# ─── 1. Basic instantiation ───────────────────────────────────
print("--- 1. Instantiation ---")
s = SynapseState("sess_unit")
check("Initial weights empty", s.weights == {})
check("Initial turn_count == 0", s.turn_count == 0)
check("session_id preserved", s.session_id == "sess_unit")

# ─── 2. Boost on category match ───────────────────────────────
print("\n--- 2. Boost ---")
s.update(["CAT5"])
check("CAT5 boosted after update", s.weights.get("CAT5", 0) > 0)
check("Boost value <= MAX_WEIGHT", s.weights.get("CAT5", 0) <= 1.0)
check("Single boost ~= BOOST_RATE (minus decay on nothing)", abs(s.weights.get("CAT5", 0) - 0.25) < 0.05)

# ─── 3. Decay per turn ────────────────────────────────────────
print("\n--- 3. Decay ---")
w_before = s.weights.get("CAT5", 0)
s.update(["CAT1"])   # different category -- CAT5 should decay
w_after  = s.weights.get("CAT5", 0)
check("CAT5 decays when not matched", w_after < w_before, f"{w_after} !< {w_before}")
check("CAT1 boosted", s.weights.get("CAT1", 0) > 0)

# ─── 4. Decay below noise threshold → pruned ─────────────────
print("\n--- 4. Noise pruning ---")
s2 = SynapseState("sess_prune")
s2.update(["CAT3"])
# Force weight very low
s2.weights["CAT3"] = 0.04  # below NOISE_THRESHOLD=0.05
s2.update([])              # trigger decay + prune, no boost
check("Weight below NOISE_THRESHOLD pruned", "CAT3" not in s2.weights)

# ─── 5. Category conflict reset ───────────────────────────────
print("\n--- 5. Category conflict reset ---")
s3 = SynapseState("sess_conflict")
s3.update(["CAT5"])  # code
s3.update(["CAT5"])  # code again
w_code = s3.weights.get("CAT5", 0)
s3.update(["CAT9"])  # emotional - full switch
check("Conflict detected, switch counter incremented", s3.stat_category_switches == 1)
# After reset, CAT5 should be gone (reset clears), CAT9 should be the only weight
check("CAT5 cleared after conflict reset", "CAT5" not in s3.weights, f"weights={s3.weights}")
check("CAT9 boosted after conflict reset", s3.weights.get("CAT9", 0) > 0)

# ─── 6. get_bias() ────────────────────────────────────────────
print("\n--- 6. get_bias() ---")
s4 = SynapseState("sess_bias")
s4.update(["CAT5"])  # code category
bias_code = s4.get_bias("code")
bias_emo  = s4.get_bias("emotional")
check("code agent gets bias from CAT5", bias_code > 0, f"bias={bias_code}")
check("emotional agent gets 0 bias (no CAT9)", bias_emo == 0.0, f"bias={bias_emo}")

s4.update(["CAT9"])  # emotional
bias_emo2 = s4.get_bias("emotional")
check("emotional agent gets bias after CAT9", bias_emo2 > 0)

# ─── 7. Max weight / dominant category ───────────────────────
print("\n--- 7. Dominant category ---")
s5 = SynapseState("sess_dom")
s5.update(["CAT2"])
s5.update(["CAT2"])
s5.update(["CAT2"])
dom = s5.get_dominant_category()
check("Dominant category is CAT2 after 3x boost", dom is not None and dom[0] == "CAT2", str(dom))
check("Max weight > 0", s5.get_max_weight() > 0)

# ─── 8. Full reset ────────────────────────────────────────────
print("\n--- 8. reset() ---")
s5.reset()
check("Weights cleared after reset()", s5.weights == {})
check("Dominant category None after reset", s5.get_dominant_category() is None)

# ─── 9. to_dict() serialization ───────────────────────────────
print("\n--- 9. to_dict() ---")
s6 = SynapseState("sess_serial")
s6.update(["CAT1", "CAT4"])
d = s6.to_dict()
check("to_dict has session_id", d.get("session_id") == "sess_serial")
check("to_dict has weights", isinstance(d.get("weights"), dict))
check("to_dict has dominant_cat", "dominant_cat" in d)
check("to_dict has stats", "stats" in d)

# ─── 10. Session registry ─────────────────────────────────────
print("\n--- 10. Session registry ---")
sa = get_synapse("reg_a")
sb = get_synapse("reg_b")
sa.update(["CAT5"])
check("Session reg_a state independent from reg_b", sb.weights == {}, str(sb.weights))
check("get_synapse returns same object", get_synapse("reg_a") is sa)
release_synapse("reg_a")
check("After release, gets new instance", get_synapse("reg_a") is not sa)

# ─── 11. Probabilistic boost flag ─────────────────────────────
print("\n--- 11. Probabilistic activation ---")
s7 = SynapseState("sess_prob")
# Low confidence + high weight -> should boost
s7.weights["CAT2"] = 0.7
check("should_probabilistic_boost: low conf + high weight", s7.should_probabilistic_boost(0.65))
check("should_probabilistic_boost: high conf -> False", not s7.should_probabilistic_boost(0.85))
s7.weights["CAT2"] = 0.5
check("should_probabilistic_boost: low conf + low weight -> False", not s7.should_probabilistic_boost(0.65))

# ─── 12. 50-input simulation (hit/miss rate) ──────────────────
print("\n--- 12. 50-input Hit/Miss Simulation ---")

CATEGORIES = ["CAT1","CAT2","CAT3","CAT4","CAT5","CAT6","CAT7","CAT8","CAT9"]
random.seed(42)

sim = SynapseState("sess_sim")
hits = 0
misses = 0
bias_sum = 0.0

turns = []
for i in range(50):
    # Simulate 30 code-focused turns, 20 random
    if i < 30:
        cat = "CAT5"
    else:
        cat = random.choice(CATEGORIES)
    turns.append(cat)
    sim.update([cat])

    # Check if "code" agent would get non-zero bias
    bias = sim.get_bias("code")
    if bias > 0:
        hits += 1
    else:
        misses += 1
    bias_sum += bias

hit_rate = hits / 50
avg_bias  = round(bias_sum / 50, 4)

print(f"  Turns:    50")
print(f"  Hits:     {hits}  (code agent got bias > 0)")
print(f"  Misses:   {misses} (code agent got bias == 0)")
print(f"  Hit Rate: {hit_rate:.2%}")
print(f"  Avg Bias: {avg_bias}")

check("Hit rate > 50% (code-heavy simulation)", hit_rate > 0.50, f"{hit_rate:.2%}")
check("Avg bias > 0.05", avg_bias > 0.05, str(avg_bias))

# Final dominant after 50 turns
dom_final = sim.get_dominant_category()
check("Final dominant category exists", dom_final is not None)
# After 30 code turns + 20 random, dominant should still be code-related
print(f"  Final dominant: {dom_final}")

# ─── 13. Performance < 1ms per call ──────────────────────────
print("\n--- 13. Performance ---")
perf = SynapseState("sess_perf")
t0 = time.perf_counter()
for _ in range(1000):
    perf.update(["CAT5", "CAT2"])
elapsed_ms = (time.perf_counter() - t0) * 1000
avg_ms = elapsed_ms / 1000
print(f"  1000 update() calls: {elapsed_ms:.2f}ms total | {avg_ms:.4f}ms avg")
check(f"Avg update() < 1ms ({avg_ms:.4f}ms)", avg_ms < 1.0)

t1 = time.perf_counter()
for _ in range(1000):
    perf.get_bias("code")
elapsed_bias = (time.perf_counter() - t1) * 1000
avg_bias_ms = elapsed_bias / 1000
print(f"  1000 get_bias() calls: {elapsed_bias:.2f}ms total | {avg_bias_ms:.4f}ms avg")
check(f"Avg get_bias() < 1ms ({avg_bias_ms:.4f}ms)", avg_bias_ms < 1.0)

# ─── 14. get_all_synapse_stats ────────────────────────────────
print("\n--- 14. Global stats ---")
# Register the sim session so it shows up in global stats
get_synapse("sess_sim_global").update(["CAT5"])
get_synapse("sess_sim_global").update(["CAT5"])
stats = get_all_synapse_stats()
check("stats has avg_dominant_weight", "avg_dominant_weight" in stats)
check("stats has category_switches", "category_switches" in stats)
check("stats has decay_cycles", stats.get("decay_cycles", 0) > 0,
      f"got decay_cycles={stats.get('decay_cycles')}")

# ─── Summary ──────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"RESULT: {PASS_COUNT} passed, {FAIL_COUNT} failed")
print(f"{'='*50}\n")
sys.exit(0 if FAIL_COUNT == 0 else 1)
