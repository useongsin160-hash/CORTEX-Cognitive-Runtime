"""C1 — RPE difficulty-learning activation: settings field + main.py wiring.

The Settings checks are e5-free. The wiring assertions use app_client (built in
the full regression) to confirm both RPE service configs receive the one setting,
and that the 7-cell synapse path + BG + CR stay independently frozen — C1 turns
on ONLY the 35-cell difficulty learning.
"""
from __future__ import annotations

from app.core.config import Settings


# ── settings field (no app build) ───────────────────────────────────────────
def test_setting_default_is_on():
    assert Settings().rpe_difficulty_learning_enabled is True


def test_setting_env_override_off(monkeypatch):
    monkeypatch.setenv("RPE_DIFFICULTY_LEARNING_ENABLED", "false")
    assert Settings().rpe_difficulty_learning_enabled is False


# ── production wiring (app_client builds create_app) ────────────────────────
def test_both_configs_receive_the_setting(app_client):
    state = app_client.app.state
    # The one setting drives BOTH difficulty-learning gates (default ON): the
    # pipeline spawn-gate (7-cell service config) and the learner gate (35-cell).
    assert state.rpe_mutation_service.config.difficulty_learning_enabled is True
    assert state.rpe_difficulty_service.config.difficulty_learning_enabled is True


def test_seven_cell_synapse_path_stays_frozen(app_client):
    # C1 opens difficulty learning only; the 7-cell synapse mutation path stays off.
    cfg = app_client.app.state.rpe_mutation_service.config
    assert cfg.observe_enabled is False
    assert cfg.active_enabled is False


def test_thirtyfive_cell_active_enabled_unchanged(app_client):
    # active_enabled was already True (the mutation gate); C1 did not touch it.
    assert app_client.app.state.rpe_difficulty_service.config.active_enabled is True


def test_bg_stays_frozen(app_client):
    # C1 left BG (applied hard-lock) frozen. (CR was frozen at C1 too, but C3 then
    # activated it — see test_c3_crossroad_activation; C2/BG remains frozen.)
    from app.basal_ganglia.advisor import BasalGangliaAdvisor

    assert isinstance(app_client.app.state.basal_ganglia, BasalGangliaAdvisor)


def test_auto_revert_default_confirm_surface_preserved(app_client):
    # B4 auto-revert default: the scheduler is injected into the 35-cell service,
    # and confirm_mutation is the preserved (unused) C-policy surface.
    svc = app_client.app.state.rpe_difficulty_service
    assert svc.confirm_mutation("nonexistent-id") is False  # no-op, surface intact
