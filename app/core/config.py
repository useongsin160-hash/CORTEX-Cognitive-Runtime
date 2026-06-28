from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.model_tier import ModelTier

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/cortex_memory.db",
        description="Async SQLite URL for Tier-1 cache and Spinal Logger.",
    )
    log_level: LogLevel = Field(
        default="INFO",
        description="Spinal Logger verbosity.",
    )
    anthropic_api_key: str = Field(
        default="sk-ant-REPLACE_ME",
        description=(
            "[DEPRECATED] Legacy single-vendor Anthropic key. Retained for "
            "backward compatibility with existing .env files only. API keys are "
            "now resolved per Tier Slot via the slot's api_key_env (see "
            "app/core/slot_registry.py). Do not add new vendor-specific key "
            "fields here."
        ),
    )
    chroma_path: str = Field(
        default="./data/cortex_chroma_db",
        description="Filesystem path for ChromaDB persistent store.",
    )
    tier_slots_config_path: str = Field(
        default="./config/tier_slots.json",
        description=(
            "Path to the Tier Slot Registry config (5 independent API slots). "
            "Maps to env TIER_SLOTS_CONFIG_PATH; matches the default used by "
            "app/core/slot_registry.load_tier_slots."
        ),
    )
    rpe_rollback_timeout_s: float = Field(
        default=300.0,
        description=(
            "B4 — seconds an applied RPE mutation stays tentative before the "
            "AsyncIOScheduler auto-rolls it back, unless confirmed within the "
            "window. Start value; tune after measurement."
        ),
    )
    glymphatic_enabled: bool = Field(
        default=False,
        description=(
            "B9 — master switch for the GlymphaticCleaner periodic cleanup. OFF "
            "by default: deletion is destructive, so it is opt-in (a hygiene "
            "safety valve, NOT a learning-gate freeze). The cleaner is gate-"
            "independent maintenance; flip True once an operator wants aging."
        ),
    )
    glymphatic_interval_minutes: float = Field(
        default=30.0,
        description=(
            "B9 — minutes between GlymphaticCleaner cycles (shared AsyncIOScheduler "
            "interval job). Start value; tune after measurement."
        ),
    )
    glymphatic_cache_max_age_days: float = Field(
        default=30.0,
        description=(
            "B9 — semantic-cache entries with created_at older than this are "
            "eligible for deletion. Conservative start value."
        ),
    )
    glymphatic_record_max_age_days: float = Field(
        default=30.0,
        description=(
            "B9 — RPE mutation records with persisted_at older than this are "
            "eligible for pruning. Conservative start value."
        ),
    )
    glymphatic_batch_limit: int = Field(
        default=100,
        description=(
            "B9 — max entries the cleaner deletes per store per cycle. Bounds the "
            "blast radius of a single cycle (never a wholesale wipe)."
        ),
    )
    cr_enabled: bool = Field(
        default=True,
        description=(
            "C3 — master switch for Crossroad Reasoning's background explore. ON "
            "(default) activates it: at a near-tie route-band crossroad, stable "
            "mode (10%) fires a background explore of the adjacent band, feeding "
            "the 35-cell learner on a sub-trace. The explore's learning is gated "
            "again by rpe_difficulty_learning_enabled (C1, already ON). Set "
            "CR_ENABLED=false to freeze. Both modes are live: the stable mode and "
            "the PFC-directed explore mode (the routes-PFC uncertainty signal is "
            "surfaced since B10 and widened in C4)."
        ),
    )
    cr_stable_probability: float = Field(
        default=0.10,
        description=(
            "B8 — probability of firing the explore in stable mode (the only live "
            "mode). Start value; tune after measurement."
        ),
    )
    cr_explore_probability: float = Field(
        default=0.50,
        description=(
            "B8 — explore-mode (PFC-directed) probability. LIVE since B10 surfaced "
            "the routes-PFC uncertainty signal (widened in C4 to any low-confidence "
            "cue); this mode fires at a crossroad when the PFC is uncertain and not "
            "in emergency mode. Start value; tune after measurement."
        ),
    )
    cr_margin: float = Field(
        default=0.05,
        description=(
            "B8 — absolute window |weight - band_threshold| within which the route "
            "decision counts as a crossroad (near-tie). Start value."
        ),
    )
    rpe_difficulty_learning_enabled: bool = Field(
        default=True,
        description=(
            "C1 — master gate for the 35-cell (category×difficulty) RPE learning. "
            "True (default) activates it: the learner writes weights that the "
            "routing override/ratchet/decay consume, so route_path can shift. "
            "Wired into BOTH RPE service configs (pipeline spawn-gate + learner "
            "gate); both must be True to fire. Set RPE_DIFFICULTY_LEARNING_ENABLED"
            "=false to freeze. The 7-cell synapse path (observe/active) and BG/CR "
            "stay independently frozen."
        ),
    )
    bg_apply_enabled: bool = Field(
        default=True,
        description=(
            "C2 — master gate for BasalGanglia apply. True (default) activates it: "
            "after the routing decision is finalized (skip_router→override→ratchet), "
            "the BG advisory recommendation is consumed PROMOTE-ONLY — it may raise "
            "task_context.route_path to a heavier band (the BG redesign escalates "
            "hard-in-disguise queries) but never lowers it, so the ratchet's "
            "no-demote floor and the B12 high-difficulty baseline are never bypassed. "
            "BG runs after the ratchet, so its promotion is per-request (ephemeral) "
            "and does not raise the learned session floor. Read at routes level "
            "(state.settings); the ActionSelectionDecision.applied model rail stays "
            "False (apply adjusts the RouteDecision, not that flag). Set "
            "BG_APPLY_ENABLED=false to fall back to observe-only telemetry."
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def _default_category_tier_map() -> dict[str, ModelTier]:
    """Phase 3 category → default ModelTier mapping (mutable factory)."""
    return {
        "coding": ModelTier.DEEP_THINKING,
        "system_design": ModelTier.DEEP_THINKING,
        "math_logic": ModelTier.DEEP_THINKING,
        "data_analysis": ModelTier.HEAVY,
        "game_design": ModelTier.STANDARD,
        "writing": ModelTier.MEDIUM,
        "general": ModelTier.LIGHTWEIGHT,
    }


@dataclass(frozen=True)
class EpinephrineConfig:
    """에피네프린 트리거 설정.

    threshold는 STEP 3.1 측정 결과 기반:
      - HIGH 카테고리 p50 self-similarity = 0.3948 (Balanced)
      - LOW 카테고리는 카테고리 게이트에서 1차 차단

    주의: 이 값은 Phase 3 임시 운영값.
      - 시드 데이터 추가/변경 시 재측정 필요
      - Phase 6 Dopamine RPE 도입 시 동적 조정 대상
      - mean-centered 좌표계 산물. 0~1 감각으로 해석 금지.

    Reference: docs/adr/ADR-003-epinephrine-threshold-temporariness.md
    """

    similarity_threshold: float = 0.3948
    high_compute_categories: frozenset[str] = frozenset({
        "coding",
        "math_logic",
        "data_analysis",
        "system_design",
    })
    category_tier_map: dict[str, ModelTier] = field(
        default_factory=_default_category_tier_map,
    )


@lru_cache(maxsize=1)
def get_epinephrine_config() -> EpinephrineConfig:
    return EpinephrineConfig()

