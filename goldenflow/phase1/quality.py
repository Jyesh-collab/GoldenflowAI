"""Data quality monitors - the firewall between Phase 1 and Phase 2.

Garbage here becomes *confident nonsense* in Phase 2. A mining pipeline does not
fail on bad input; it produces a plausible-looking process model with holes in it,
and nobody can tell by looking. Every check below exists to make a specific class of
silent corruption loud.

Thresholds default to the Phase 1 exit criteria in ROADMAP.md and are configurable
per app, because "acceptable" differs between a banking app and a media app.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from goldenflow.phase0.taxonomy import Taxonomy
from goldenflow.phase1.store import EventStore
from goldenflow.phase1.tracking_plan import TrackingPlan


@dataclass
class Thresholds:
    """Phase 1 exit criteria, expressed as machine-checkable limits."""

    max_data_latency_hours: float = 4.0
    max_session_null_pct: float = 5.0
    max_orphan_pct: float = 5.0
    min_screen_coverage_pct: float = 90.0
    max_out_of_order_pct: float = 1.0
    max_duplicate_pct: float = 0.1
    min_conformance_pct: float = 95.0
    min_events: int = 1000


@dataclass
class QualityCheck:
    name: str
    passed: bool
    value: float
    threshold: float
    detail: str
    blocking: bool = True

    def format(self) -> str:
        status = "PASS" if self.passed else ("FAIL" if self.blocking else "WARN")
        return f"  [{status}]  {self.name:<24} {self.detail}"


@dataclass
class QualityReport:
    checks: list[QualityCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.blocking)

    @property
    def failures(self) -> list[QualityCheck]:
        return [c for c in self.checks if not c.passed and c.blocking]

    @property
    def warnings(self) -> list[QualityCheck]:
        return [c for c in self.checks if not c.passed and not c.blocking]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "value": c.value,
                    "threshold": c.threshold,
                    "blocking": c.blocking,
                    "detail": c.detail,
                }
                for c in self.checks
            ],
        }

    def format(self) -> str:
        lines = ["Data Quality Monitors", "=" * 70]
        lines += [c.format() for c in self.checks]
        lines.append("=" * 70)
        if self.passed:
            lines.append(f"PASSED - {len(self.checks)} checks clean")
        else:
            lines.append(
                f"FAILED - {len(self.failures)} blocking, {len(self.warnings)} warning"
            )
        return "\n".join(lines)


def run_quality_checks(
    store: EventStore,
    taxonomy: Taxonomy,
    *,
    tracking_plan: TrackingPlan | None = None,
    thresholds: Thresholds | None = None,
    now: datetime | None = None,
) -> QualityReport:
    """Run every monitor against the store."""
    t = thresholds or Thresholds()
    now = now or datetime.now(timezone.utc)
    report = QualityReport()

    total = store.count_events()

    # --- volume -----------------------------------------------------------
    report.checks.append(QualityCheck(
        name="volume",
        passed=total >= t.min_events,
        value=float(total),
        threshold=float(t.min_events),
        detail=f"{total:,} events (minimum {t.min_events:,})",
    ))
    if total == 0:
        return report

    # --- freshness --------------------------------------------------------
    latest = store.latest_timestamp()
    if latest is None:
        latency = float("inf")
    else:
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        latency = (now - latest).total_seconds() / 3600.0
    report.checks.append(QualityCheck(
        name="freshness",
        passed=latency <= t.max_data_latency_hours,
        value=round(latency, 2),
        threshold=t.max_data_latency_hours,
        detail=f"newest event is {latency:.1f}h old "
               f"(limit {t.max_data_latency_hours}h)",
    ))

    # --- session id presence ---------------------------------------------
    null_sessions = store.null_session_count()
    null_pct = 100.0 * null_sessions / total
    report.checks.append(QualityCheck(
        name="session_id_presence",
        passed=null_pct <= t.max_session_null_pct,
        value=round(null_pct, 2),
        threshold=t.max_session_null_pct,
        detail=f"{null_pct:.2f}% of events have no session_id "
               f"({null_sessions:,}); those events cannot join any journey",
    ))

    # --- orphan screen tags ----------------------------------------------
    orphans = store.orphan_count()
    orphan_pct = 100.0 * orphans / total
    report.checks.append(QualityCheck(
        name="orphan_screen_tags",
        passed=orphan_pct <= t.max_orphan_pct,
        value=round(orphan_pct, 2),
        threshold=t.max_orphan_pct,
        detail=f"{orphan_pct:.2f}% of events carry a tag absent from the taxonomy "
               f"({orphans:,}); they drop silently out of every journey",
    ))

    # --- screen coverage --------------------------------------------------
    declared = {s.screen_id for s in taxonomy.screens}
    observed = set(store.screen_counts())
    coverage = 100.0 * len(observed & declared) / len(declared) if declared else 0.0
    missing = sorted(declared - observed)
    report.checks.append(QualityCheck(
        name="screen_coverage",
        passed=coverage >= t.min_screen_coverage_pct,
        value=round(coverage, 1),
        threshold=t.min_screen_coverage_pct,
        detail=f"{len(observed & declared)}/{len(declared)} declared screens seen"
               + (f"; missing {', '.join(missing[:4])}" if missing else ""),
    ))

    # --- session ordering -------------------------------------------------
    out_of_order = 0
    sessions = store.session_ids()
    for session_id in sessions:
        stamps = [e.timestamp for e in store.session_events(session_id)]
        if stamps != sorted(stamps):
            out_of_order += 1
    ooo_pct = 100.0 * out_of_order / len(sessions) if sessions else 0.0
    report.checks.append(QualityCheck(
        name="session_ordering",
        passed=ooo_pct <= t.max_out_of_order_pct,
        value=round(ooo_pct, 2),
        threshold=t.max_out_of_order_pct,
        detail=f"{out_of_order:,}/{len(sessions):,} sessions contain out-of-order "
               f"events ({ooo_pct:.2f}%)",
        blocking=False,
    ))

    # --- duplicates -------------------------------------------------------
    # Content-addressed IDs make the store itself idempotent, so this measures
    # upstream duplication: the same action emitted twice by the app.
    counter: Counter[tuple] = Counter()
    for event in store.iter_events():
        counter[(event.session_id, event.event_name, event.timestamp)] += 1
    dupes = sum(c - 1 for c in counter.values() if c > 1)
    dupe_pct = 100.0 * dupes / total
    report.checks.append(QualityCheck(
        name="duplicate_events",
        passed=dupe_pct <= t.max_duplicate_pct,
        value=round(dupe_pct, 3),
        threshold=t.max_duplicate_pct,
        detail=f"{dupes:,} duplicate emissions ({dupe_pct:.3f}%); inflates journey "
               f"frequency and therefore Golden Journey scoring",
        blocking=False,
    ))

    # --- tracking plan conformance ---------------------------------------
    if tracking_plan is not None:
        stream = (
            {"event_name": e.event_name, "properties": e.properties,
             "screen_tag": e.screen_tag, "session_id": e.session_id,
             "user_id": e.user_id, "timestamp": e.timestamp}
            for e in store.iter_events()
        )
        result = tracking_plan.validate_stream(
            stream, screen_resolver=taxonomy.resolve_tag
        )
        report.checks.append(QualityCheck(
            name="plan_conformance",
            passed=result.conformance_pct >= t.min_conformance_pct,
            value=result.conformance_pct,
            threshold=t.min_conformance_pct,
            detail=f"{result.conformance_pct}% of events honour the tracking plan"
                   + (f"; undeclared: {', '.join(result.unknown_event_names[:4])}"
                      if result.unknown_event_names else ""),
        ))

    return report


def freshness_sla_breached(
    store: EventStore, *, max_hours: float = 4.0, now: datetime | None = None
) -> bool:
    """Standalone freshness probe for alerting between full quality runs."""
    latest = store.latest_timestamp()
    if latest is None:
        return True
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - latest > timedelta(hours=max_hours)
