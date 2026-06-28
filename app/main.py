import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from app.api.routes import router
from app.basal_ganglia.advisor import BasalGangliaAdvisor
from app.core.config import get_epinephrine_config, get_settings
from app.core.embedder import get_embedding_function
from app.core.lock_manager import LockManager
from app.core.logging import get_spinal_logger
from app.execution.factory import build_execution_swarm, get_llm_client, get_llm_mode
from app.ingress.exact_cache import ExactCache
from app.ingress.sanitizer import PromptSanitizer
from app.ingress.semantic_cache import SemanticCache
from app.ingress.thalamus import Thalamus
from app.maintenance.glymphatic import (
    CleanupTarget,
    DeleteStrategy,
    GlymphaticCleaner,
)
from app.maintenance.plc import PLC
from app.memory.store import InMemorySessionGoalStore
from app.routing.centroid_store import CentroidStore
from app.routing.continuation_detector import ContinuationDetector
from app.routing.cue_classifier import CueClassifier
from app.routing.lc import LocusCoeruleus
from app.routing.neuromodulators import Epinephrine, Glycine, Norepinephrine
from app.routing.pfc import PrefrontalCortex
from app.routing.semantic_evaluator import SemanticEvaluator
from app.routing.skip_router import SkipLogicRouter
from app.routing.crossroad import CrossroadConfig, CrossroadReasoner
from app.routing.rpe_route_override import DifficultyRouteOverride
from app.routing.routing_decay import RoutingDecay
from app.routing.routing_ratchet import RoutingRatchet
from app.routing.tier1_5 import Tier15Augmentation
from app.rpe.calculators import SynapseDifficultyDryRunCalculator
from app.rpe.difficulty_gate import SynapseDifficultyGate
from app.rpe.difficulty_learner import RPEDifficultyLearner
from app.rpe.dopamine import DopamineRPE
from app.rpe.ifom_store import InMemoryIFOMTTLOverrideStore
from app.rpe.models import ActiveMutationConfig
from app.rpe.mutators import (
    IFOMTTLMutator,
    SynapseDifficultyWeightMutator,
    SynapseStoreAdapter,
    SynapseWeightMutator,
)
from app.rpe.pipeline import RPEMutationPipelineWrapper
from app.rpe.preset_store import DifficultyPresetStore, PresettedDifficultyStore
from app.rpe.recent_counter import RPERecentCounter
from app.rpe.record_store import RPERecordStore
from app.rpe.rollback_scheduler import RollbackScheduler
from app.rpe.service import RPEMutationService
from app.rpe.sources import HeuristicOutcomeSource, MockRewardSource
from app.synapse.observer import SynapseObserver
from app.synapse.policies import FlushPolicy
from app.synapse.snapshot import SynapseSnapshotter
from app.synapse.store import SynapseStore

