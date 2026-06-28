"""C3 — Crossroad Reasoning activation: settings flip (cr_enabled default ON).

The Settings checks are e5-free. The wiring assertions use app_client (built in
the full regression) to confirm CR is now enabled, while BG stays frozen and C1's
difficulty learning stays on — C3 flips only the cr_enabled flag (CR mechanism
unchanged). The explore's learning is reached because C1 already opened
rpe_difficulty_learning_enabled.
"""
from __future__ import annotations

from app.core.config import Settings


# ── settings field (no app build) ───────────────────────────────────────────
def test_cr_enabled_default_is_on():
    assert Settings().cr_enabled is True


def test_cr_enabled_env_override_off(monkeypatch):
    monkeypatch.setenv("CR_ENABLED", "false")
    assert Settings().cr_enabled is False


# ── production wiring (app_client builds create_app) ────────────────────────
def test_crossroad_is_enabled(app_client):
    # C3 activates the explore; settings.cr_enabled drives CrossroadConfig.enabled.
    assert app_client.app.state.crossroad.config.enabled is True


def test_difficulty_learning_still_on(app_client):
    # The explore's learn gate (C1) must stay open for CR to feed the 35-cell.
    cfg = app_client.app.state.rpe_difficulty_service.config
    assert cfg.difficulty_learning_enabled is True


def test_bg_stays_frozen_under_c3(app_client):
    # C3 is RPE/CR-only: BG (applied hard-lock) is untouched (C2 still frozen).
    from app.basal_ganglia.advisor import BasalGangliaAdvisor

    assert isinstance(app_client.app.state.basal_ganglia, BasalGangliaAdvisor)
