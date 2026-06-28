"""
CORTEX 3.0 — Phase 1 Migration Test
Confirms:
1. All existing SQLite tables remain accessible
2. ChromaDB VectorMemory store/search/delete round-trip works
3. No cross-contamination between sessions
4. SQLite and ChromaDB can be imported together without conflict
Run from project root: python test_phase1_migration.py
"""

import sys
import time
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
        print(f"  [FAIL] {label}  ← {detail}")

print("\n=== CORTEX 3.0 - Phase 1 Migration Test ===\n")

# ─── 1. SQLite tables still work ──────────────────────────────
print("--- 1. SQLite backward-compat ---")
try:
    from cortex.db import (
        init_db, create_session, get_turn_count, increment_turn,
        save_message, get_history, cache_get, cache_set,
        log_token_usage, get_analytics,
    )
    init_db()
    check("init_db() (SQLite)", True)
except Exception as e:
    check("init_db() (SQLite)", False, str(e))
    sys.exit(1)

try:
    create_session("migration_test_session")
    check("create_session()", True)
except Exception as e:
    check("create_session()", False, str(e))

try:
    save_message("migration_test_session", 0, "user", "hello migration")
    hist = get_history("migration_test_session")
    check("save_message + get_history", len(hist) > 0)
except Exception as e:
    check("save_message + get_history", False, str(e))

try:
    cache_set("test_input", "test_response", "CAT1", "1")
    cached = cache_get("test_input")
    check("cache_set + cache_get", cached is not None)
except Exception as e:
    check("cache_set + cache_get", False, str(e))

try:
    log_token_usage("migration_test_session", 0, "test_agent", "1", 100, 50)
    analytics = get_analytics()
    check("log_token_usage + get_analytics", "total_input_tokens" in analytics)
except Exception as e:
    check("log_token_usage + get_analytics", False, str(e))

# ─── 2. ChromaDB VectorMemory import ─────────────────────────
print("\n--- 2. ChromaDB VectorMemory import ---")
try:
    from cortex.db.vector import VectorMemory, COLLECTION_SHORT_TERM, get_collection_stats
    check("VectorMemory import", True)
except Exception as e:
    check("VectorMemory import", False, str(e))
    sys.exit(1)

# ─── 3. VectorMemory store + search ──────────────────────────
print("\n--- 3. VectorMemory store + search ---")
vm = None
stored_id = None
try:
    vm = VectorMemory(session_id="test_session_A", collection_name=COLLECTION_SHORT_TERM)
    check("VectorMemory instantiation", True)
except Exception as e:
    check("VectorMemory instantiation", False, str(e))
    sys.exit(1)

try:
    t0 = time.perf_counter()
    stored_id = vm.store(
        "The main character is a warrior named Kael living in a summer post-war world.",
        metadata={
            "turn": 1,
            "category": "CAT6",
            "confidence": "HIGH",
            "weight": 0.85,
            "source": "user",
        }
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    check(f"store() returns id ({elapsed_ms:.1f}ms)", isinstance(stored_id, str) and len(stored_id) > 0)
except Exception as e:
    check("store()", False, str(e))

try:
    results = vm.search("warrior character in a post-war setting", top_k=3)
    check("search() returns results", len(results) > 0)
    check("search result has 'document' key", "document" in results[0])
    check("search result has 'distance' key", "distance" in results[0])
    check("search result has 'metadata' key", "metadata" in results[0])
    if results:
        meta = results[0]["metadata"]
        check("metadata has session_id", meta.get("session_id") == "test_session_A")
        check("metadata has confidence", "confidence" in meta)
except Exception as e:
    check("search()", False, str(e))

# ─── 4. Session isolation ─────────────────────────────────────
print("\n--- 4. Session isolation ---")
try:
    vm_b = VectorMemory(session_id="test_session_B", collection_name=COLLECTION_SHORT_TERM)
    vm_b.store("Session B unrelated content about tax forms.", metadata={"turn": 1, "category": "CAT1"})
    # Session A search should NOT return Session B docs
    results_a = vm.search("tax forms", top_k=3)
    # All results from session A search must have session_id == test_session_A
    contaminated = any(r["metadata"].get("session_id") != "test_session_A" for r in results_a)
    check("Session A search excludes Session B docs", not contaminated,
          f"got {[r['metadata'].get('session_id') for r in results_a]}")
except Exception as e:
    check("Session isolation", False, str(e))

# ─── 5. Delete ────────────────────────────────────────────────
print("\n--- 5. VectorMemory delete ---")
if stored_id:
    try:
        ok = vm.delete(stored_id)
        check("delete() returns True", ok)
        results_after = vm.search("warrior character", top_k=3)
        still_there = any(r["id"] == stored_id for r in results_after)
        check("deleted doc no longer retrieved", not still_there)
    except Exception as e:
        check("delete()", False, str(e))

# ─── 6. get_stale ─────────────────────────────────────────────
print("\n--- 6. get_stale ---")
try:
    vm_stale = VectorMemory(session_id="stale_test", collection_name=COLLECTION_SHORT_TERM)
    vm_stale.store("old content to be cleaned", metadata={"turn": 1, "last_accessed": 1000.0})
    stale = vm_stale.get_stale(last_accessed_before=time.time() - 10)
    check("get_stale returns old documents", len(stale) > 0)
    check("stale doc has 'document' key", all("document" in d for d in stale))
except Exception as e:
    check("get_stale()", False, str(e))

# ─── 7. Collection stats ──────────────────────────────────────
print("\n--- 7. Collection stats ---")
try:
    stats = get_collection_stats()
    check("get_collection_stats() returns dict", isinstance(stats, dict))
    check("stats has cortex_short_term key", "cortex_short_term" in stats)
    check("stats short_term count >= 0", stats.get("cortex_short_term", -1) >= 0)
except Exception as e:
    check("get_collection_stats()", False, str(e))

# ─── 8. SQLite tables STILL accessible after ChromaDB ops ─────
print("\n--- 8. SQLite post-ChromaDB compat check ---")
try:
    hist2 = get_history("migration_test_session")
    analytics2 = get_analytics()
    check("SQLite history still readable after ChromaDB ops", len(hist2) > 0)
    check("SQLite analytics still readable after ChromaDB ops", "total_input_tokens" in analytics2)
except Exception as e:
    check("SQLite post-ChromaDB compat", False, str(e))

# ─── Summary ──────────────────────────────────────────────────
print(f"\n{'='*48}")
print(f"RESULT: {PASS_COUNT} passed, {FAIL_COUNT} failed")
print(f"{'='*48}\n")
sys.exit(0 if FAIL_COUNT == 0 else 1)
