# CORTEX-AEV Core v0.7

> Cognitive Orchestration Runtime for Task Execution  
> AEV 기반 AI 인지 실행 런타임 — Async Execution, PFC, RPE, BasalGanglia Advisor

CORTEX-AEV는 LLM 요청을 단순 호출하지 않고, 입력 정제 → 의미 평가 → 라우팅 → 비동기 실행 → 목표 기억 → RPE 학습 신호 → 행동 후보 조언까지 처리하는 인지형 실행 런타임입니다.

## Current Status

- Core version: v0.7
- Phase 1~6 complete
- Final regression: `1717/1717 passed`
- Production active learning: disabled-by-default
- Conflict Resolution: deferred via ADR-014
- Live LLM integration: available as gated next step

---

## Architecture Overview

CORTEX-AEV Core v0.7 consists of:

1. Ingress / Sanitizer
2. Thalamus + Exact/Semantic Cache
3. SemanticEvaluator + LC
4. Synapse Layer
5. ContextAgent + GABA
6. Planner / Generator / AsyncSwarm
7. PLC + LockManager + Glycine
8. GoalStack + IFOM
9. PFC + ContinuationDetector
10. Dopamine RPE Stack
11. RPE Mutation Pipeline
12. BasalGanglia Advisor

---

flowchart TD
    INPUT["User Input"]
    SANITIZE["PromptSanitizer"]
    THAL["Thalamus"]
    CACHE["Exact / Semantic Cache"]
    EVAL["SemanticEvaluator"]
    LC["Locus Coeruleus"]
    SYN["Synapse Layer"]
    CTX["ContextAgent + GABA"]
    PFC["PrefrontalCortex"]
    PLAN["PlannerAgent"]
    GEN["GeneratorAgent"]
    SWARM["AsyncSwarm"]
    RPE["Dopamine RPE"]
    BG["BasalGanglia Advisor"]
    OUT["Response"]

    INPUT --> SANITIZE
    SANITIZE --> THAL
    THAL --> CACHE
    CACHE --> EVAL
    EVAL --> LC
    LC --> SYN
    SYN --> CTX
    LC --> PFC
    CTX --> PLAN
    PFC --> PLAN
    PLAN --> GEN
    GEN --> SWARM
    SWARM --> RPE
    RPE --> BG
    SWARM --> OUT

    ---

    6. Installation / Run:
   - pip install -r requirements.txt
   - uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
7. Test:
   - pytest tests/phase1/ tests/phase2/ tests/phase3/ tests/phase3_5/ tests/phase4/ tests/phase5/ tests/phase6/ -v
8. Documentation map:
   - PHASE6_COMPLETE.md
   - CORTEX_AEV_현행_구현기준_설계도_v0_7.md
   - AEV_현행_파일_디렉토리_구조_v0_7.md
   - docs/adr/INDEX.md
   - docs/measurements/phase6_final_verification.md
9. Deferred:
   - Conflict Resolution
   - production active learning enablement
   - mutation record persistence
   - source aggregation
   - Live LLM hardening
   - MSA 제품군
