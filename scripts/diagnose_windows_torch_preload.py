"""Step 1 diagnostic: torch pre-load with thread/env restrictions.

All prints are immediately flushed so we can pinpoint the crash stage
even when the process is killed by a Windows access violation (0xC0000005).

Exit codes:
  0 — model loaded successfully
  1 — Python-level exception
  (process killed / exit -1073741819) — access violation
"""
import sys
import os

def p(msg):
    print(msg, flush=True)

p("=== Step 1: Windows torch pre-load diagnostic ===\n")

# ── Phase 1: env vars BEFORE any torch/numpy import ──────────────────────────
p("[1] Setting thread-limit env vars...")
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
p("    done\n")

# ── Phase 2: import torch ─────────────────────────────────────────────────────
p("[2] import torch ...")
try:
    import torch
    p(f"    torch {torch.__version__} imported OK\n")
except Exception as exc:
    p(f"    EXCEPTION: {exc}")
    sys.exit(1)

# ── Phase 3: set thread counts ────────────────────────────────────────────────
p("[3] torch.set_num_threads(1) ...")
torch.set_num_threads(1)
p(f"    get_num_threads() = {torch.get_num_threads()}")
try:
    torch.set_num_interop_threads(1)
    p(f"    get_num_interop_threads() = {torch.get_num_interop_threads()}\n")
except RuntimeError as exc:
    p(f"    set_num_interop_threads RuntimeError (continuing): {exc}\n")

# ── Phase 4: sys.path ─────────────────────────────────────────────────────────
p("[4] sys.path setup ...")
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
p(f"    repo_root = {repo_root}\n")

# ── Phase 5: import SentenceTransformer ──────────────────────────────────────
p("[5] import SentenceTransformer ...")
try:
    from sentence_transformers import SentenceTransformer
    p("    SentenceTransformer imported OK\n")
except Exception as exc:
    p(f"    EXCEPTION: {exc}")
    sys.exit(1)

# ── Phase 6: instantiate model (this is where crash previously occurred) ──────
p("[6] SentenceTransformer('intfloat/multilingual-e5-base') ...")
p("    (process dies here = access violation with these settings)")
try:
    model = SentenceTransformer("intfloat/multilingual-e5-base")
    p(f"    SUCCESS: {type(model).__name__}\n")
except Exception as exc:
    p(f"    EXCEPTION: {type(exc).__name__}: {exc}")
    sys.exit(1)

# ── Phase 7: encode ───────────────────────────────────────────────────────────
p("[7] model.encode(['hello world']) ...")
try:
    vecs = model.encode(["hello world"], normalize_embeddings=True)
    p(f"    SUCCESS: dim={len(vecs[0])}\n")
except Exception as exc:
    p(f"    EXCEPTION: {type(exc).__name__}: {exc}")
    sys.exit(1)

p("=== RESULT: Step 1 PASSED ===")
sys.exit(0)
