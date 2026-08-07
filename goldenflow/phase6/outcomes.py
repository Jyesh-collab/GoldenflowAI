"""Outcome tracking - did any of this actually work?

Phases 2-5 produce journeys, gaps and tests. None of that is evidence. The loop only
closes when a production defect can be traced back to what GoldenFlow said about the
journey it broke:

* Was the journey in the Golden set?
* Was it covered?
* If covered, why did the test not catch it?

Those three answers are the highest-quality training signal the system has, and they
are also the only honest basis for claiming the product works.

**Precision and recall are computed against escaped defects, not against opinion.**
A gap GoldenFlow flagged that later produced an incident is a true positive. A gap it
flagged that never did is - at this stage - unproven rather than wrong, and the code
says so: :attr:`OutcomeReport.precision` is deliberately reported alongside the
unresolved count so nobody reads a low number as failure when it may be immaturity.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence


class DefectOrigin(str, Enum):
    """Why a defect escaped, from GoldenFlow's point of view."""

    UNCOVERED_GAP = "uncovered_gap"
    """GoldenFlow flagged this journey as a gap and nobody closed it. The system was
    right and was ignored - a process failure, not a detection failure."""

    COVERED_BUT_MISSED = "covered_but_missed"
    """A test walked the path and passed anyway. The most valuable signal there is:
    the coverage was real and the assertions were not."""

    UNKNOWN_JOURNEY = "unknown_journey"
    """The journey was not in the Golden set at all. Either mining missed it, or the
    instrumentation cannot see it."""

    OUT_OF_SCOPE = "out_of_scope"
    """Backend, content, or a platform issue with no journey to attribute it to.
    Excluded from precision and recall rather than counted as a miss."""


@dataclass
class EscapedDefect:
    """A production defect, as reported by whoever found it."""

    defect_id: str
    title: str
    detected_at: datetime
    release: str = ""
    journey_id: str | None = None
    screens: list[str] = field(default_factory=list)
    severity: str = "medium"
    origin: DefectOrigin | None = None
    covering_tests: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def attributed(self) -> bool:
        return self.origin is not None

    @property
    def in_scope(self) -> bool:
        return self.origin is not DefectOrigin.OUT_OF_SCOPE

    def to_dict(self) -> dict[str, Any]:
        return {
            "defect_id": self.defect_id,
            "title": self.title,
            "detected_at": self.detected_at.isoformat(),
            "release": self.release,
            "journey_id": self.journey_id,
            "screens": self.screens,
            "severity": self.severity,
            "origin": self.origin.value if self.origin else None,
            "covering_tests": self.covering_tests,
            "notes": self.notes,
        }


@dataclass
class GapOutcome:
    """What became of one gap GoldenFlow reported."""

    journey_id: str
    reported_at: datetime
    severity: str = "medium"
    closed_by_test: str | None = None
    caused_defect: str | None = None

    @property
    def closed(self) -> bool:
        return self.closed_by_test is not None

    @property
    def vindicated(self) -> bool:
        """The gap was real: it produced a defect before anyone closed it."""
        return self.caused_defect is not None

    @property
    def unresolved(self) -> bool:
        """Still open, no incident yet. Neither right nor wrong - just early."""
        return not self.closed and not self.vindicated


def attribute(
    defect: EscapedDefect,
    *,
    golden_journey_ids: Iterable[str],
    coverage_by_journey: dict[str, float] | None = None,
    tests_by_journey: dict[str, list[str]] | None = None,
) -> EscapedDefect:
    """Decide why a defect escaped.

    Deterministic, and deliberately conservative: a defect with no journey attached
    is ``OUT_OF_SCOPE`` rather than ``UNKNOWN_JOURNEY``, because guessing would
    inflate the "mining missed it" bucket with backend incidents that were never
    GoldenFlow's to catch.
    """
    coverage_by_journey = coverage_by_journey or {}
    tests_by_journey = tests_by_journey or {}
    golden = set(golden_journey_ids)

    if defect.journey_id is None:
        defect.origin = DefectOrigin.OUT_OF_SCOPE
        return defect

    if defect.journey_id not in golden:
        defect.origin = DefectOrigin.UNKNOWN_JOURNEY
        return defect

    covered = coverage_by_journey.get(defect.journey_id, 0.0)
    if covered >= 1.0:
        defect.origin = DefectOrigin.COVERED_BUT_MISSED
        defect.covering_tests = tests_by_journey.get(defect.journey_id, [])
    else:
        defect.origin = DefectOrigin.UNCOVERED_GAP
    return defect


