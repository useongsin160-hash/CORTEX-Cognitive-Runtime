"""
CORTEX 3.0 - Phase 3 + Phase 4 Test Suite
Tests: SpinalAgent semantic injection + Glycine/Epinephrine arbitration.
Run: python test_phase3_phase4.py
"""
import sys, time
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

print("\n=== CORTEX 3.0 - Phase 3 + 4 Tests ===\n")

# ─── PHASE 3: SpinalAgent ─────────────────────────────────────
print("--- Phase 3: SpinalAgent imports ---")
try:
    from cortex.agents.spinal import SpinalAgent, get_semantic_context, _build_meta_injection
    check("SpinalAgent import", True)
except Exception as e:
    check("SpinalAgent import", False, str(e)); sys.exit(1)

spinal = SpinalAgent()
check("SpinalAgent.requires_llm == False", not spinal.requires_llm)
check("SpinalAgent.name == 'spinal'", spinal.name == "spinal")

print("\n--- Phase 3: _build_meta_injection ---")
neighbors = [
    {"document": "def calculate_physics(mass, velocity):", "metadata": {"category": "CAT5", "confidence": "HIGH", "source": "user"}},
    {"document": "Kael is a warrior in a summer post-war world.", "metadata": {"category": "CAT6", "confidence": "MED", "source": "user"}},
    {"document": "What is the speed of light?", "metadata": {"category": "CAT1", "confidence": "HIGH", "source": "user"}},
]
meta = _build_meta_injection(neighbors)
check("meta has 'current_tone'", "current_tone" in meta)
check("tone is technical from CAT5", meta["current_tone"] == "technical/code")
check("active_project populated from CAT5 HIGH", len(meta["active_project"]) > 0)
check("world_context populated from CAT6", "Kael" in meta["world_context"])
check("user_preference populated from HIGH source", len(meta["user_preference"]) > 0)

print("\n--- Phase 3: get_semantic_context (no VectorMemory) ---")
result = get_semantic_context("test query", "sess_test", vector_memory=None)
check("get_semantic_context returns {} when no VM", result == {})

print("\n--- Phase 3: get_semantic_context with ChromaDB ---")
try:
    from cortex.db.vector import VectorMemory, COLLECTION_SHORT_TERM
    from cortex.db import init_db
    init_db()

    vm = VectorMemory(session_id="spinal_test_sess", collection_name=COLLECTION_SHORT_TERM)
    # Store some docs
    vm.store(
        "The main character is coding a Unity C# physics engine.",
        metadata={"turn": 1, "category": "CAT5", "confidence": "HIGH",
                  "weight": 0.9, "source": "user"}
    )
    vm.store(
        "The world is set in summer, post-war era.",
        metadata={"turn": 2, "category": "CAT6", "confidence": "HIGH",
                  "weight": 0.7, "source": "user"}
    )

    ctx = get_semantic_context(
        "help me with the C# physics code",
        "spinal_test_sess",
        vector_memory=vm,
    )
    check("get_semantic_context returns dict", isinstance(ctx, dict))
    check("meta_injection has 4 fields", len(ctx) == 4)
    # May or may not find neighbors depending on distance threshold
    print(f"  meta_injection result: {ctx}")
    check("get_semantic_context didn't raise", True)
except Exception as e:
    check("get_semantic_context with ChromaDB", False, str(e))

print("\n--- Phase 3: SpinalAgent.process() zero-LLM ---")
try:
    from cortex.db import create_session
    create_session("spinal_proc_sess")
    ctx_dict = {
        "call_gemini": None,
        "session_id": "spinal_proc_sess",
        "turn": 0,
        "level": "2",
        "meta_injection": {"current_tone": "technical/code", "active_project": "Unity physics"},
    }
    t0 = time.perf_counter()
    result = spinal.process("status check", 1.0, [], [], ctx_dict)
    elapsed = (time.perf_counter() - t0) * 1000
    check("SpinalAgent.process() returns AgentResult", result.agent_name == "spinal")
    check("SpinalAgent tokens_used == 0", result.tokens_used == 0)
    check(f"SpinalAgent < 10ms ({elapsed:.2f}ms)", elapsed < 10)
    check("meta_injection reflected in content", "semantic context" in result.content or "spinal" in result.content)
except Exception as e:
    check("SpinalAgent.process()", False, str(e))


# ─── PHASE 4: Neuromodulators ─────────────────────────────────
print("\n\n--- Phase 4: Neuromodulator imports ---")
try:
    from cortex.pipeline.neuromodulators import (
        Glycine, Epinephrine, arbitrate,
        get_glycine, get_epinephrine, record_api_call, _count_tokens,
    )
    check("neuromodulators import", True)
except Exception as e:
    check("neuromodulators import", False, str(e)); sys.exit(1)

g = Glycine()
epi = Epinephrine()

print("\n--- Phase 4: _count_tokens ---")
token_count = _count_tokens("Hello, this is a test sentence.")
check("_count_tokens returns int > 0", isinstance(token_count, int) and token_count > 0)

print("\n--- Phase 4: Glycine.check_token_limit ---")
short_text = "Hello world"
was_trimmed, result_text = g.check_token_limit(short_text)
check("Short text not trimmed", not was_trimmed)
check("Short text returned unchanged", result_text == short_text)

long_text = "This is a test sentence. " * 700  # ~175 tokens * 700 = ~5000 words
was_trimmed2, result_text2 = g.check_token_limit(long_text)
check("Long text (5000+ tokens) is trimmed", was_trimmed2, f"token_count={_count_tokens(long_text)}")
check("Trimmed result shorter than original", len(result_text2) < len(long_text))
check("stat_token_trims incremented", g.stat_token_trims == 1)

