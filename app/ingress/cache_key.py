"""Persistent-cache namespace keys (OVERTURE A1 — read-side hardening).

영속 캐시(ExactCache=SQLite, SemanticCache=ChromaDB)가 llm_mode/슬롯 식별자를
키에 담지 않아 live 가 mock 시대 답변을 hit 하던 정직성 버그를 제거하기 위한
**단일 정규화 경로**다. get/put 양쪽이 이 모듈만 거쳐 키·네임스페이스를 만들어
드리프트를 차단한다.

설계 불변식:
  - 이 모듈은 app 내부 모듈을 import 하지 않는다(hashlib 만). 캐시 레이어를
    슬롯/팩토리 의존성에서 분리해 둔다.
  - `slot_fingerprint` 의 입력은 (tier_name, protocol, base_url, model) 네 개뿐이며
    API key 값·api_key_env 이름은 시그니처에 **존재하지 않는다** — 구조적으로
    secret 이 fingerprint/키에 들어갈 수 없다.
  - 캐시 키 안에는 prompt 와 위 식별자만 들어가며, API key 값은 어디에도 없다.
"""
from __future__ import annotations

import hashlib

# 캐시 키 스킴 버전. 스킴이 바뀌면 이 문자열을 올려 기존 엔트리를 일괄 무효화한다.
# (영속 파일은 graceful 하게 miss 처리되고 크래시하지 않는다 — 마이그레이션 불요.)
CACHE_SCHEMA = "cortex-cache-v2"

# answer 캐시 엔트리 표식. retrieval corpus / 구 Chroma 엔트리와 한 컬렉션에 섞여도
# where 필터에서 배제돼 graceful miss 가 되도록 모든 엔트리에 부착한다.
CACHE_KIND = "answer_cache"

# routes 의 캐시 read 는 LC tier 선택 **이전**이라 resolved slot/model 을 모른다.
# 그 경우 이 센티넬 네임스페이스로 조회하며, resolved live 엔트리와는 절대 매칭되지
# 않는다(아래 resolve_write_namespace 가 live unresolved write 를 거부하므로
# `(live, __unresolved__, __unresolved__)` 자리에는 엔트리가 생기지 않는다).
UNRESOLVED = "__unresolved__"

# 허용 llm_mode 집합. factory.get_llm_client() 가 실제로 인스턴스화하는 값과
# 일치시킨다(app/execution/factory.py: "mock"→MockLLMClient, "live"→LiveLLMClient,
# 그 외 ValueError). "stub" 은 core 의 mode 소스(get_llm_mode)에서 발생하지 않으므로
# (테스트 파일명에만 존재) 의도적으로 제외한다 — 새 모드를 더하려면 factory 와 함께
# 이 집합을 갱신한다.
ALLOWED_LLM_MODES: frozenset[str] = frozenset({"mock", "live"})

# canonical key joiner — ASCII Unit Separator. 네임스페이스 필드 값에 이 문자가
# 들어오면 거부한다(_reject_separator). "출현 불가"를 주석이 아니라 코드로 강제.
_FS = "\x1f"


class CacheNamespaceError(ValueError):
    """캐시 네임스페이스 입력이 유효하지 않다(알 수 없는 mode, 구분자 포함,
    slot_fingerprint 입력 누락 등). 메시지에 secret/config 값을 넣지 않는다."""


class CachePolicyError(ValueError):
    """resolved slot/model 없이 비-mock(live 등) 캐시 write 를 시도했다 — 거부.
    메시지에 secret/config 값을 넣지 않는다(모드 이름만 노출)."""


def _reject_separator(field: str, value: str) -> None:
    """네임스페이스 필드에 예약 구분자(0x1f)가 들어오면 거부한다.

    값 자체는 예외 메시지에 넣지 않는다(프롬프트/식별자 누출 방지) — 필드명만.
    """
    if _FS in value:
        raise CacheNamespaceError(
            f"cache namespace field '{field}' contains the reserved unit "
            f"separator (0x1f); refusing to build an ambiguous key."
        )


