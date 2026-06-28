"""BasalGanglia module.

Phase 6 STEP 5.1:
- Action selection advisor
- Read-only / recommendation-only
- No production behavior change

Conflict Resolution (CR) is deferred to a follow-up closeout.
"""

from app.basal_ganglia.advisor import (
    BasalGangliaAdvisor,
    build_action_selection_context_from_snapshots,
    route_path_for_candidate_type,
)
from app.basal_ganglia.models import (
    ActionCandidate,
    ActionCandidateType,
    ActionSelectionContext,
    ActionSelectionDecision,
    ActionSelectionPolicyConfig,
)
from app.basal_ganglia.policies import ActionSelectionPolicy

__all__ = [
    "ActionCandidate",
    "ActionCandidateType",
    "ActionSelectionContext",
    "ActionSelectionDecision",
    "ActionSelectionPolicy",
    "ActionSelectionPolicyConfig",
    "BasalGangliaAdvisor",
    "build_action_selection_context_from_snapshots",
    "route_path_for_candidate_type",
]
