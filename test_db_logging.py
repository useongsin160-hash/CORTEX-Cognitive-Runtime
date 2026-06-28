import os
import time
import sqlite3
import json
from cortex.config import DB_PATH, CHROMA_DB_PATH
from cortex.db.schema import init_db
import traceback

if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
init_db()

import cortex.orchestrator as orch
orch.VECTOR_MEMORY_ENABLED = True
from cortex.db.vector import VectorMemory
orch.VectorMemory = VectorMemory

# Set up mock
def mock_call_gemini(prompt, json_schema=None, system=None, model=None, _log_ctx=None):
    from cortex.db.analytics import log_token_usage
    if _log_ctx:
        log_token_usage(_log_ctx.get("session_id", "test"), _log_ctx.get("turn", 0), _log_ctx.get("agent_name", "mock"), _log_ctx.get("level", "1"), 100, 50, False, False)
    if json_schema:
        if "agent" in str(json_schema):
            return '[{"agent": "knowledge", "confidence": 0.9}]'
        if "target" in str(json_schema):
            return '{"target": "mock_tag", "category": "CAT1", "level": "1.5", "weight": 0.5, "confidence": "HIGH"}'
        return '{"verdict": "PASS"}'
    return "Mock Response"

orch.call_gemini = mock_call_gemini

print("\n--- Test 1: EXACT CACHE HIT ---")
print("Response 1:", orch.cortex_process("CORTEX 3", "sess_opt"))
print("Response 2:", orch.cortex_process("CORTEX 3", "sess_opt"))

print("\n--- Test 2: THALAMUS HIT ---")
print("Response 3:", orch.cortex_process("안녕", "sess_opt"))

print("\n--- DB Inspection ---")
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute("SELECT agent_name, input_tokens, output_tokens, was_cached, thalamus_hit FROM token_log")
rows = c.fetchall()
for r in rows:
    print(r)
c.execute("SELECT SUM(was_cached), SUM(thalamus_hit) FROM token_log")
print("Sums:", c.fetchone())
conn.close()
