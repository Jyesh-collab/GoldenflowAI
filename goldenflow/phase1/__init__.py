"""Phase 1 - Data Foundation.

Builds the single source of truth. Every downstream phase reads from here and
nowhere else, which is the point: four vendor APIs queried at analysis time is
what kills these systems in production.

* :mod:`goldenflow.phase1.tracking_plan` - the event schema contract, reviewed
  like an API. The interface between the app team and GoldenFlow.
* :mod:`goldenflow.phase1.ingest` - vendor-shaped events normalised into one
  canonical form, with screen tags resolved through the Phase 0 taxonomy.
* :mod:`goldenflow.phase1.store` - the canonical event model, warehouse DDL, and
  a runnable SQLite reference implementation.
* :mod:`goldenflow.phase1.risk` - crash, ANR and error signals joined on session
  ID so a failure lands at a precise point in a journey.
* :mod:`goldenflow.phase1.pii` - enforces docs/pii-policy.md against real event
  properties. Policy that is not scanned for is policy that is not followed.
* :mod:`goldenflow.phase1.quality` - freshness, volume, drift, null and orphan
  monitors. Garbage here becomes confident nonsense in Phase 2.
"""

from goldenflow.phase1.ingest import IngestResult, normalise_event, normalise_stream
from goldenflow.phase1.pii import PiiFinding, PiiScanner, scan_events
from goldenflow.phase1.quality import QualityReport, run_quality_checks
from goldenflow.phase1.risk import RiskSignal, join_risk_signals
from goldenflow.phase1.store import CanonicalEvent, EventStore, generate_ddl
from goldenflow.phase1.tracking_plan import (
    EventSchema,
    PropertySchema,
    TrackingPlan,
    ViolationKind,
)

__all__ = [
    "IngestResult",
    "normalise_event",
    "normalise_stream",
    "PiiFinding",
    "PiiScanner",
    "scan_events",
    "QualityReport",
    "run_quality_checks",
    "RiskSignal",
    "join_risk_signals",
    "CanonicalEvent",
    "EventStore",
    "generate_ddl",
    "EventSchema",
    "PropertySchema",
    "TrackingPlan",
    "ViolationKind",
]
