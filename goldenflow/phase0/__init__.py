"""Phase 0 - Foundation & Readiness.

The phase that decides whether GoldenFlow is buildable for a given app at all.

Four deliverables, each independently enforceable in CI:

* :mod:`goldenflow.phase0.taxonomy` - the screen taxonomy contract, the join key
  between production journeys and QA assets. Nothing downstream works without it.
* :mod:`goldenflow.phase0.readiness` - instrumentation readiness scoring and the
  M2 go/no-go gate.
* :mod:`goldenflow.phase0.baseline` - the immutable "before" snapshot. Without it
  no improvement claim in Phase 6 is defensible.
* :mod:`goldenflow.phase0.registry` - the protected test registry, defined here so
  it exists before anything in Phase 5 can propose a deletion.
"""

from goldenflow.phase0.baseline import BaselineSnapshot, BaselineComparison
from goldenflow.phase0.readiness import ReadinessReport, audit_readiness
from goldenflow.phase0.registry import ProtectedTest, ProtectedTestRegistry
from goldenflow.phase0.taxonomy import (
    Platform,
    ScreenDefinition,
    Sensitivity,
    Taxonomy,
    ValidationIssue,
    Severity,
)

__all__ = [
    "BaselineSnapshot",
    "BaselineComparison",
    "ReadinessReport",
    "audit_readiness",
    "ProtectedTest",
    "ProtectedTestRegistry",
    "Platform",
    "ScreenDefinition",
    "Sensitivity",
    "Taxonomy",
    "ValidationIssue",
    "Severity",
]
