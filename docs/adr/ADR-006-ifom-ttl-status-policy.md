# ADR-006: IFOM TTL + Status-based Forgetting Policy

## Status

Accepted (Phase 5 STEP 2)

## Context

Phase 5에서 PFC + GoalStack 도입으로 인해 stale goal 누적 방지가 필요해졌다.

GoalStack은 자동 cleanup을 수행하지 않으며(STEP 1 설계), 명시적 정책으로 goal 망각을 처리해야 한다.
Dopamine RPE (Phase 6 영역)와의 연계를 위한 hook slot을 Phase 5에서 미리 마련한다.

## Decision

### TTL 정책 (Phase 5 임시값)

| 상태 | TTL | 비고 |
|------|-----|------|
| active | 60분 (3600s) | sliding TTL (last_used_at 기준) |
| paused | 60분 (3600s) | |
| completed | 10분 (600s) | |
| low priority (priority ≤ 0.3) | 5분 (300s) | status와 무관하게 적용 |
| completed + low priority | min(600, 300) = 5분 | 더 짧은 쪽 선택 |

모든 값은 `IFOMConfig` (frozen dataclass)에서 configurable.

### Status 전환 정책

TTL 초과 후 분기 순서:

1. `status == "completed"` → GoalStack에서 **즉시 제거** (`action="remove"`)
2. `is_low_priority(goal)` → `status = "expired"` (`action="mark_expired"`)
3. `active` / `paused` → `status = "expired"` (`action="mark_expired"`)

expired goal:
- GoalStack에 남아 있음 (관찰 가능)
- eviction 1순위 (STEP 1 정책 — `_STATUS_EVICTION_ORDER["expired"] = 0`)

### 호출 시점 (Hybrid)

- GoalStack의 **어떤 메서드도 자동 cleanup 금지** (add/get/list/update)
- `IFOMPolicy.cleanup_expired(context, now=None)`을 **명시적으로 호출**
- PFC 발동 시점에 호출 (STEP 3 이후 연결)
- `now` 파라미터로 테스트 주입 가능

### Phase 6 RPE Hook

```python
def adjust_ttl_with_rpe_hook(self, goal: Goal, base_ttl: float) -> float:
    # Phase 5 STEP 2: no-op
    return base_ttl
```

Phase 5 STEP 2에서는 `base_ttl`을 그대로 반환.
Phase 6 진입 시 RPE signal 기반 TTL 동적 조정 활성화.

## Consequences

**긍정적:**
- GoalStack 메모리 오염 방지
- expired status로 전환해 디버깅 가능
- PFC 발동과 자연스럽게 결합 (STEP 3 이후)
- Phase 6 RPE 연결 슬롯 사전 마련

**부정적:**
- PFC가 `cleanup_expired()` 호출을 잊으면 stale goal 누적
- TTL 값은 임시 (실측 기반 조정 필요)
- active goal 0개 상태의 PFC 동작 미정의 (DMG 영역)

## Resolution Plan

- Phase 5 STEP 3에서 PFC가 `cleanup_expired()` 자동 호출 연결
- Phase 6에서 `adjust_ttl_with_rpe_hook` 활성화 + TTL 동적 조정
- DMG (idle 상태 처리)는 Phase 5 후속 또는 후속 Phase

## References

- STEP 1: GoalStack eviction 정책 (`_STATUS_EVICTION_ORDER`)
- STEP 3: PFC → `cleanup_expired()` 연결 예정
- Phase 6: Dopamine RPE signal 기반 hook 활성화 예정
