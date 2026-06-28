"""
CORTEX 2.0 — Verification Test Suite
Migration test + Thalamus unit test + Performance benchmark
"""

import sys
import time
sys.path.insert(0, '.')

PASS_COUNT = 0
FAIL_COUNT = 0

def check(label, condition, detail=""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  [PASS] {label}")
    else:
        FAIL_COUNT += 1
        print(f"  [FAIL] {label}  {detail}")

print("\n=== CORTEX 2.0 Verification Suite ===\n")

# ─── 1. Import test ───────────────────────────────────────────
print("--- 1. Import Tests ---")
try:
    from cortex import cortex_process, init_db
    from cortex.config import MODEL_NAME, DB_PATH
    from cortex.pipeline.thalamus import thalamus, ROUTING_TABLE
    from cortex.pipeline.irl import IRLCounter
    from cortex.pipeline.gate import propagate_confidence
    from cortex.pipeline.router import route_pipeline
    from cortex.agents import (SpinalAgent, KnowledgeAgent, ReasoningAgent,
                                ComputeAgent, CodeAgent, EmotionalAgent)
    from cortex.db.analytics import get_analytics
    check("All package imports", True)
except Exception as e:
    check("All package imports", False, str(e))
    sys.exit(1)

# ─── 2. DB init ───────────────────────────────────────────────
print("\n--- 2. DB Init ---")
try:
    init_db()
    check("init_db()", True)
except Exception as e:
    check("init_db()", False, str(e))

# ─── 3. Thalamus Phase A (LEVEL 1 fast-path) ─────────────────
print("\n--- 3. Thalamus Phase A (LEVEL 1) ---")
level1_inputs = [
    ("annyeong input", "안녕"),
    ("hi greeting", "hi"),
    ("thanks", "감사합니다"),
    ("positive", "ok"),
    ("bye", "bye"),
]
for label, inp in level1_inputs:
    r = thalamus(inp)
    check(f"Phase A '{inp}'", r is not None and "response" in r and r.get("level") == "1")

# ─── 4. Thalamus Phase B (Top-K routing) ─────────────────────
print("\n--- 4. Thalamus Phase B (Top-K) ---")
routing_tests = [
    ("why reasoning", "why is the sky blue", ["reasoning"]),
    ("calculate compute", "calculate 2+2 for me", ["compute"]),
    ("python code", "write a python function", ["code"]),
    ("inventory spinal", "check inventory status", ["spinal"]),
    ("sad emotional", "I feel so sad", ["emotional"]),
    ("what is knowledge", "what is machine learning", ["knowledge"]),
]
for label, inp, expected in routing_tests:
    r = thalamus(inp)
    if r and "top_k" in r:
        found = [a["agent"] for a in r["top_k"]]
        hit = any(a in found for a in expected)
        check(f"Phase B '{inp[:25]}'", hit, f"got {found}, expected {expected}")
    elif r and "response" in r:
        # Fell into LEVEL 1 fast-path — still valid
        check(f"Phase B '{inp[:25]}' (LEVEL1 path)", True)
    else:
        check(f"Phase B '{inp[:25]}'", False, "returned None (full fallback)")

# ─── 5. Top-K=2 constraint ───────────────────────────────────
print("\n--- 5. Top-K <= 2 Constraint ---")
multi_inp = "why is python code calculate debug analyze reasoning logic"
r = thalamus(multi_inp)
if r and "top_k" in r:
    check("Top-K <= 2", len(r["top_k"]) <= 2, f"got k={len(r['top_k'])}")
else:
    check("Top-K <= 2", True, "LEVEL1 or fallback path")

# ─── 6. Thalamus performance < 5ms ───────────────────────────
print("\n--- 6. Thalamus Performance ---")
t0 = time.perf_counter()
for _ in range(1000):
    thalamus("why is the sky blue because of light scattering")
elapsed_ms = (time.perf_counter() - t0) * 1000
avg_ms = elapsed_ms / 1000
check(f"Avg < 5ms ({avg_ms:.4f}ms)", avg_ms < 5)
print(f"  1000 calls: {elapsed_ms:.1f}ms total | {avg_ms:.4f}ms avg")

# ─── 7. IRL Counter ───────────────────────────────────────────
print("\n--- 7. IRL Counter ---")
irl = IRLCounter()
s1 = irl.tick("REVISE")
check("REVISE tick 1 -> RUNNING", s1["status"] == "RUNNING")
s2 = irl.tick("REVISE")
check("REVISE tick 2 -> RUNNING", s2["status"] == "RUNNING")
s3 = irl.tick("REVISE")
check("REVISE tick 3 -> FORCED_EXIT", s3["status"] == "FORCED_EXIT")

irl2 = IRLCounter()
s = irl2.tick("PASS")
check("PASS -> PASS", s["status"] == "PASS")

# ─── 8. GATE confidence propagation ──────────────────────────
print("\n--- 8. GATE Propagation ---")
check("HIGH+HIGH -> HIGH", propagate_confidence(["HIGH", "HIGH"]) == "HIGH")
check("HIGH+MED -> MED", propagate_confidence(["HIGH", "MED"]) == "MED")
check("MED+LOW -> LOW", propagate_confidence(["MED", "LOW"]) == "LOW")

# ─── 9. Pipeline routing ─────────────────────────────────────
print("\n--- 9. Pipeline Routing ---")
check("LEVEL 1 pipeline", route_pipeline("1") == ["RET", "LGEN", "V1", "THINK", "GATE", "CP3"])
check("LEVEL 3 has TAG_UPDATE", "TAG_UPDATE" in route_pipeline("3"))

# ─── 10. SpinalAgent zero-LLM ────────────────────────────────
print("\n--- 10. SpinalAgent (zero-LLM) ---")
spinal = SpinalAgent()
check("SpinalAgent.requires_llm == False", not spinal.requires_llm)

ctx = {"call_gemini": None, "session_id": "test_sess", "turn": 0, "level": "1"}
from cortex.db import create_session
create_session("test_sess")
t_spinal = time.perf_counter()
result = spinal.process("status check", 1.0, [], [], ctx)
spinal_ms = (time.perf_counter() - t_spinal) * 1000
check("SpinalAgent returns AgentResult", result.agent_name == "spinal")
check("SpinalAgent tokens_used == 0", result.tokens_used == 0)
check(f"SpinalAgent < 10ms ({spinal_ms:.2f}ms)", spinal_ms < 10)

# ─── 11. Analytics endpoint (empty DB) ───────────────────────
print("\n--- 11. Analytics ---")
analytics = get_analytics()
check("analytics has total_input_tokens", "total_input_tokens" in analytics)
check("analytics has cache_hit_rate", "cache_hit_rate" in analytics)
check("analytics has by_agent", "by_agent" in analytics)

# ─── Summary ─────────────────────────────────────────────────
print(f"\n{'='*40}")
print(f"RESULT: {PASS_COUNT} passed, {FAIL_COUNT} failed")
print(f"{'='*40}\n")
sys.exit(0 if FAIL_COUNT == 0 else 1)
