import os
import sys
os.environ["CHROMA_TELEMETRY_OPTOUT"] = "1"

from cortex.orchestrator import cortex_process
from cortex.db import init_db

print("Starting RET RAG Verification (V2)...")
init_db()

import uuid
u_id = str(uuid.uuid4())[:8]

print("\n==================================")
print("  [SESSION 1] LTM Sowing Phase")
print("==================================\n")

q1 = f"가상 세계 {u_id} 의 핵심 원소를 이루는 것은 '스타라이트 광물'과 '딥 워터 수정'이다. 이 두 가지를 설명해줘."
res1 = cortex_process(f"sess_ltm_1_{u_id}", q1)
print(f"\n[Output 1]\n{res1}\n")

print("\n==================================")
print("  [SESSION 2] LTM Harvesting Phase")
print("==================================\n")

q2 = f"방금 말한 그 가상의 핵심 원소 두 가지 중에 첫 번째 원소 이름이 뭐였어?"
res2 = cortex_process(f"sess_ltm_2_{u_id}", q2)
print(f"\n[Output 2]\n{res2}\n")
