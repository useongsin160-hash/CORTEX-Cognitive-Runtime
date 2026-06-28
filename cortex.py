"""
CORTEX 2.0 — Root Entry Point (Thin Wrapper)
Imports everything from the cortex/ package.

This file is preserved as the CLI entry point for backward compatibility.
All logic now lives in cortex/.
"""

# ── Re-export public API for backward compatibility ──────────────────────────
from cortex import cortex_process, init_db                  # noqa: F401
from cortex.config import GEMINI_API_KEY, MODEL_NAME, DB_PATH  # noqa: F401
from cortex.orchestrator import call_gemini                  # noqa: F401
from cortex.pipeline.thalamus import thalamus                # noqa: F401
from cortex.pipeline.irl import IRLCounter                   # noqa: F401
from cortex.pipeline.gate import propagate_confidence, gate_output_rule  # noqa: F401
from cortex.pipeline.router import route_pipeline, PIPELINE_ROUTES       # noqa: F401
from cortex.pipeline.cp3 import step_classify, step_cp3, step_extract_tag  # noqa: F401
from cortex.db import (                                      # noqa: F401
    create_session, increment_turn, get_turn_count,
    save_message, get_history,
    save_tag, get_active_tags, update_tag_weights,
    cache_get, cache_set,
    save_checkpoint, get_latest_checkpoint,
    check_compression_trigger,
)
from cortex.prompts import CORTEX_SYSTEM_PROMPT, get_system_prompt  # noqa: F401

# ──────────────────────────────────────────────────────────
# CLI entry point (unchanged from v1.5)
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    from datetime import datetime

    init_db()

    session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    print(f"\n[CORTEX 2.0] 세션 시작: {session_id}")
    print(f"[CORTEX 2.0] 모델: {MODEL_NAME}")
    print("종료: Ctrl+C 또는 'exit' 입력\n")

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input or user_input.lower() == "exit":
                print("[CORTEX] 세션 종료")
                break

            response = cortex_process(session_id, user_input)
            print(f"\nCORTEX: {response}\n")

        except KeyboardInterrupt:
            print("\n[CORTEX] 인터럽트 — 세션 종료")
            break
        except Exception as e:
            print(f"[CORTEX ERROR] {e}")
