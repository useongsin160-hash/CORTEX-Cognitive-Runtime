"""Phase 4 STEP 3.3b — factory.py 의존성 격리.

factory는 execution 내부 조립 전용 — routes / API schema / main을
import 해서는 안 된다 (core dependency rule).
"""
from __future__ import annotations

import inspect

import app.execution.factory as factory_module


def _import_lines(src: str) -> str:
    return "\n".join(
        line for line in src.splitlines()
        if line.strip().startswith(("import ", "from "))
    )


def test_factory_does_not_import_routes():
    imports = _import_lines(inspect.getsource(factory_module))
    assert "app.api.routes" not in imports
    assert "from app.api import routes" not in imports


def test_factory_does_not_import_api_schemas():
    imports = _import_lines(inspect.getsource(factory_module))
    assert "app.api.schemas" not in imports


def test_factory_does_not_import_main():
    # Use word-boundary-aware checks to avoid false positives with
    # app.maintenance (which is a valid STEP 4 import in factory.py).
    imports = _import_lines(inspect.getsource(factory_module))
    assert "from app.main " not in imports        # catches "from app.main import"
    assert "from app.main\n" not in imports
    assert "import app.main\n" not in imports
    assert "from app import main" not in imports


def test_core_does_not_import_execution():
    """core dependency rule — core는 execution을 import할 수 없다."""
    import app.core.config as config_module
    import app.core.embedder as embedder_module
    import app.core.errors as errors_module
    import app.core.logging as logging_module
    import app.core.model_tier as model_tier_module

    for mod in (config_module, embedder_module, errors_module,
                logging_module, model_tier_module):
        imports = _import_lines(inspect.getsource(mod))
        assert "app.execution" not in imports, f"{mod.__name__} imports execution"