def normalize_mode(llm_mode: str | None) -> str:
    """llm_mode 를 정규화·검증한다.

    알 수 없는 값은 조용히 새 네임스페이스로 흘려보내지 않고 CacheNamespaceError
    로 거부한다(허용값은 ALLOWED_LLM_MODES). 빈 값/None 도 거부 대상이다 —
    호출부가 명시적으로 "mock"/"live" 를 넘기게 강제한다.
    """
    mode = (llm_mode or "").strip().lower()
    if mode not in ALLOWED_LLM_MODES:
        allowed = ", ".join(sorted(ALLOWED_LLM_MODES))
        raise CacheNamespaceError(
            f"unknown llm_mode '{mode or '(empty)'}'; expected one of: {allowed}."
        )
    return mode


def _normalize_id(field: str, value: str | None) -> str:
    """slot_fingerprint / model_id 정규화. None/blank → UNRESOLVED 센티넬.

    값이 있으면 strip 후 구분자 검증을 거친다.
    """
    if value is None:
        return UNRESOLVED
    v = value.strip()
    if not v:
        return UNRESOLVED
    _reject_separator(field, v)
    return v


def slot_fingerprint(
    *, tier_name: str, protocol: str, base_url: str, model: str
) -> str:
    """슬롯의 public-safe fingerprint.

    config 변경(tier/protocol/base_url/model)이 일어나면 fingerprint 가 달라져
    캐시가 자동으로 무효화된다. 입력은 정확히 이 네 개뿐 — API key 값과
    api_key_env 이름은 시그니처에 존재하지 않아 구조적으로 fingerprint 에 포함될
    수 없다. 네 입력 중 하나라도 None/blank 면 부분 명세 슬롯을 fingerprint 하지
    않고 거부한다.
    """
    parts = {
        "tier_name": tier_name,
        "protocol": protocol,
        "base_url": base_url,
        "model": model,
    }
    cleaned: dict[str, str] = {}
    for key, value in parts.items():
        if value is None or not str(value).strip():
            raise CacheNamespaceError(
                f"slot_fingerprint requires a non-empty '{key}'; refusing to "
                f"fingerprint a partially-specified slot."
            )
        cleaned[key] = str(value).strip()
        _reject_separator(key, cleaned[key])
    raw = _FS.join((
        "slotfp-v1",
        cleaned["tier_name"],
        cleaned["protocol"],
        cleaned["base_url"],
        cleaned["model"],
    ))
    return "sfp_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _namespace_fields(
    llm_mode: str | None,
    slot_fingerprint: str | None,
    model_id: str | None,
) -> dict[str, str]:
    """정규 네임스페이스를 discrete 필드로 반환한다(Chroma metadata/where 공용)."""
    return {
        "cache_schema": CACHE_SCHEMA,
        "cache_kind": CACHE_KIND,
        "llm_mode": normalize_mode(llm_mode),
        "slot_fp": _normalize_id("slot_fingerprint", slot_fingerprint),
        "model_id": _normalize_id("model_id", model_id),
    }