print("\n--- Phase 4: Glycine.check_rate_limit ---")
g2 = Glycine()
# Simulate 0 calls -> normal
r0 = g2.check_rate_limit("rl_sess_0")
check("0 calls -> normal", r0 == "normal", r0)

# Simulate 9 calls -> throttle
for _ in range(9):
    record_api_call("rl_sess_throttle")
r_throttle = g2.check_rate_limit("rl_sess_throttle")
check("9 calls -> throttle", r_throttle == "throttle", r_throttle)

# Simulate 11 calls -> downgrade
for _ in range(11):
    record_api_call("rl_sess_dg")
r_dg = g2.check_rate_limit("rl_sess_dg")
check("11 calls -> downgrade", r_dg == "downgrade", r_dg)
check("stat_rate_downgrades incremented", g2.stat_rate_downgrades >= 1)

print("\n--- Phase 4: Glycine.check_irl_loop ---")
g3 = Glycine()
check("IRL count 0 -> continue", g3.check_irl_loop(0) == "continue")
check("IRL count 1 -> continue", g3.check_irl_loop(1) == "continue")
check("IRL count 2 -> force_hold (>= IRL_MAX_BEFORE_FALLBACK)", g3.check_irl_loop(2) == "force_hold")
check("IRL count 5 -> force_hold", g3.check_irl_loop(5) == "force_hold")
check("stat_irl_force_holds incremented", g3.stat_irl_force_holds >= 1)

print("\n--- Phase 4: Epinephrine.should_boost ---")
epi2 = Epinephrine()
check("keyword 'core design' triggers boost", epi2.should_boost("Tell me the core design", 0.0))
check("keyword '처음부터' triggers boost", epi2.should_boost("처음부터 다시 설명해줘", 0.0))
check("keyword '아키텍처' triggers boost", epi2.should_boost("전체 아키텍처를 보여줘", 0.0))
check("No keyword, low weight -> no boost", not epi2.should_boost("What is Python?", 0.3))
check("No keyword, high weight (0.8 > 0.7) -> boost", epi2.should_boost("What is Python?", 0.8))
check("keyword_triggers counted", epi2.stat_keyword_triggers >= 3)
check("synapse_triggers counted", epi2.stat_synapse_triggers >= 1)

print("\n--- Phase 4: Epinephrine.apply_boost ---")
epi3 = Epinephrine()
config_before = {"model": "gemini-2.5-flash", "max_output_tokens": 2048}
boosted_config, boosted_prompt = epi3.apply_boost(config_before, "You are a helpful assistant.")
check("apply_boost sets MODEL_PRO", "pro" in boosted_config["model"].lower() or "1.5" in boosted_config["model"])
check("apply_boost sets max_output_tokens=8192", boosted_config["max_output_tokens"] == 8192)
check("apply_boost does NOT mutate original config", config_before["model"] == "gemini-2.5-flash")
check("apply_boost injects boost header into prompt", "EPINEPHRINE BOOST" in boosted_prompt)
check("apply_boost sets _epinephrine_boost flag", boosted_config.get("_epinephrine_boost") is True)

print("\n--- Phase 4: Arbitration (Glycine WINS) ---")
g_arb = Glycine()
# Both hard limit AND boost triggered
result = arbitrate(glycine_hard_limit=True, epinephrine_boost=True, glycine=g_arb)
check("Glycine wins when both fire", result == "glycine_wins", result)
check("stat_conflicts_won incremented", g_arb.stat_conflicts_won == 1)

result2 = arbitrate(glycine_hard_limit=False, epinephrine_boost=True, glycine=g_arb)
check("Epinephrine wins when no hard limit", result2 == "epinephrine_wins", result2)

result3 = arbitrate(glycine_hard_limit=False, epinephrine_boost=False, glycine=g_arb)
check("no_conflict when neither fires", result3 == "no_conflict", result3)

print("\n--- Phase 4: Performance < 1ms ---")
gt = Glycine()
sample_prompt = "This is a moderately long prompt. " * 50  # ~500 tokens

t0 = time.perf_counter()
for _ in range(1000):
    gt.check_token_limit(sample_prompt)
elapsed_ms = (time.perf_counter() - t0) * 1000
avg_ms = elapsed_ms / 1000
print(f"  1000x check_token_limit: {elapsed_ms:.1f}ms | {avg_ms:.4f}ms avg")
check(f"check_token_limit < 1ms ({avg_ms:.4f}ms)", avg_ms < 1.0)

et = Epinephrine()
t1 = time.perf_counter()
for _ in range(1000):
    et.should_boost("Tell me the core design of the full architecture", 0.5)
elapsed2 = (time.perf_counter() - t1) * 1000
avg2 = elapsed2 / 1000
print(f"  1000x should_boost: {elapsed2:.1f}ms | {avg2:.4f}ms avg")
check(f"should_boost < 1ms ({avg2:.4f}ms)", avg2 < 1.0)

print("\n--- Phase 4: Module singletons ---")
check("get_glycine() returns Glycine", isinstance(get_glycine(), Glycine))
check("get_epinephrine() returns Epinephrine", isinstance(get_epinephrine(), Epinephrine))
check("Singletons are same objects on repeated calls", get_glycine() is get_glycine())

# ─── Summary ──────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"RESULT: {PASS_COUNT} passed, {FAIL_COUNT} failed")
print(f"{'='*50}\n")
sys.exit(0 if FAIL_COUNT == 0 else 1)
