import os
import sys

# Silence ChromaDB overly verbose logs
os.environ["CHROMA_TELEMETRY_OPTOUT"] = "1"

from cortex.orchestrator import cortex_process
from cortex.db import init_db

print("Starting RET Cross-Session Verification...")
init_db()

print("\n==================================")
print("  [SESSION 1] LTM Sowing Phase\n  Generating a complex response...")
print("==================================\n")

# Triggering LEVEL 2 or 3 to ensure it persists to LTM (cortex_long_term)
res1 = cortex_process("session_LTM_1", "대한민국의 헌법 제1조 1항과 2항에 대해 각각 1문장씩 설명해줘.")
print(f"\n[Session 1 Output]\n{res1}\n")

print("\n==================================")
print("  [SESSION 2] LTM Harvesting Phase\n  Retrieving context without history...")
print("==================================\n")

# In a completely different session, asking a dependent question
res2 = cortex_process("session_LTM_2", "방금 네가 설명한 헌법 조항 중 2항이 정확히 뭐야?")
print(f"\n[Session 2 Output]\n{res2}\n")

print("Test complete. Check the logs above for [RET] module activation and token spikes in Session 2.")