def _canonical_hash(fields: dict[str, str], prompt: str) -> str:
    """고정 순서 5-튜플 + prompt 를 구분자로 잇고 sha256 한다.

    prompt 는 항상 **마지막** 세그먼트다. 앞 네임스페이스 필드들은 모두 구분자가
    제거/거부된 상태이므로, prompt 내부에 구분자가 있어도 필드 경계와 혼동될 수
    없다(앞 필드가 prompt 쪽 구분자를 흡수하지 못한다). 따라서 prompt 자체는
    별도 구분자 검증 없이 그대로 해싱한다.
    """
    raw = _FS.join((
        fields["cache_schema"],
        fields["cache_kind"],
        fields["llm_mode"],
        fields["slot_fp"],
        fields["model_id"],
        prompt,
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def exact_key(
    prompt: str,
    *,
    llm_mode: str | None,
    slot_fingerprint: str | None = None,
    model_id: str | None = None,
) -> str:
    """ExactCache(SQLite) 의 prompt_hash 값(네임스페이스 포함 sha256 hex)."""
    return _canonical_hash(
        _namespace_fields(llm_mode, slot_fingerprint, model_id), prompt
    )


def semantic_id(
    prompt: str,
    *,
    llm_mode: str | None,
    slot_fingerprint: str | None = None,
    model_id: str | None = None,
) -> str:
    """SemanticCache(Chroma) upsert id. "v2_" 접두 + 네임스페이스 포함.

    id 자체가 네임스페이스를 담으므로 mock/live/slotA/slotB 가 같은 prompt 에서
    upsert id 충돌로 서로 덮어쓰지 않는다(공존).
    """
    return "v2_" + _canonical_hash(
        _namespace_fields(llm_mode, slot_fingerprint, model_id), prompt
    )


def semantic_metadata(
    *,
    llm_mode: str | None,
    slot_fingerprint: str | None = None,
    model_id: str | None = None,
    response: str,
    created_at: float,
) -> dict[str, str | float]:
    """SemanticCache 엔트리 metadata(네임스페이스 필드 + response + created_at).

    Chroma 는 metadata 값에 None 을 허용하지 않으므로 식별자/응답은 모두 문자열이다
    (미상 식별자는 UNRESOLVED 센티넬 문자열). created_at 만 숫자(epoch seconds)로,
    GlymphaticCleaner(B9)가 나이 기반 청소를 where 숫자 비교($lt)로 수행할 수 있게
    한다. 시각 자체는 put 이 주입한다 — 이 모듈은 시간 소스를 갖지 않는다(hashlib 만
    import 하는 순수 정규화 경로 불변).
    """
    fields: dict[str, str | float] = dict(
        _namespace_fields(llm_mode, slot_fingerprint, model_id)
    )
    fields["response"] = response
    fields["created_at"] = float(created_at)
    return fields


def semantic_where(
    *,
    llm_mode: str | None,
    slot_fingerprint: str | None = None,
    model_id: str | None = None,
) -> dict:
    """SemanticCache get 의 Chroma where 필터.

    chromadb 1.5.5 는 다중 조건에 **$and 형식**을 요구한다(암시적 다중키는
    "Expected where to have exactly one operator" 로 거부됨 — 실측 확인). 그
    문법을 여기서만 캡슐화해 호출부에 Chroma 문법이 흩어지지 않게 한다.
    cache_schema/cache_kind 조건이 있어 그 필드가 없는 retrieval corpus·구 엔트리는
    자연히 제외(graceful miss)된다.
    """
    fields = _namespace_fields(llm_mode, slot_fingerprint, model_id)
    return {
        "$and": [
            {"cache_schema": fields["cache_schema"]},
            {"cache_kind": fields["cache_kind"]},
            {"llm_mode": fields["llm_mode"]},
            {"slot_fp": fields["slot_fp"]},
            {"model_id": fields["model_id"]},
        ]
    }


def resolve_write_namespace(
    *,
    llm_mode: str | None,
    slot_fingerprint: str | None = None,
    model_id: str | None = None,
) -> tuple[str, str, str]:
    """write 시 네임스페이스를 확정하고 정책을 강제한다.

    비-mock(live 등) write 는 resolved slot_fingerprint·model_id 가 반드시 있어야
    하며, 없으면 CachePolicyError 로 거부한다 — unresolved 네임스페이스에 live
    답변을 영속화해 교차-hit 위험을 만들지 않는다. mock write 는 기존 테스트
    호환을 위해 unresolved 네임스페이스를 허용한다.

    반환 (mode, slot_fp, model_id) 은 정규화된 값으로, put 이 그대로 키/메타 구성에
    재사용해 get 과 동일 네임스페이스를 보장한다.
    """
    mode = normalize_mode(llm_mode)
    fp = _normalize_id("slot_fingerprint", slot_fingerprint)
    mid = _normalize_id("model_id", model_id)
    if mode != "mock" and (fp == UNRESOLVED or mid == UNRESOLVED):
        raise CachePolicyError(
            f"refusing to write a '{mode}' cache entry without a resolved slot "
            f"fingerprint and model id (would persist under the "
            f"'{UNRESOLVED}' namespace and risk cross-mode hits)."
        )
    return mode, fp, mid
