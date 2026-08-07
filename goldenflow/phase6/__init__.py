"""Phase 6 - Closed Loop & Governance.

The original diagram's feedback loop read "Production Insights -> Better Quality ->
Happier Users". That is a narrative, not a mechanism. This phase makes it something
the system can measure itself against::

    generated tests -> run in CI -> did they catch real defects?
                    -> precision/recall of gap prediction
                    -> retune the scoring weights

* :mod:`~goldenflow.phase6.outcomes` - escaped-defect attribution. Was the journey in
  the Golden set? Was it covered? If covered, why did the test not catch it?
* :mod:`~goldenflow.phase6.tuning` - bounded, explained, reversible weight proposals.
  Nothing tunes itself.
* :mod:`~goldenflow.phase6.governance` - audit lineage for every generated artifact,
  and per-unit cost accounting.

The honest posture throughout: a gap that has not yet caused an incident is
**unresolved, not wrong**, and precision returns ``None`` rather than ``0.0`` when
nothing has resolved. A hard zero reads as "the system is broken" when the truth is
"too early to say", and that distinction decides whether a programme survives.
"""

from goldenflow.phase6.governance import (
    AuditLog,
    CostEvent,
    CostLedger,
    CostRates,
    LineageRecord,
    hash_prompt,
    lineage_from_validation,
)
from goldenflow.phase6.outcomes import (
    DefectOrigin,
    EscapedDefect,
    GapOutcome,
    OutcomeReport,
    attribute,
    build_report,
    load_defects,
)
from goldenflow.phase6.tuning import (
    ComponentEvidence,
    WeightProposal,
    apply_proposal,
    gather_evidence,
    propose_weights,
)

__all__ = [
    "AuditLog", "CostEvent", "CostLedger", "CostRates", "LineageRecord",
    "hash_prompt", "lineage_from_validation",
    "DefectOrigin", "EscapedDefect", "GapOutcome", "OutcomeReport", "attribute",
    "build_report", "load_defects",
    "ComponentEvidence", "WeightProposal", "apply_proposal", "gather_evidence",
    "propose_weights",
]
