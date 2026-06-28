import os
import time
import json
import logging
import sqlite3
import shutil
from concurrent.futures import ThreadPoolExecutor
from cortex.orchestrator import cortex_process
import cortex.orchestrator as orch
from cortex.config import DB_PATH, CHROMA_DB_PATH
from cortex.db.schema import init_db

from cortex.pipeline.thalamus import _CORTEX_3_1_INTERCEPTS

# Suppress debug logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("chromadb").setLevel(logging.WARNING)

QUERIES = []
SIMPLE_PATTERNS = ["안녕", "안녕하세요", "지금 몇 시야", "오늘 날씨 어때", "이름이 뭐야", "도와줘", "고마워", "잘 자", "피곤해", "15 + 27", "100 / 4", "수고했어", "잘 가", "메뉴 추천해줘", "뭐해?"]
QUERIES.extend(SIMPLE_PATTERNS * 2) # 30

PARAPHRASED_BASE = ["CORTEX 프레임워크의 주요 설계 원칙 3가지를 설명해줘", "파이썬에서 리스트 생성을 최적화하는 코드를 작성해 줘", "이 함수에서 발생하는 메모리 누수 원인이 뭘까?", "블랙홀의 사건의 지평선에 대해 쉽게 설명해 줘"]
PARAPHRASED_VARS = ["CORTEX 프레임워크 설계 원칙 세 가지 알려줘", "리스트 생성 최적화 파이썬 코드로 어떻게 짜?", "해당 함수 메모리 누수 발생 원인 분석해봐", "사건의 지평선(블랙홀) 개념 쉽게 풀어 설명해"]
QUERIES.extend(PARAPHRASED_BASE * 5) # 20
QUERIES.extend(PARAPHRASED_VARS * 5) # 20

for i in range(30):
    QUERIES.append(f"이 테스트를 위해 무작위로 생성된 질문입니다 고유값: {i * 739}번. 상세한 구조적 답변 부탁합니다.")

def mock_call_gemini(prompt, json_schema=None, system=None, model=None, _log_ctx=None):
    time.sleep(0.01) # Simulate fast LLM
    from cortex.db.analytics import log_token_usage
    if _log_ctx:
        for _ in range(3):
            try:
                log_token_usage(_log_ctx.get("session_id", "test"), _log_ctx.get("turn", 0), _log_ctx.get("agent_name", "mock"), _log_ctx.get("level", "1"), 100, 50, False, False)
                break
            except BaseException:
                time.sleep(0.02)
    if json_schema:
        schema_str = str(json_schema)
        if "agent" in schema_str: return '[{"agent": "knowledge", "confidence": 0.9}]'
        if "target" in schema_str: return '{"target": "mocked_tag", "confidence": "HIGH", "category": "CAT1", "weight": 0.9}'
        return '{"verdict": "PASS"}'
    return "Mock LLM Response"

orch.call_gemini = mock_call_gemini

def reset_environment():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM token_log")
        c.execute("DELETE FROM cache")
        conn.commit()
        conn.close()
    except Exception as e:
        print("SQLite reset error:", e)

    import cortex.db.vector as vector
    client = vector._get_client()
    try:
        client.delete_collection("cortex_cache")
    except Exception: pass
    try:
        client.delete_collection(vector.COLLECTION_SHORT_TERM)
    except Exception: pass
    try:
        client.delete_collection(vector.COLLECTION_LONG_TERM)
    except Exception: pass

def fetch_metrics():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), COUNT(*), COALESCE(SUM(was_cached), 0), COALESCE(SUM(thalamus_hit), 0) FROM token_log")
        row = c.fetchone()
        conn.close()
        if not row: return 0, 0, 0
        return int(row[2]), int(row[3]), int(row[4])
    except Exception as e:
        print("fetch_metrics ERROR:", e)
        return 0, 0, 0

