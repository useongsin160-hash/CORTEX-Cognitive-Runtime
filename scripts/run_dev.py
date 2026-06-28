"""Launch the AEV FastAPI app under uvicorn with autoreload."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"


def _ensure_env_file() -> None:
    if ENV_FILE.exists():
        return
    if ENV_EXAMPLE.exists():
        print(
            f"[run_dev] .env not found. Copy `{ENV_EXAMPLE.name}` to `.env` first:\n"
            f"    cp {ENV_EXAMPLE} {ENV_FILE}\n"
            "Then edit values (especially ANTHROPIC_API_KEY) before re-running.",
            file=sys.stderr,
        )
    else:
        print("[run_dev] Neither .env nor .env.example exists.", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    _ensure_env_file()
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
