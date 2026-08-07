"""Projecting the test suite onto the journey graph.

Phase 2 built a graph of what users do. This projects what the suite *tests* onto
the same node set, so coverage analysis becomes a graph diff rather than a pile of
heuristics. That only works because both sides speak the canonical screen IDs from
the Phase 0 taxonomy - the naming contract earning its keep three phases later.

The unit of coverage is the **transition**, not the screen. A suite can touch every
screen in the app and still never walk ``cart -> payment_method``, and it is the
transitions where navigation regressions live.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from goldenflow.phase2.graph import JourneyGraph
from goldenflow.phase2.scoring import GoldenJourney
from goldenflow.phase3.models import ParsedTest, TestSuite


@dataclass
class TransitionCoverage:
    """One production transition and the tests that walk it."""

    source: str
    target: str
    weight: int
    covering_tests: list[str] = field(default_factory=list)

    @property
    def covered(self) -> bool:
        return bool(self.covering_tests)

    @property
    def redundancy(self) -> int:
        return len(self.covering_tests)


@dataclass
class JourneyCoverage:
    """How much of one Golden Journey the suite actually exercises."""

    journey: GoldenJourney
    covered_transitions: int = 0
    total_transitions: int = 0
    covering_tests: set[str] = field(default_factory=set)
    missing: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        if not self.total_transitions:
            return 0.0
        return round(self.covered_transitions / self.total_transitions, 3)

    @property
    def fully_covered(self) -> bool:
        return self.total_transitions > 0 and self.covered_transitions == self.total_transitions

    @property
    def uncovered(self) -> bool:
        return self.covered_transitions == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "journey_id": self.journey.journey_id,
            "name": self.journey.archetype.name,
            "score": round(self.journey.score, 1),
            "sessions": self.journey.session_count,
            "coverage_ratio": self.ratio,
            "covered": self.covered_transitions,
            "total": self.total_transitions,
            "covering_tests": sorted(self.covering_tests),
            "missing_transitions": [f"{a} -> {b}" for a, b in self.missing],
        }


@dataclass
class CoverageModel:
    """The full projection of a suite onto the journey graph."""

    transitions: dict[tuple[str, str], TransitionCoverage] = field(default_factory=dict)
    journeys: list[JourneyCoverage] = field(default_factory=list)
    screen_tests: dict[str, list[str]] = field(default_factory=dict)
    test_transitions: dict[str, set[tuple[str, str]]] = field(default_factory=dict)
    unmapped_tests: list[str] = field(default_factory=list)

    # ------------------------------------------------------------- aggregates

    @property
    def covered_transition_count(self) -> int:
        return sum(1 for t in self.transitions.values() if t.covered)

    @property
    def transition_coverage_pct(self) -> float:
        if not self.transitions:
            return 0.0
        return round(100.0 * self.covered_transition_count / len(self.transitions), 1)

    def weighted_coverage_pct(self) -> float:
        """Coverage weighted by production traffic.

        The number that actually matters. A suite can cover 80% of transitions and
        miss the three that carry half the sessions.
        """
        total = sum(t.weight for t in self.transitions.values())
        if not total:
            return 0.0
        covered = sum(t.weight for t in self.transitions.values() if t.covered)
        return round(100.0 * covered / total, 1)

    def journey_coverage(self, journey_id: str) -> JourneyCoverage | None:
        return next((j for j in self.journeys if j.journey.journey_id == journey_id), None)

    def coverage_by_archetype(self) -> dict[str, float]:
        """Feeds back into Phase 2's ``exposure`` scoring term."""
        return {j.journey.journey_id: j.ratio for j in self.journeys}

    def uncovered_transitions(self, *, min_weight: int = 1) -> list[TransitionCoverage]:
        gaps = [
            t for t in self.transitions.values()
            if not t.covered and t.weight >= min_weight
        ]
        gaps.sort(key=lambda t: -t.weight)
        return gaps

    def summary(self) -> dict[str, Any]:
        fully = sum(1 for j in self.journeys if j.fully_covered)
        none = sum(1 for j in self.journeys if j.uncovered)
        return {
            "production_transitions": len(self.transitions),
            "covered_transitions": self.covered_transition_count,
            "transition_coverage_pct": self.transition_coverage_pct,
            "traffic_weighted_coverage_pct": self.weighted_coverage_pct(),
            "journeys": len(self.journeys),
            "journeys_fully_covered": fully,
            "journeys_uncovered": none,
            "unmapped_tests": len(self.unmapped_tests),
        }


def build_coverage(
    graph: JourneyGraph,
    suite: TestSuite,
    journeys: Sequence[GoldenJourney] | None = None,
) -> CoverageModel:
    """Project every parsed test as a path over the production journey graph."""
    model = CoverageModel()

    for edge in graph.transitions:
        model.transitions[(edge.source, edge.target)] = TransitionCoverage(
            source=edge.source, target=edge.target, weight=edge.weight
        )

    screen_tests: dict[str, list[str]] = defaultdict(list)

    for test in suite.tests:
        if not test.sequence:
            model.unmapped_tests.append(test.test_id)
            continue
        model.test_transitions[test.test_id] = test.transitions
        for screen in test.covered_screens:
            screen_tests[screen].append(test.test_id)
        for transition in test.transitions:
            entry = model.transitions.get(transition)
            if entry is not None:
                entry.covering_tests.append(test.test_id)

    model.screen_tests = dict(screen_tests)

    for journey in journeys or graph.journeys:
        sequence = journey.sequence
        required = list(zip(sequence, sequence[1:]))
        coverage = JourneyCoverage(journey=journey, total_transitions=len(required))
        for transition in required:
            entry = model.transitions.get(transition)
            if entry is not None and entry.covered:
                coverage.covered_transitions += 1
                coverage.covering_tests.update(entry.covering_tests)
            else:
                coverage.missing.append(transition)
        model.journeys.append(coverage)

    model.journeys.sort(key=lambda j: -j.journey.score)
    return model


def tests_touching(suite: TestSuite, screen_id: str) -> list[ParsedTest]:
    return [t for t in suite.tests if screen_id in t.covered_screens]


def transitions_of(tests: Iterable[ParsedTest]) -> set[tuple[str, str]]:
    return {transition for test in tests for transition in test.transitions}
