"""Gap analysis - the first output of GoldenFlow worth paying for.

Five findings, all computed as diffs over the shared graph:

==================  ========================================================
coverage_gap        Users walk it; no test does
obsolete            A test walks it; production no longer does
stale               Covered, but the surrounding journey has changed shape
over_tested         Effort concentrated on a low-value journey
assertion_gap       Path is walked but the production failure mode is not asserted
==================  ========================================================

Two rules constrain what this module is allowed to conclude.

**Obsolescence is a candidate, never a verdict.** A path can be absent from a
sample because it is genuinely dead, or because it is seasonal, flag-gated, or
simply rare. Every obsolete finding is cross-checked against the Phase 0 protected
registry, and anything protected is removed from the deletion list and reported
separately - low traffic is not low importance, and for refunds, erasure and
accessibility the traffic signal is actively inverted.

**Severity is weighted by consequence, not volume.** A gap on a 2-session account
deletion journey outranks a gap on a 400-session browse journey, because the
registry says one is a regulatory obligation and the other is a nice-to-have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from goldenflow.phase0.registry import ProtectedTestRegistry
from goldenflow.phase0.taxonomy import Taxonomy
from goldenflow.phase3.coverage import CoverageModel
from goldenflow.phase3.models import TestSuite


class FindingKind(str, Enum):
    COVERAGE_GAP = "coverage_gap"
    OBSOLETE = "obsolete"
    STALE = "stale"
    OVER_TESTED = "over_tested"
    ASSERTION_GAP = "assertion_gap"
    UNMAPPED_TEST = "unmapped_test"
    PROTECTED_RETAINED = "protected_retained"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}[self.value]


@dataclass
class Finding:
    kind: FindingKind
    severity: Severity
    title: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    journey_id: str | None = None
    test_ids: list[str] = field(default_factory=list)
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "severity": self.severity.value,
            "title": self.title,
            "detail": self.detail,
            "evidence": self.evidence,
            "journey_id": self.journey_id,
            "test_ids": self.test_ids,
            "recommendation": self.recommendation,
        }

    def format(self) -> str:
        head = f"[{self.severity.value.upper():8}] {self.kind.value:<18} {self.title}"
        lines = [head, f"           {self.detail}"]
        if self.recommendation:
            lines.append(f"           -> {self.recommendation}")
        return "\n".join(lines)


@dataclass
class GapReport:
    findings: list[Finding] = field(default_factory=list)
    coverage: CoverageModel | None = None

    def of_kind(self, kind: FindingKind) -> list[Finding]:
        return [f for f in self.findings if f.kind is kind]

    def by_severity(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Severity}
        for finding in self.findings:
            counts[finding.severity.value] += 1
        return counts

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity is Severity.CRITICAL)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.coverage.summary() if self.coverage else {},
            "by_severity": self.by_severity(),
            "by_kind": {
                kind.value: len(self.of_kind(kind)) for kind in FindingKind
            },
            "findings": [f.to_dict() for f in self.findings],
        }

    def format(self, limit: int = 25) -> str:
        lines = ["Gap Analysis", "=" * 78]
        if self.coverage:
            summary = self.coverage.summary()
            lines.append(
                f"  transition coverage : {summary['transition_coverage_pct']}% "
                f"({summary['covered_transitions']}/"
                f"{summary['production_transitions']})"
            )
            lines.append(
                f"  traffic-weighted    : {summary['traffic_weighted_coverage_pct']}%"
            )
            lines.append(
                f"  journeys            : {summary['journeys_fully_covered']} fully "
                f"covered, {summary['journeys_uncovered']} untouched, "
                f"of {summary['journeys']}"
            )
        lines.append(f"  findings            : {self.by_severity()}")
        lines.append("=" * 78)
        for finding in self.findings[:limit]:
            lines.append(finding.format())
        if len(self.findings) > limit:
            lines.append(f"\n  ... and {len(self.findings) - limit} more")
        return "\n".join(lines)


# --------------------------------------------------------------- the analyses


def analyse_gaps(
    coverage: CoverageModel,
    suite: TestSuite,
    *,
    taxonomy: Taxonomy | None = None,
    registry: ProtectedTestRegistry | None = None,
    min_gap_weight: int = 5,
    over_test_threshold: int = 3,
) -> GapReport:
    """Run every gap analysis and return findings ordered by severity."""
    report = GapReport(coverage=coverage)
    critical_screens = (
        {s.screen_id for s in taxonomy.screens if s.critical} if taxonomy else set()
    )

    report.findings += _coverage_gaps(coverage, critical_screens, min_gap_weight)
    report.findings += _obsolete(coverage, suite, registry)
    report.findings += _stale(coverage)
    report.findings += _over_tested(coverage, over_test_threshold)
    report.findings += _assertion_gaps(coverage, suite, critical_screens)
    report.findings += _unmapped(coverage, suite)

    report.findings.sort(
        key=lambda f: (-f.severity.rank, -int(f.evidence.get("sessions", 0)))
    )
    return report


def _coverage_gaps(
    coverage: CoverageModel, critical_screens: set[str], min_weight: int
) -> list[Finding]:
    """Journeys users walk that no test does."""
    findings: list[Finding] = []

    for journey_coverage in coverage.journeys:
        if journey_coverage.fully_covered or not journey_coverage.missing:
            continue
        journey = journey_coverage.journey
        touches_critical = bool(set(journey.sequence) & critical_screens)

        if journey_coverage.uncovered and touches_critical:
            severity = Severity.CRITICAL
        elif journey_coverage.uncovered:
            severity = Severity.HIGH
        elif touches_critical:
            severity = Severity.HIGH
        else:
            severity = Severity.MEDIUM

        missing = ", ".join(f"{a} -> {b}" for a, b in journey_coverage.missing[:4])
        findings.append(Finding(
            kind=FindingKind.COVERAGE_GAP,
            severity=severity,
            title=f"{journey.archetype.name or journey.journey_id}: "
                  f"{journey_coverage.covered_transitions}/"
                  f"{journey_coverage.total_transitions} transitions covered",
            detail=f"{journey.session_count:,} sessions "
                   f"({journey.session_share_pct:.1f}% of traffic), score "
                   f"{journey.score:.1f}. Missing: {missing}",
            evidence={
                "sessions": journey.session_count,
                "score": round(journey.score, 1),
                "missing_transitions": [f"{a} -> {b}" for a, b in journey_coverage.missing],
                "touches_critical_screen": touches_critical,
                "risk_events": journey.risk_events,
            },
            journey_id=journey.journey_id,
            recommendation=(
                "Generate an Appium test for this journey (Phase 5). "
                + ("Journey touches a screen the taxonomy marks critical."
                   if touches_critical else "")
            ),
        ))

    # Individually heavy transitions that no journey happened to surface.
    reported = {
        transition
        for jc in coverage.journeys
        for transition in jc.missing
    }
    for entry in coverage.uncovered_transitions(min_weight=min_weight):
        key = (entry.source, entry.target)
        if key in reported:
            continue
        touches_critical = bool({entry.source, entry.target} & critical_screens)
        findings.append(Finding(
            kind=FindingKind.COVERAGE_GAP,
            severity=Severity.HIGH if touches_critical else Severity.LOW,
            title=f"Untested transition {entry.source} -> {entry.target}",
            detail=f"{entry.weight:,} production sessions walk this transition and "
                   f"no test covers it.",
            evidence={"sessions": entry.weight,
                      "touches_critical_screen": touches_critical},
            recommendation="Add coverage for this navigation step.",
        ))
    return findings


def _obsolete(
    coverage: CoverageModel, suite: TestSuite, registry: ProtectedTestRegistry | None
) -> list[Finding]:
    """Tests walking transitions production no longer exhibits.

    Candidates only. Anything in the protected registry is pulled out of the
    deletion list and reported as retained, because for those journeys low traffic
    is the expected state rather than evidence of death.
    """
    findings: list[Finding] = []
    production = set(coverage.transitions)

    for test_id, transitions in coverage.test_transitions.items():
        unreachable = sorted(transitions - production)
        if not unreachable or len(unreachable) < len(transitions):
            # Partially-unreachable tests are stale, not obsolete - handled below.
            continue

        if registry is not None and registry.is_protected(test_id):
            entry = registry.match(test_id)
            findings.append(Finding(
                kind=FindingKind.PROTECTED_RETAINED,
                severity=Severity.INFO,
                title=f"Protected from pruning: {test_id}",
                detail=f"No production traffic on its path, but the test is "
                       f"protected under {entry.category.value}. {entry.reason}",
                evidence={"category": entry.category.value, "owner": entry.owner,
                          "unreachable": [f"{a} -> {b}" for a, b in unreachable]},
                test_ids=[test_id],
                recommendation="Retain. Low traffic is the expected state here.",
            ))
            continue

        findings.append(Finding(
            kind=FindingKind.OBSOLETE,
            severity=Severity.LOW,
            title=f"Obsolete candidate: {test_id}",
            detail="Every transition this test walks is absent from production: "
                   + ", ".join(f"{a} -> {b}" for a, b in unreachable[:3]),
            evidence={"unreachable": [f"{a} -> {b}" for a, b in unreachable],
                      "sessions": 0},
            test_ids=[test_id],
            recommendation=(
                "CANDIDATE ONLY - requires a second corroborating signal (removed "
                "screen, retired feature flag) and human approval before any "
                "deletion PR is opened."
            ),
        ))
    return findings


def _stale(coverage: CoverageModel) -> list[Finding]:
    """Tests that still work but no longer match how the journey is walked."""
    findings: list[Finding] = []
    production = set(coverage.transitions)

    for test_id, transitions in coverage.test_transitions.items():
        unreachable = sorted(transitions - production)
        if not unreachable or len(unreachable) == len(transitions):
            continue
        findings.append(Finding(
            kind=FindingKind.STALE,
            severity=Severity.MEDIUM,
            title=f"Stale path in {test_id}",
            detail=f"{len(unreachable)} of {len(transitions)} transitions no longer "
                   f"occur in production: "
                   + ", ".join(f"{a} -> {b}" for a, b in unreachable[:3]),
            evidence={"unreachable": [f"{a} -> {b}" for a, b in unreachable],
                      "total_transitions": len(transitions)},
            test_ids=[test_id],
            recommendation="Update the test to follow the current navigation model.",
        ))
    return findings


def _over_tested(coverage: CoverageModel, threshold: int) -> list[Finding]:
    """Effort concentrated where it buys least."""
    findings: list[Finding] = []
    ranked = sorted(coverage.journeys, key=lambda j: -j.journey.score)
    median_score = (
        ranked[len(ranked) // 2].journey.score if ranked else 0.0
    )

    for journey_coverage in coverage.journeys:
        tests = journey_coverage.covering_tests
        if len(tests) < threshold or journey_coverage.journey.score >= median_score:
            continue
        findings.append(Finding(
            kind=FindingKind.OVER_TESTED,
            severity=Severity.LOW,
            title=f"{len(tests)} tests on a below-median journey: "
                  f"{journey_coverage.journey.archetype.name}",
            detail=f"Journey scores {journey_coverage.journey.score:.1f} "
                   f"(median {median_score:.1f}) yet carries {len(tests)} tests. "
                   f"Reallocating some of that effort would buy more.",
            evidence={"tests": len(tests),
                      "score": round(journey_coverage.journey.score, 1),
                      "sessions": journey_coverage.journey.session_count},
            journey_id=journey_coverage.journey.journey_id,
            test_ids=sorted(tests),
            recommendation="Consider consolidating and redirecting effort to an "
                           "uncovered high-score journey.",
        ))
    return findings


def _assertion_gaps(
    coverage: CoverageModel, suite: TestSuite, critical_screens: set[str]
) -> list[Finding]:
    """Paths that are walked but not actually checked.

    A test that navigates a screen without asserting anything about it counts as
    coverage on the graph and catches nothing in practice. On critical screens that
    is a false sense of safety, which is worse than a known gap.
    """
    findings: list[Finding] = []
    for test in suite.tests:
        if not test.sequence:
            continue
        unchecked = sorted(test.covered_screens - test.asserted_screens)
        critical_unchecked = [s for s in unchecked if s in critical_screens]
        if not critical_unchecked:
            continue
        findings.append(Finding(
            kind=FindingKind.ASSERTION_GAP,
            severity=Severity.MEDIUM,
            title=f"{test.test_id} drives critical screens without asserting",
            detail="Walks but never asserts on: " + ", ".join(critical_unchecked[:4])
                   + ". A regression on these screens would not fail this test.",
            evidence={"unasserted_critical_screens": critical_unchecked,
                      "assertions": len(test.assertions)},
            test_ids=[test.test_id],
            recommendation="Add assertions on the critical screens this test visits.",
        ))
    return findings


def _unmapped(coverage: CoverageModel, suite: TestSuite) -> list[Finding]:
    """Tests whose screens could not be resolved.

    Reported rather than dropped: an unmapped test is invisible to coverage
    analysis, so silently excluding it would inflate the apparent gap and send
    Phase 5 to write a test that already exists.
    """
    if not coverage.unmapped_tests:
        return []

    uncertain = {
        t.test_id: [r.evidence for r in t.uncertain_screens]
        for t in suite.tests
        if t.test_id in set(coverage.unmapped_tests) and t.uncertain_screens
    }
    return [Finding(
        kind=FindingKind.UNMAPPED_TEST,
        severity=Severity.MEDIUM,
        title=f"{len(coverage.unmapped_tests)} test(s) could not be mapped to screens",
        detail="These are invisible to coverage analysis. Either the Page Objects "
               "are unbound in the taxonomy, or the parser could not resolve them. "
               "Counting them as gaps would send Phase 5 to write tests that exist.",
        evidence={"tests": sorted(coverage.unmapped_tests)[:20],
                  "low_confidence_hints": uncertain},
        test_ids=sorted(coverage.unmapped_tests),
        recommendation="Bind the Page Objects in config/taxonomy.yaml, or confirm "
                       "the low-confidence attributions by hand.",
    )]


def deletable_candidates(
    report: GapReport, registry: ProtectedTestRegistry | None = None
) -> tuple[list[str], list[str]]:
    """Split obsolete candidates into ``(proposable, protected)``.

    The final safety net before Phase 5. Even here the answer is only what may be
    *proposed* - a human still approves every deletion PR.
    """
    candidates = [
        test_id
        for finding in report.of_kind(FindingKind.OBSOLETE)
        for test_id in finding.test_ids
    ]
    if registry is None:
        return candidates, []
    return registry.partition(candidates)
