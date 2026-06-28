#!/usr/bin/env python3
"""Live preflight for the Tier Slot Registry (설계 4-4, V1).

5칸이 라이브 호출 준비가 됐는지 점검한다:
  - protocol 이 알려진 양식 이름인지 (V1: KNOWN_PROTOCOLS 화이트리스트.
    V3 에서 ADAPTERS 구현 여부 검사로 대체)
  - base_url / model 이 비어있지 않은지
  - 필요한 API 키가 환경변수에 존재하는지 (allow_empty_api_key 규칙 적용)

API 키 **값**은 절대 출력하지 않는다 (env 이름까지만).
평가 로직(evaluate_slot)의 단일 소스는 app.core.slot_registry 다 — core /health 의
slots_ready 와 동일 함수를 공유해 검증이 두 곳으로 갈라지지 않는다.
exit code: 전 칸 OK/OK_NO_AUTH 면 0, 하나라도 문제면 1.

사용:
  python scripts/check_llm_slots.py
  python scripts/check_llm_slots.py --path config/tier_slots.example.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.model_tier import ModelTier  # noqa: E402
from app.core.slot_registry import (  # noqa: E402
    LiveModeFallbackError,
    SlotRegistryError,
    evaluate_slot,
    load_tier_slots,
)

_TIER_COL = 14
_STATUS_COL = 13


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tier Slot Registry live preflight (no network calls).",
    )
    parser.add_argument(
        "--path",
        default=None,
        help="tier_slots.json 경로 (기본: TIER_SLOTS_CONFIG_PATH 또는 ./config/tier_slots.json)",
    )
    args = parser.parse_args(argv)

    try:
        registry = load_tier_slots(args.path)
    except LiveModeFallbackError as exc:
        print(f"NO-GO: {exc}")
        return 1
    except SlotRegistryError as exc:
        print(f"NO-GO: {exc}")
        return 1

    print(f"{'TIER':<{_TIER_COL}} {'STATUS':<{_STATUS_COL}} DETAIL")
    print("-" * 72)

    all_ok = True
    for tier in ModelTier:
        slot = getattr(registry, tier.name)
        status, detail = evaluate_slot(slot)
        if status not in ("OK", "OK_NO_AUTH"):
            all_ok = False
        print(f"{tier.name:<{_TIER_COL}} {status:<{_STATUS_COL}} {detail}")

    print("-" * 72)
    print("RESULT: GO" if all_ok else "RESULT: NO-GO")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
