"""Instrumentation readiness audit - the M2 go/no-go gate.

GoldenFlow's entire value depends on instrumentation quality it does not control.
This module answers one question before a single pound is spent on Phase 1:

    *Can this app's telemetry actually support journey mining?*

A low score is not a failure of the audit. It is the audit working - discovering
in month 2, for the cost of a sample export, what would otherwise surface in month 9
after the data platform is already built.

Scoring is deliberately weighted toward the things that cannot be fixed later by
analysis. Sparse screen coverage can be remediated by adding events. A broken
session ID cannot be reconstructed retrospectively at all, so it is weighted heavily
and reported as a hard blocker.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

from goldenflow.phase0.taxonomy import Taxonomy

DEFAULT_GATE_THRESHOLD = 70.0
"""Phase 0 exit criterion: readiness >= 70% on at least one pilot app."""

SCREEN_VIEW_EVENTS = {"screen_view", "screen_viewed", "page_view", "Screen Viewed"}
"""Event names conventionally used for navigation. Configurable per app."""


@dataclass
class DimensionScore:
    """One scored axis of the rubric."""

    name: str
    weight: float
    score: float  # 0-100
    detail: str
    blocker: bool = False
    remediation: str = ""

    @property
    def weighted(self) -> float:
        return self.score * self.weight


@dataclass
class ReadinessReport:
    app_id: str
    sample_size: int
    dimensions: list[DimensionScore] = field(default_factory=list)
    threshold: float = DEFAULT_GATE_THRESHOLD

    @property
    def score(self) -> float:
        total_weight = sum(d.weight for d in self.dimensions)
        if not total_weight:
            return 0.0
        return round(sum(d.weighted for d in self.dimensions) / total_weight, 1)

    @property
    def blockers(self) -> list[DimensionScore]:
        return [d for d in self.dimensions if d.blocker]

    @property
    def passed(self) -> bool:
        """A blocker fails the gate regardless of the aggregate score.

        An app can score 78% overall while having session IDs so broken that no
        journey can ever be reconstructed. Averaging would hide that; this does not.
        """
        return self.score >= self.threshold and not self.blockers

    @property
    def verdict(self) -> str:
        if self.passed:
            return "GO"
        if self.blockers:
            return "NO-GO (blocking defect)"
        return "NO-GO (below threshold)"

    def remediation_plan(self) -> list[str]:
        """Ordered worklist - biggest weighted deficit first."""
        gaps = [d for d in self.dimensions if d.score < 100 and d.remediation]
        gaps.sort(key=lambda d: (100 - d.score) * d.weight, reverse=True)
        return [f"[{d.name}] {d.remediation}" for d in gaps]

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "sample_size": self.sample_size,
            "score": self.score,
            "threshold": self.threshold,
            "verdict": self.verdict,
            "passed": self.passed,
            "dimensions": [
                {
                    "name": d.name,
                    "weight": d.weight,
                    "score": round(d.score, 1),
                    "detail": d.detail,
                    "blocker": d.blocker,
                }
                for d in self.dimensions
            ],
            "remediation": self.remediation_plan(),
        }

    def format(self) -> str:
        lines = [
            f"Instrumentation Readiness - {self.app_id}",
            f"{'=' * 60}",
            f"Sample size : {self.sample_size:,} events",
            f"Score       : {self.score}%  (gate: {self.threshold}%)",
            f"Verdict     : {self.verdict}",
            "",
            f"{'Dimension':<24}{'Weight':>8}{'Score':>8}   Detail",
            f"{'-' * 60}",
        ]
        for d in self.dimensions:
            flag = " !" if d.blocker else "  "
            lines.append(
                f"{d.name:<24}{d.weight:>8.2f}{d.score:>7.0f}%{flag} {d.detail}"
            )
        plan = self.remediation_plan()
        if plan:
            lines += ["", "Remediation (highest impact first):"]
            lines += [f"  {i}. {item}" for i, item in enumerate(plan, 1)]
        return "\n".join(lines)


def _parse_ts(value: Any) -> float | None:
    """Accept epoch seconds, epoch millis, or ISO-8601. Return epoch seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Heuristic: anything past year 2286 in seconds is really milliseconds.
        return float(value) / 1000.0 if value > 10_000_000_000 else float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def audit_readiness(
    events: Iterable[dict[str, Any]],
    taxonomy: Taxonomy,
    *,
    threshold: float = DEFAULT_GATE_THRESHOLD,
    screen_view_events: set[str] | None = None,
) -> ReadinessReport:
    """Score an event sample against the readiness rubric.

    Args:
        events: Raw production events. Expected keys: ``event_name``, ``screen_tag``,
            ``session_id``, ``user_id``, ``timestamp``. Missing keys are treated as
            findings rather than errors - that is precisely what is being measured.
        taxonomy: The screen taxonomy to audit against.
        threshold: Gate threshold, defaults to the Phase 0 exit criterion of 70%.
        screen_view_events: Override the navigation event names for this app.
    """
    nav_events = screen_view_events or SCREEN_VIEW_EVENTS
    sample: Sequence[dict[str, Any]] = list(events)

    report = ReadinessReport(
        app_id=taxonomy.app_id, sample_size=len(sample), threshold=threshold
    )
    if not sample:
        report.dimensions.append(
            DimensionScore(
                name="sample",
                weight=1.0,
                score=0.0,
                detail="no events supplied",
                blocker=True,
                remediation="Export a production event sample before auditing.",
            )
        )
        return report

    # --- observations -------------------------------------------------------
    observed_tags: set[str] = set()
    unmapped_tags: defaultdict[str, int] = defaultdict(int)
    screens_with_actions: set[str] = set()
    sessions: defaultdict[str, list[float]] = defaultdict(list)
    users_by_session: dict[str, set[str]] = defaultdict(set)
    events_with_session = 0
    events_with_user = 0

    for ev in sample:
        name = ev.get("event_name") or ev.get("event") or ""
        tag = ev.get("screen_tag") or ev.get("screen") or ev.get("screen_name")
        session_id = ev.get("session_id")
        user_id = ev.get("user_id")
        ts = _parse_ts(ev.get("timestamp") or ev.get("ts"))

        if session_id:
            events_with_session += 1
            if ts is not None:
                sessions[session_id].append(ts)
            if user_id:
                users_by_session[session_id].add(str(user_id))
        if user_id:
            events_with_user += 1

        if tag:
            observed_tags.add(tag)
            screen = taxonomy.resolve_tag(tag)
            if screen is None:
                unmapped_tags[tag] += 1
            elif name not in nav_events:
                screens_with_actions.add(screen.screen_id)

    # --- 1. screen coverage (0.25) -----------------------------------------
    declared = {s.screen_id for s in taxonomy.screens}
    observed_screens = {
        s.screen_id
        for tag in observed_tags
        if (s := taxonomy.resolve_tag(tag)) is not None
    }
    covered = len(observed_screens)
    screen_cov = 100.0 * covered / len(declared) if declared else 0.0
    missing = sorted(declared - observed_screens)
    report.dimensions.append(
        DimensionScore(
            name="screen_coverage",
            weight=0.25,
            score=screen_cov,
            detail=f"{covered}/{len(declared)} declared screens seen in sample",
            remediation=(
                f"Add screen-view instrumentation to {len(missing)} screen(s): "
                f"{', '.join(missing[:5])}{'...' if len(missing) > 5 else ''}"
            )
            if missing
            else "",
        )
    )

    # --- 2. action coverage (0.15) -----------------------------------------
    action_cov = 100.0 * len(screens_with_actions) / covered if covered else 0.0
    report.dimensions.append(
        DimensionScore(
            name="action_coverage",
            weight=0.15,
            score=action_cov,
            detail=f"{len(screens_with_actions)}/{covered} observed screens emit "
            f"non-navigation events",
            remediation=(
                "Instrument interaction events (taps, submits, failures) - "
                "screen views alone give sequence but no intent, so mined journeys "
                "cannot be scored by business value."
            )
            if action_cov < 100
            else "",
        )
    )

    # --- 3. session integrity (0.20) - hard blocker if broken ---------------
    session_presence = 100.0 * events_with_session / len(sample)
    ordered = sum(1 for ts in sessions.values() if ts == sorted(ts))
    ordering = 100.0 * ordered / len(sessions) if sessions else 0.0
    session_score = 0.6 * session_presence + 0.4 * ordering
    # Remediation wording scales with severity. A 99% score describing itself as
    # "session IDs are missing or unstable" trains readers to ignore the report.
    if session_presence < 80.0:
        session_remediation = (
            "Session IDs are missing or unstable. Sequence cannot be reconstructed "
            "retrospectively - this must be fixed in the app before Phase 1 begins."
        )
    elif session_score < 97.0:
        session_remediation = (
            f"{100 - session_presence:.0f}% of events carry no session ID and "
            f"{len(sessions) - ordered} session(s) arrive out of order; those events "
            f"drop silently out of every mined journey."
        )
    else:
        session_remediation = ""
    report.dimensions.append(
        DimensionScore(
            name="session_integrity",
            weight=0.20,
            score=session_score,
            detail=f"{session_presence:.0f}% events carry session_id; "
            f"{ordering:.0f}% of {len(sessions)} sessions are time-ordered",
            blocker=session_presence < 80.0,
            remediation=session_remediation,
        )
    )

    # --- 4. identity stability (0.15) ---------------------------------------
    user_presence = 100.0 * events_with_user / len(sample)
    split_sessions = sum(1 for ids in users_by_session.values() if len(ids) > 1)
    consistency = (
        100.0 * (1 - split_sessions / len(users_by_session)) if users_by_session else 0.0
    )
    identity_score = 0.5 * user_presence + 0.5 * consistency
    report.dimensions.append(
        DimensionScore(
            name="identity_stability",
            weight=0.15,
            score=identity_score,
            detail=f"{user_presence:.0f}% events carry user_id; "
            f"{split_sessions} session(s) span multiple user IDs",
            remediation=(
                "Set a stable user identity at login and keep it across reinstall. "
                "Without it, cohorting and per-user journey analysis degrade to "
                "per-session only."
            )
            if identity_score < 100
            else "",
        )
    )

    # --- 5. naming consistency (0.15) ---------------------------------------
    total_tags = len(observed_tags)
    mapped = total_tags - len(unmapped_tags)
    naming = 100.0 * mapped / total_tags if total_tags else 0.0
    worst = sorted(unmapped_tags.items(), key=lambda kv: -kv[1])[:5]
    report.dimensions.append(
        DimensionScore(
            name="naming_consistency",
            weight=0.15,
            score=naming,
            detail=f"{mapped}/{total_tags} observed tags resolve to the taxonomy",
            remediation=(
                "Unmapped tags seen in production: "
                + ", ".join(f"{t} ({n}x)" for t, n in worst)
                + ". Either add them to the taxonomy or fix the app's tagging - "
                "unmapped events are silently dropped from every journey."
            )
            if unmapped_tags
            else "",
        )
    )

    # --- 6. automation binding (0.10) ---------------------------------------
    bound = sum(1 for s in taxonomy.screens if s.is_automatable)
    binding = 100.0 * bound / len(declared) if declared else 0.0
    report.dimensions.append(
        DimensionScore(
            name="automation_binding",
            weight=0.10,
            score=binding,
            detail=f"{bound}/{len(declared)} screens have a Page Object binding",
            remediation=(
                "Bind remaining screens to Page Objects. Unbound screens produce "
                "journey specs that Phase 4 cannot resolve into executable steps."
            )
            if binding < 100
            else "",
        )
    )

    return report
