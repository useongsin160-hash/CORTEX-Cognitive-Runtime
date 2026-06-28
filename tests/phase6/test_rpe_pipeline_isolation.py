"""Phase 6 STEP 3.2 — RPE pipeline isolation tests.

Verifies STEP 3.2 isolation rules:
1. pipeline.py exists and does NOT import app.execution.swarm runtime.
2. pipeline.py does NOT import app.api.routes or app.main.
3. routes.py uses state.rpe_pipeline (not state.async_swarm directly).
4. swarm.py has zero RPE imports.
5. No RPEMutationService reference outside app/rpe and main.py.
6. Background tasks (asyncio.create_task) are confined to pipeline.py.
7. No source aggregation in pipeline.py.
8. No BasalGanglia / CR references in new files.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
RPE_ROOT = APP_ROOT / "rpe"


def _collect_imports(path: Path) -> set[str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


class TestPipelineFileIsolation:
    def test_pipeline_py_exists(self) -> None:
        assert (RPE_ROOT / "pipeline.py").is_file()

    def test_pipeline_does_not_import_swarm_runtime(self) -> None:
        """pipeline.py must NOT import app.execution.swarm (runtime module).

        app.execution.swarm_models (pure data) is allowed.
        """
        imports = _collect_imports(RPE_ROOT / "pipeline.py")
        for imp in imports:
            # Forbid exactly app.execution.swarm and app.execution.swarm.X
            assert not (
                imp == "app.execution.swarm"
                or imp.startswith("app.execution.swarm.")
            ), f"pipeline.py imports swarm runtime: {imp}"

    def test_pipeline_does_not_import_routes(self) -> None:
        imports = _collect_imports(RPE_ROOT / "pipeline.py")
        for imp in imports:
            assert not imp.startswith("app.api.routes"), (
                f"pipeline.py imports routes: {imp}"
            )

    def test_pipeline_does_not_import_main(self) -> None:
        imports = _collect_imports(RPE_ROOT / "pipeline.py")
        for imp in imports:
            assert imp != "app.main", "pipeline.py imports app.main"

    def test_pipeline_does_not_import_memory(self) -> None:
        imports = _collect_imports(RPE_ROOT / "pipeline.py")
        for imp in imports:
            assert not imp.startswith("app.memory"), (
                f"pipeline.py imports memory: {imp}"
            )

    def test_pipeline_no_basal_ganglia_or_cr(self) -> None:
        src = (RPE_ROOT / "pipeline.py").read_text(encoding="utf-8")
        forbidden = ("BasalGanglia", "basal_ganglia", "ConflictResolution", "conflict_resolution")
        for token in forbidden:
            assert token not in src, f"pipeline.py references {token}"

    def test_pipeline_no_source_aggregation(self) -> None:
        src = (RPE_ROOT / "pipeline.py").read_text(encoding="utf-8")
        # aggregation is FORBIDDEN — only selection
        assert "aggregat" not in src.lower(), "pipeline.py implements source aggregation"


class TestSwarmIsolation:
    def test_swarm_has_zero_rpe_imports(self) -> None:
        imports = _collect_imports(APP_ROOT / "execution" / "swarm.py")
        for imp in imports:
            assert not imp.startswith("app.rpe"), (
                f"swarm.py imports rpe: {imp}"
            )

    def test_swarm_not_modified_for_rpe(self) -> None:
        src = (APP_ROOT / "execution" / "swarm.py").read_text(encoding="utf-8")
        assert "RPEMutationService" not in src
        assert "DopamineRPE" not in src
        assert "rpe_pipeline" not in src


class TestRoutesIsolation:
    def test_routes_uses_rpe_pipeline_not_async_swarm_direct(self) -> None:
        """routes.py must call state.rpe_pipeline.execute(), not async_swarm."""
        src = (APP_ROOT / "api" / "routes.py").read_text(encoding="utf-8")
        # rpe_pipeline must be referenced
        assert "rpe_pipeline" in src, "routes.py must use rpe_pipeline"

    def test_routes_no_direct_async_swarm_execute(self) -> None:
        """state.async_swarm.execute() must NOT appear in routes.py after STEP 3.2."""
        src = (APP_ROOT / "api" / "routes.py").read_text(encoding="utf-8")
        assert "async_swarm.execute" not in src, (
            "routes.py still calls async_swarm.execute() directly"
        )

    def test_routes_does_not_import_rpe_service(self) -> None:
        imports = _collect_imports(APP_ROOT / "api" / "routes.py")
        for imp in imports:
            assert not imp.startswith("app.rpe.service"), (
                f"routes.py imports rpe.service directly: {imp}"
            )
            assert not imp.startswith("app.rpe.mutators"), (
                f"routes.py imports rpe.mutators directly: {imp}"
            )


class TestBackgroundTaskConfinement:
    def test_background_tasks_only_in_pipeline(self) -> None:
        """asyncio.create_task for RPE must be confined to pipeline.py.

        No other file outside app/rpe/ should create RPE background tasks.
        """
        pipeline_src = (RPE_ROOT / "pipeline.py").read_text(encoding="utf-8")
        # pipeline.py must have create_task
        assert "create_task" in pipeline_src, "pipeline.py must use asyncio.create_task"

        # routes.py must NOT have create_task for RPE
        routes_src = (APP_ROOT / "api" / "routes.py").read_text(encoding="utf-8")
        # routes.py shouldn't have any asyncio.create_task for rpe
        if "create_task" in routes_src:
            # If present, it must not involve RPE symbols
            assert "rpe" not in routes_src.split("create_task")[1][:100].lower(), (
                "routes.py creates RPE background task directly"
            )

    def test_main_does_not_create_rpe_background_task(self) -> None:
        src = (APP_ROOT / "main.py").read_text(encoding="utf-8")
        if "create_task" in src:
            # Any create_task must not involve rpe_background
            assert "_rpe_background" not in src, (
                "main.py creates RPE background task directly"
            )


class TestModelsIsolation:
    def test_models_does_not_import_pipeline(self) -> None:
        imports = _collect_imports(RPE_ROOT / "models.py")
        for imp in imports:
            assert not imp.startswith("app.rpe.pipeline"), (
                f"models.py imports pipeline: {imp}"
            )

    def test_models_does_not_import_execution(self) -> None:
        imports = _collect_imports(RPE_ROOT / "models.py")
        for imp in imports:
            assert not imp.startswith("app.execution"), (
                f"models.py imports execution: {imp}"
            )