def run_pipeline(session_id: str, feature_flag_semantic: bool, feature_flag_thalamus: bool):
    orch.VECTOR_MEMORY_ENABLED = feature_flag_semantic
    os.environ["CORTEX_THALAMUS"] = "1" if feature_flag_thalamus else "0"

    start = time.time()
    try:
        for i, q in enumerate(QUERIES):
            if not feature_flag_semantic:
                orch.VectorMemory = None
            else:
                from cortex.db.vector import VectorMemory
                orch.VectorMemory = VectorMemory
            try:
                cortex_process(session_id, q)
            except Exception as e:
                print(f"Error on query {i}: {e}")
    finally:
        pass
        
    return time.time() - start

if __name__ == "__main__":
    print("====================================")
    print("CORTEX 3.1 BENCHMARK SCRIPT")
    print("====================================")
    
    print("\n[1/2] Running Baseline (CORTEX 3.0 - Optimizations Disabled)...")
    reset_environment()
    base_time = run_pipeline("bench_session", feature_flag_semantic=False, feature_flag_thalamus=False)
    base_calls, base_cache, base_thal = fetch_metrics()
    
    print("\n[2/2] Running Optimized (CORTEX 3.1 - 2-Tier Cache + Thalamus)...")
    reset_environment()
    opt_time = run_pipeline("bench_session", feature_flag_semantic=True, feature_flag_thalamus=True)
    opt_calls, opt_cache, opt_thal = fetch_metrics()
    
    base_llm_calls = base_calls - base_cache - base_thal
    opt_llm_calls = opt_calls - opt_cache - opt_thal
    
    cost_base = base_llm_calls * 0.03
    cost_opt  = opt_llm_calls * 0.03
    
    base_llm_calls_safe = max(1, base_llm_calls)
    reduction_pct = ((base_llm_calls - opt_llm_calls) / base_llm_calls_safe) * 100
    
    # Calculate simulated API time: true local execution time + 1.2s average API network delay per call
    base_time_sim = base_time + (base_llm_calls * 1.2)
    opt_time_sim = opt_time + (opt_llm_calls * 1.2)
    base_time_sim_safe = max(0.001, base_time_sim)
    latency_improvement = ((base_time_sim - opt_time_sim) / base_time_sim_safe) * 100
    latency_str = f"{latency_improvement:.1f}% Faster" if latency_improvement > 0 else f"{abs(latency_improvement):.1f}% Slower"
    
    report = f"""# CORTEX 3.1 BENCHMARK PERFORMANCE REPORT

## 1. API Call Volume (100 Queries)
| Metric | 3.0 Baseline | 3.1 Optimized | Reduction |
|--------|-------------|---------------|-----------|
| Total LLM Calls Triggered | {base_llm_calls} | {opt_llm_calls} | **-{reduction_pct:.1f}%** |
| Cache Hits (2-Tier combined) | {base_cache} | {opt_cache} | +{opt_cache - base_cache} |
| Thalamus Intercepts | {base_thal} | {opt_thal} | +{opt_thal - base_thal} |

*Note: In 3.1 Optimized, Phase 2 implements a 2-Tier Cache Rule (Tier 1: ChromaDB Semantic Cache -> Tier 2: SQLite Exact-Match Cache).*

## 2. API Cost Estimate 
*(Assuming $0.03 average per LLM reasoning chain)*
| Scenario | Estimated Cost | Savings |
|----------|----------------|---------|
| 3.0 Baseline | ${cost_base:.3f} | - |
| 3.1 Optimized | ${cost_opt:.3f} | **${cost_base - cost_opt:.3f}** |

## 3. Latency (Real-World Network Simulation)
*(Includes measured local routing overhead + 1.2s average API roundtrip per LLM call)*
| Scenario | Total Time (100 q) | Avg ms/req |
|----------|--------------------|------------|
| 3.0 Baseline | {base_time_sim:.2f}s | {(base_time_sim/100)*1000:.1f} ms |
| 3.1 Optimized | {opt_time_sim:.2f}s | {(opt_time_sim/100)*1000:.1f} ms |
| **Improvement** | - | **{latency_str}** |
"""
    with open("CORTEX_3_1_PERFORMANCE_REPORT.md", "w", encoding="utf-8") as f:
        f.write(report)
    print("Generated CORTEX_3_1_PERFORMANCE_REPORT.md")