@dataclass
class OutcomeReport:
    """The loop, measured."""

    defects: list[EscapedDefect] = field(default_factory=list)
    gaps: list[GapOutcome] = field(default_factory=list)
    window_start: datetime | None = None
    window_end: datetime | None = None

    # ------------------------------------------------------------- defects

    def by_origin(self) -> dict[str, int]:
        counts = {origin.value: 0 for origin in DefectOrigin}
        for defect in self.defects:
            if defect.origin:
                counts[defect.origin.value] += 1
        return counts

    @property
    def in_scope_defects(self) -> list[EscapedDefect]:
        return [d for d in self.defects if d.attributed and d.in_scope]

    # ---------------------------------------------------- precision / recall

    @property
    def true_positives(self) -> int:
        """Gaps GoldenFlow flagged that went on to produce a defect."""
        return sum(1 for g in self.gaps if g.vindicated)

    @property
    def false_positives(self) -> int:
        """Gaps closed by a test that never produced a defect.

        Counted only once a test closed them. An *open* gap that has not yet caused
        an incident is unresolved, not wrong - see :attr:`unresolved_gaps`.
        """
        return sum(1 for g in self.gaps if g.closed and not g.vindicated)

    @property
    def unresolved_gaps(self) -> int:
        return sum(1 for g in self.gaps if g.unresolved)

    @property
    def false_negatives(self) -> int:
        """Defects on journeys GoldenFlow did not flag as gaps."""
        return sum(
            1 for d in self.in_scope_defects
            if d.origin in (DefectOrigin.COVERED_BUT_MISSED,
                            DefectOrigin.UNKNOWN_JOURNEY)
        )

    @property
    def precision(self) -> float | None:
        """Of the gaps whose fate is known, how many mattered.

        Returns None rather than 0.0 when nothing has resolved yet. A hard zero would
        read as "the system is wrong" when the truth is "too early to say", and that
        distinction decides whether a programme gets cancelled.
        """
        decided = self.true_positives + self.false_positives
        return round(self.true_positives / decided, 3) if decided else None

    @property
    def recall(self) -> float | None:
        """Of the defects that were GoldenFlow's to catch, how many it flagged."""
        total = self.true_positives + self.false_negatives
        return round(self.true_positives / total, 3) if total else None

    @property
    def assertion_blind_spot(self) -> float | None:
        """Share of in-scope defects that slipped past a *passing* test.

        The number that most directly justifies Phase 3's assertion-gap analysis.
        """
        in_scope = self.in_scope_defects
        if not in_scope:
            return None
        missed = sum(1 for d in in_scope
                     if d.origin is DefectOrigin.COVERED_BUT_MISSED)
        return round(missed / len(in_scope), 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "window": {
                "start": self.window_start.isoformat() if self.window_start else None,
                "end": self.window_end.isoformat() if self.window_end else None,
            },
            "defects": len(self.defects),
            "in_scope_defects": len(self.in_scope_defects),
            "by_origin": self.by_origin(),
            "gaps_reported": len(self.gaps),
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "unresolved_gaps": self.unresolved_gaps,
            "precision": self.precision,
            "recall": self.recall,
            "assertion_blind_spot": self.assertion_blind_spot,
        }

    def format(self) -> str:
        origins = self.by_origin()
        precision = "n/a (nothing resolved yet)" if self.precision is None \
            else f"{self.precision:.1%}"
        recall = "n/a" if self.recall is None else f"{self.recall:.1%}"
        blind = "n/a" if self.assertion_blind_spot is None \
            else f"{self.assertion_blind_spot:.1%}"

        lines = [
            "Outcome tracking - did the loop close?",
            "=" * 74,
            f"  escaped defects       : {len(self.defects)} "
            f"({len(self.in_scope_defects)} in scope)",
            "",
            "  Why they escaped:",
            f"    uncovered gap       : {origins['uncovered_gap']}  "
            f"(GoldenFlow flagged it; nobody closed it)",
            f"    covered but missed  : {origins['covered_but_missed']}  "
            f"(a test walked the path and passed anyway)",
            f"    unknown journey     : {origins['unknown_journey']}  "
            f"(mining or instrumentation missed it)",
            f"    out of scope        : {origins['out_of_scope']}  "
            f"(no journey to attribute it to)",
            "",
            f"  gaps reported         : {len(self.gaps)}",
            f"    vindicated (TP)     : {self.true_positives}",
            f"    closed, no incident : {self.false_positives}",
            f"    still open          : {self.unresolved_gaps}  "
            f"(unproven, not wrong)",
            "",
            f"  precision             : {precision}",
            f"  recall                : {recall}",
            f"  assertion blind spot  : {blind}  "
            f"(defects that passed a green test)",
            "=" * 74,
        ]
        return "\n".join(lines)


def load_defects(path: str | Path) -> list[EscapedDefect]:
    """Read escaped defects from JSON exported from Jira or similar."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    records = raw.get("defects", raw) if isinstance(raw, dict) else raw
    defects: list[EscapedDefect] = []
    for record in records:
        detected = record.get("detected_at")
        defects.append(EscapedDefect(
            defect_id=record["defect_id"],
            title=record.get("title", ""),
            detected_at=datetime.fromisoformat(detected.replace("Z", "+00:00"))
            if detected else datetime.now(timezone.utc),
            release=record.get("release", ""),
            journey_id=record.get("journey_id"),
            screens=record.get("screens", []),
            severity=record.get("severity", "medium"),
            covering_tests=record.get("covering_tests", []),
            notes=record.get("notes", ""),
        ))
    return defects


def build_report(
    defects: Sequence[EscapedDefect],
    gaps: Sequence[GapOutcome],
    *,
    golden_journey_ids: Iterable[str],
    coverage_by_journey: dict[str, float] | None = None,
    tests_by_journey: dict[str, list[str]] | None = None,
) -> OutcomeReport:
    golden = list(golden_journey_ids)
    attributed = [
        attribute(d, golden_journey_ids=golden,
                  coverage_by_journey=coverage_by_journey,
                  tests_by_journey=tests_by_journey)
        for d in defects
    ]
    stamps = [d.detected_at for d in attributed]
    return OutcomeReport(
        defects=attributed,
        gaps=list(gaps),
        window_start=min(stamps) if stamps else None,
        window_end=max(stamps) if stamps else None,
    )