_SEED_PATH = Path(__file__).resolve().parent.parent / "tests" / "phase3" / "seed_queries.json"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Warm up the multilingual-e5-base embedder + build Phase 3
    centroids so the first /query request never pays cold-start cost.

    Components themselves are constructed in create_app() so plain
    `TestClient(app)` (no `with`) still works for legacy unit tests.
    """
    logger = get_spinal_logger()
    warmup_trace = await logger.new_trace()
    await logger.log_event(
        trace_id=warmup_trace,
        module_name="app.lifespan",
        event_type="warmup.started",
        payload={"seed_path": str(_SEED_PATH)},
    )

    # B3b — load the global difficulty EMA preset before the first request so a
    # new session starts from the learned value (not the 0.3 seed). Empty DB =
    # empty preset = current behaviour (read None → no routing override). Under
    # the B13 freeze nothing ever writes it, so it stays empty.
    await app.state.rpe_preset_store.load_all()

    # B4 — start the auto-rollback scheduler on the running loop. No jobs exist
    # until a mutation is applied (none under the B13 freeze). This starts the
    # shared AsyncIOScheduler (same instance the glymphatic job is added to next).
    app.state.rollback_scheduler.start()

    # B9 — register the GlymphaticCleaner cycle on the shared scheduler (started
    # above). max_instances=1 + coalesce prevent overlapping cycles — the real
    # concurrency guard, since the reused PLC lock is per-trace, not global. The
    # job is added even when disabled (run_cycle no-ops), so toggling
    # glymphatic_enabled needs no re-wiring.
    app.state.shared_scheduler.add_job(
        app.state.glymphatic_cleaner.run_cycle,
        "interval",
        minutes=app.state.settings.glymphatic_interval_minutes,
        id="glymphatic_cycle",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # SemanticCache embedder warm — first call pulls the e5 weights into
    # memory; threshold=2.0 is unreachable so this never produces a hit.
    t0 = time.perf_counter()
    await app.state.semantic_cache.get("__cortex_aev_warmup__", threshold=2.0)
    cache_warmup_ms = (time.perf_counter() - t0) * 1000.0

    # CentroidStore build — reads cache if present (fast), otherwise
    # embeds 70 paired seeds (slower, but happens once per deploy).
    t1 = time.perf_counter()
    if _SEED_PATH.exists():
        await app.state.centroid_store.build_from_seeds(_SEED_PATH)
        centroid_ms = (time.perf_counter() - t1) * 1000.0
        cache_source = "cache" if app.state.centroid_store.last_load_was_cached else "fresh"
    else:
        centroid_ms = 0.0
        cache_source = "skipped:seed_missing"

    await logger.log_event(
        trace_id=warmup_trace,
        module_name="app.lifespan",
        event_type="warmup.completed",
        payload={
            "semantic_cache_warmup_ms": round(cache_warmup_ms, 1),
            "centroid_build_ms": round(centroid_ms, 1),
            "centroid_source": cache_source,
            "total_ms": round(cache_warmup_ms + centroid_ms, 1),
        },
    )
    yield
    # B4 — stop the scheduler (wait=False: don't block shutdown on pending jobs).
    _rollback_scheduler = getattr(app.state, "rollback_scheduler", None)
    if _rollback_scheduler is not None:
        _rollback_scheduler.shutdown()
    # 수명주기 종료 — chroma client 핸들을 결정론적으로 해제한다(리소스 누수 위생,
    # 크래시 수정 아님). 프로덕션 lifespan 은 프로세스당 1회 종료다. semantic_cache 가
    # close() 를 가질 때만 호출한다(테스트가 캐시 더블로 교체했을 수 있어 방어적).
    _semantic_cache = getattr(app.state, "semantic_cache", None)
    _close = getattr(_semantic_cache, "close", None)
    if callable(_close):
        _close()
    await logger.log_event(
        trace_id=warmup_trace,
        module_name="app.lifespan",
        event_type="shutdown",
        payload={},
    )


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="CORTEX-AEV",
        version="0.1.0",
        description="Autonomous cognitive control middleware.",
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Stateless Phase 2 components.
    app.state.sanitizer = PromptSanitizer()
    app.state.thalamus = Thalamus()
    app.state.exact_cache = ExactCache(settings.database_url)
    app.state.semantic_cache = SemanticCache(settings.chroma_path)

    # Phase 3 centroid lookup. Heavy weights are loaded lazily by the
    # lifespan warmup; instantiating CentroidStore here is cheap (just
    # binds the shared embedder singleton). Must exist BEFORE the
    # evaluator so the evaluator can hold a live reference.
    app.state.centroid_store = CentroidStore()

    # SemanticEvaluator now classifies via CentroidStore (Phase 3 STEP 2).
    # The reference is shared — when lifespan builds centroids on startup,
    # the evaluator sees the populated state through the same object.
    app.state.evaluator = SemanticEvaluator(centroid_store=app.state.centroid_store)

    # Phase 3 STEP 3.2 — Epinephrine trigger lives between Evaluator and
    # the executing layer. LC consumes it to stamp the chosen tier onto
    # TaskContext.
    app.state.epinephrine_config = get_epinephrine_config()
    app.state.epinephrine = Epinephrine(app.state.epinephrine_config)

    # Phase 3.5 — Synapse Layer (Observe + Snapshot infrastructure).
    app.state.synapse_store = SynapseStore()
    app.state.synapse_observer = SynapseObserver(
        store=app.state.synapse_store,
        flush_policy=FlushPolicy(),
    )
    app.state.synapse_snapshotter = SynapseSnapshotter(store=app.state.synapse_store)

    # Phase 4 STEP 4 — LockManager + PLC (field-level concurrency control).
    # LockManager is a singleton external to TaskContext (v0.4 rule: no
    # runtime objects inside TaskContext).
    app.state.lock_manager = LockManager()
    app.state.plc = PLC(app.state.lock_manager)

    app.state.lc = LocusCoeruleus(
        epinephrine=app.state.epinephrine,
        snapshotter=app.state.synapse_snapshotter,
        lock_manager=app.state.lock_manager,
    )
    app.state.skip_router = SkipLogicRouter()

    # Phase 4 STEP 3.3b — Execution Layer (Async Swarm) 조립.
    # SemanticCache가 보유한 chromadb collection을 재사용하고, 임베더는
    # 앱 전역 공유 callable을 그대로 주입한다 (ADR-002 부분 활용).
    app.state.embedder = get_embedding_function()
    app.state.llm_client = get_llm_client()  # CORTEX_LLM_MODE 환경변수 분기
    # B1 — Tier-1.5 diff-edit shares the same injected client seam (built above);
    # mock vs live is decided here, never inside Tier15Augmentation. Constructed
    # after llm_client so the shared client exists.
    app.state.tier15 = Tier15Augmentation(llm_client=app.state.llm_client)
    # 응답 텔레메트리용 시스템 LLM 모드(mock/live). client와 동일 소스에서 읽어
    # routes가 QueryResponse.llm_mode를 채운다 (answer 출처 정직성).
    app.state.llm_mode = get_llm_mode()
    app.state.norepinephrine = Norepinephrine()
    app.state.glycine = Glycine()
    # Phase 5 STEP 5 — CueClassifier (PFC + ContinuationDetector 공유).
    app.state.cue_classifier = CueClassifier()
    # Phase 5 STEP 4 — PrefrontalCortex bounded hint 통합.
    # PFC는 LLM 없는 휴리스틱 cue hierarchy. 30ms bounded wait로
    # Planner 시작 전 hint 결정. 미주입 시 Phase 4 흐름 100% 보존.
    app.state.pfc = PrefrontalCortex(cue_classifier=app.state.cue_classifier)
    # Phase 5 STEP 5 — SessionGoalStore + ContinuationDetector.
    # Detector는 store read-only로 active_goal 확인 후 forced swarm 분기 결정.
    app.state.session_goal_store = InMemorySessionGoalStore()
    app.state.continuation_detector = ContinuationDetector(
        cue_classifier=app.state.cue_classifier,
        session_goal_store=app.state.session_goal_store,
        logger=get_spinal_logger(),
    )
    app.state.async_swarm = build_execution_swarm(
        chroma_collection=app.state.semantic_cache.collection,
        embedder=app.state.embedder,
        llm_client=app.state.llm_client,
        norepinephrine=app.state.norepinephrine,
        plc=app.state.plc,
        pfc=app.state.pfc,
    )

    # Phase 6 STEP 3.2 — RPE Pipeline (disabled-by-default).
    # B5: the single enabled switch is split into observe_enabled (observe/
    # dry-run/log gate) and active_enabled (mutation gate). Production keeps BOTH
    # False — B5 only makes observe independently togglable (capability); turning
    # production observe on is deferred to B6 (measurement harness). active_enabled
    # =False is the absolute safety invariant: no mutation fires unless explicitly
    # re-deployed with active_enabled=True.
    # SynapseStoreAdapter wraps the existing synapse_store (no store changes).
    app.state.rpe_synapse_adapter = SynapseStoreAdapter(app.state.synapse_store)
    app.state.rpe_mutator = SynapseWeightMutator(store=app.state.rpe_synapse_adapter)

    # B11 S1 — category×difficulty 35-cell isolated learning substrate. Separate
    # backend (never wraps SynapseState); the production 7-cell path above stays
    # frozen. difficulty_learning_enabled gates writes into THIS store only.
    # B3b — the store is now preset-backed: per-session weights stay in-memory
    # (B11 dynamics unchanged), with a global (category, difficulty) EMA preset
    # persisted to aiosqlite as the read-fallback so learning survives restart.
    # The preset is rolled up ONLY by learning mutations (difficulty service
    # post-apply), never by decay. lifespan loads it before the first request.
    app.state.rpe_preset_store = DifficultyPresetStore(settings.database_url)
    app.state.rpe_difficulty_store = PresettedDifficultyStore(
        preset=app.state.rpe_preset_store
    )
    app.state.rpe_difficulty_mutator = SynapseDifficultyWeightMutator(
        store=app.state.rpe_difficulty_store
    )

    # Phase 6 STEP 4 — IFOM TTL override store + mutator (disabled-by-default).
    # InMemoryIFOMTTLOverrideStore is the production backend for STEP 4.
    # Global IFOMConfig is NEVER mutated — only session-scoped overrides.
    app.state.ifom_ttl_store = InMemoryIFOMTTLOverrideStore()
    app.state.ifom_ttl_mutator = IFOMTTLMutator(store=app.state.ifom_ttl_store)

    # B3a — shared aiosqlite store for applied RPE records (raw, append). Both
    # services persist into it; rollback_id (uuid4) is the PK so a single table
    # is safe. Inert under the B13 freeze (no applies → no rows); lazy table
    # creation on first persist.
    app.state.rpe_record_store = RPERecordStore(settings.database_url)

    # B4 — auto-rollback scheduler (the last mode-2 safeguard). An applied
    # mutation is tentative: auto-rolled-back after the timeout unless confirmed.
    # Injected ONLY into the cat×difficulty service (mode-2 target). Started in
    # lifespan. Inert under the B13 freeze (no applies → no jobs).
    # B9 — one AsyncIOScheduler is shared by B4 rollback (date jobs) and B9
    # glymphatic cleanup (interval job): no second scheduler infra. RollbackScheduler
    # already accepts an injected scheduler, so we own the instance here.
    app.state.shared_scheduler = AsyncIOScheduler()
    app.state.rollback_scheduler = RollbackScheduler(
        logger=get_spinal_logger(),
        timeout_s=settings.rpe_rollback_timeout_s,
        scheduler=app.state.shared_scheduler,
    )

    app.state.rpe_mutation_service = RPEMutationService(
        mutator=app.state.rpe_mutator,
        logger=get_spinal_logger(),
        config=ActiveMutationConfig(
            observe_enabled=False,
            active_enabled=False,
            # C1 — 35-cell difficulty learning is now settings-gated (default ON).
            # On the 7-cell service this flag is the pipeline's spawn-gate for the
            # difficulty learn task (the 35-cell service below carries the learner
            # gate; both read the SAME setting). The 7-cell synapse path itself
            # stays frozen (observe=active=False) — only difficulty learning opens.
            difficulty_learning_enabled=settings.rpe_difficulty_learning_enabled,
        ),
        ifom_mutator=app.state.ifom_ttl_mutator,
        record_store=app.state.rpe_record_store,
    )
    app.state.dopamine_rpe = DopamineRPE(
        sources=[MockRewardSource(), HeuristicOutcomeSource()],
        logger=get_spinal_logger(),
    )

    # B11 S2 — dedicated 35-cell learning service (active_enabled=True applies to
    # the difficulty store ONLY; the 7-cell production service stays frozen). The
    # learner reuses dopamine_rpe for the reward signal; the gate overlays the
    # current cell onto the per-request snapshot before routing.
    app.state.rpe_difficulty_service = RPEMutationService(
        mutator=app.state.rpe_difficulty_mutator,
        logger=get_spinal_logger(),
        config=ActiveMutationConfig(
            active_enabled=True,
            # C1 — settings-gated (default ON). With this True, learn() writes the
            # (category, difficulty) cell; override/ratchet/decay consume it, so
            # route_path can shift. Applied mutations are tentative: B4 auto-reverts
            # each after the timeout unless confirmed (confirm policy NOT wired —
            # session learning is 300s-tentative; the global EMA preset rolls up at
            # apply time and persists across sessions). BG/CR remain independently
            # frozen (applied=False / cr_enabled=False).
            difficulty_learning_enabled=settings.rpe_difficulty_learning_enabled,
        ),
        record_store=app.state.rpe_record_store,
        preset_store=app.state.rpe_preset_store,
        rollback_scheduler=app.state.rollback_scheduler,
    )
    app.state.rpe_difficulty_learner = RPEDifficultyLearner(
        dopamine_rpe=app.state.dopamine_rpe,
        calculator=SynapseDifficultyDryRunCalculator(),
        service=app.state.rpe_difficulty_service,
        logger=get_spinal_logger(),
    )
    app.state.synapse_difficulty_gate = SynapseDifficultyGate(
        store=app.state.rpe_difficulty_store,
        logger=get_spinal_logger(),
    )
    # B11 S3a — biological routing override (label only). Reads the same 35-cell
    # store; an unlearned cell (None) leaves the B12 path untouched. tier unchanged.
    app.state.rpe_route_override = DifficultyRouteOverride(
        store=app.state.rpe_difficulty_store,
        logger=get_spinal_logger(),
    )
    # B11 S4 — monotonic ratchet: session no-demote (category×difficulty floor,
    # B12-native baseline). Rise = learning, fall = forgetting (S5 decay only).
    app.state.routing_ratchet = RoutingRatchet(logger=get_spinal_logger())
    # B11 S5 — step-based lazy decay: idle cells forget; below threshold the floor
    # is released one band toward baseline (the ratchet's counterpart).
    app.state.routing_decay = RoutingDecay(
        store=app.state.rpe_difficulty_store,
        ratchet=app.state.routing_ratchet,
        logger=get_spinal_logger(),
    )

    # B7 — BasalGanglia advisor wired into production (one-way: main/routes → BG).
    # Stateless recommendation-only pass; reads task_context snapshots in routes
    # and logs bg.evaluated. applied=False (type hard-lock) + recommendation is
    # never consumed (C2 flips the apply). No store / scheduler / lifespan hook.
    app.state.basal_ganglia = BasalGangliaAdvisor(logger=get_spinal_logger())

    # B10 — read-side RPE recent-outcome counter. Fed the sign of each applied
    # 35-cell mutation (real C1 outcome) by the pipeline background; read by routes
    # to fill the BG advisory's rpe_recent_* term (was 0/0 — B7). Never the gate.
    app.state.rpe_recent_counter = RPERecentCounter()

    app.state.rpe_pipeline = RPEMutationPipelineWrapper(
        inner_swarm=app.state.async_swarm,
        dopamine_rpe=app.state.dopamine_rpe,
        mutation_service=app.state.rpe_mutation_service,
        logger=get_spinal_logger(),
        difficulty_learner=app.state.rpe_difficulty_learner,
        recent_counter=app.state.rpe_recent_counter,
    )

    # B8 — Crossroad Reasoning: at a route-band crossroad (learned weight within
    # cr_margin of a band threshold), stable mode fires a background explore of
    # the adjacent (loser) band via rpe_pipeline.execute on a sub-trace, feeding
    # the 35-cell learner. Reads the difficulty store; the runner is injected so
    # this stays a leaf. Doubly frozen: cr_enabled=False here AND the explore's
    # learn is gated by difficulty_learning_enabled (B13). C3 flips both.
    app.state.crossroad = CrossroadReasoner(
        store=app.state.rpe_difficulty_store,
        explore_runner=app.state.rpe_pipeline.execute,
        logger=get_spinal_logger(),
        config=CrossroadConfig(
            enabled=settings.cr_enabled,
            stable_probability=settings.cr_stable_probability,
            explore_probability=settings.cr_explore_probability,
            margin=settings.cr_margin,
        ),
    )

    # B9 — GlymphaticCleaner: periodic age-based cleanup of the two persistent
    # stores nothing else evicts (ChromaDB semantic cache + aiosqlite RPE records).
    # The ChromaDB target wraps its delete in PLC.protect_chromadb_write so the
    # cleaner stays a leaf (it never imports PLC). Gate-independent maintenance,
    # OFF by default (glymphatic_enabled=False) — deletion is destructive, opt-in.
    # The interval job is registered on the shared scheduler in the lifespan.
    _day_s = 86400.0
    app.state.glymphatic_cleaner = GlymphaticCleaner(
        targets=(
            CleanupTarget(
                name="semantic_cache",
                store=app.state.semantic_cache,
                max_age_s=settings.glymphatic_cache_max_age_days * _day_s,
                lock_factory=lambda: app.state.plc.protect_chromadb_write(
                    "glymphatic"
                ),
            ),
            CleanupTarget(
                name="rpe_records",
                store=app.state.rpe_record_store,
                max_age_s=settings.glymphatic_record_max_age_days * _day_s,
            ),
        ),
        strategy=DeleteStrategy(),
        logger=get_spinal_logger(),
        enabled=settings.glymphatic_enabled,
        batch_limit=settings.glymphatic_batch_limit,
    )

    app.include_router(router)
    return app


app = create_app()
