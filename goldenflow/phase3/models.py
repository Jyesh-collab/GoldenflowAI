"""Shared models for QA asset parsing.

Every parser - Python, Java, JavaScript - produces these types, so the coverage
graph never learns which language a test was written in.

The important idea here is :class:`Confidence`. A parser can be certain that a test
touches the cart screen because it constructs ``CartPage``, or it can be guessing
because the test is named ``test_cart_flow``. Those are not the same claim, and
collapsing them produces a coverage report that looks authoritative and is partly
fiction.

So every screen attribution carries how it was derived. Low-confidence mappings are
reported for human confirmation rather than silently counted as coverage - a gap
analysis that overstates coverage is worse than no gap analysis, because it argues
against writing the test that was actually missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Confidence(str, Enum):
    """How a screen attribution was derived."""

    HIGH = "high"
    """Explicit Page Object reference resolved through the taxonomy. Trustworthy."""

    MEDIUM = "medium"
    """Inferred from a method call, import, or screen-name string literal."""

    LOW = "low"
    """Guessed from the test or file name. Reported, never counted as coverage."""

    @property
    def counts_as_coverage(self) -> bool:
        return self is not Confidence.LOW

    @property
    def rank(self) -> int:
        return {Confidence.HIGH: 3, Confidence.MEDIUM: 2, Confidence.LOW: 1}[self]


class Language(str, Enum):
    PYTHON = "python"
    JAVA = "java"
    JAVASCRIPT = "javascript"
    KOTLIN = "kotlin"


@dataclass
class ScreenReference:
    """One screen a test touches, with the evidence for that claim."""

    screen_id: str
    confidence: Confidence
    evidence: str
    line: int = 0
    order: int = 0

    def __hash__(self) -> int:
        return hash((self.screen_id, self.line, self.order))


@dataclass
class Assertion:
    """An assertion made by a test, and the screen it was made on."""

    screen_id: str | None
    kind: str
    expression: str
    line: int = 0


@dataclass
class ParsedTest:
    """One test, reduced to what coverage analysis needs."""

    test_id: str
    name: str
    file_path: str
    language: Language
    line: int = 0

    screens: list[ScreenReference] = field(default_factory=list)
    assertions: list[Assertion] = field(default_factory=list)
    page_objects: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    skipped: bool = False

    @property
    def sequence(self) -> tuple[str, ...]:
        """Ordered screens this test exercises, deduplicated consecutively.

        Sorted by **line number**, because that is source order. Discovery order is
        not: ``ast.walk`` is breadth-first, so ordering by it produces sequences
        that read backwards - ``login -> otp_verify -> home -> splash`` for a test
        that plainly starts at splash. A wrong sequence yields wrong transitions,
        which yields wrong coverage, so this ordering is load-bearing.

        ``order`` survives only as a stable tiebreak within a single line.

        Only HIGH and MEDIUM references count. A LOW-confidence guess must not be
        able to make a path look covered.
        """
        ordered = sorted(
            (r for r in self.screens if r.confidence.counts_as_coverage),
            key=lambda r: (r.line, r.order),
        )
        out: list[str] = []
        for reference in ordered:
            if not out or out[-1] != reference.screen_id:
                out.append(reference.screen_id)
        return tuple(out)

    @property
    def transitions(self) -> set[tuple[str, str]]:
        sequence = self.sequence
        return set(zip(sequence, sequence[1:]))

    @property
    def covered_screens(self) -> set[str]:
        return {
            r.screen_id for r in self.screens if r.confidence.counts_as_coverage
        }

    @property
    def uncertain_screens(self) -> list[ScreenReference]:
        return [r for r in self.screens if r.confidence is Confidence.LOW]

    @property
    def confidence(self) -> Confidence:
        """The weakest evidence in the test - a chain is as strong as its worst link."""
        if not self.screens:
            return Confidence.LOW
        return min((r.confidence for r in self.screens), key=lambda c: c.rank)

    @property
    def asserted_screens(self) -> set[str]:
        return {a.screen_id for a in self.assertions if a.screen_id}

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "name": self.name,
            "file": self.file_path,
            "language": self.language.value,
            "line": self.line,
            "sequence": list(self.sequence),
            "page_objects": self.page_objects,
            "assertions": len(self.assertions),
            "tags": self.tags,
            "skipped": self.skipped,
            "confidence": self.confidence.value,
        }


@dataclass
class PageObject:
    """A Page Object class and its locator inventory."""

    class_name: str
    file_path: str
    language: Language
    screen_id: str | None = None
    locators: dict[str, str] = field(default_factory=dict)
    methods: list[str] = field(default_factory=list)
    line: int = 0

    @property
    def is_bound(self) -> bool:
        """Whether the taxonomy maps this class to a canonical screen."""
        return self.screen_id is not None

    def locator_strategies(self) -> dict[str, int]:
        """Count locators by strategy - the input to Phase 4's stability ranking."""
        counts: dict[str, int] = {}
        for value in self.locators.values():
            counts[_strategy_of(value)] = counts.get(_strategy_of(value), 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def _strategy_of(locator: str) -> str:
    text = locator.strip()
    if text.startswith("//") or text.startswith("(//"):
        return "xpath"
    if text.startswith("~"):
        return "accessibility_id"
    if ":id/" in text or text.startswith("id="):
        return "resource_id"
    if text.startswith("-android uiautomator") or text.startswith("-ios"):
        return "platform_query"
    if text.startswith("#") or text.startswith("."):
        return "css"
    return "other"


@dataclass
class ParseIssue:
    """Something the parser could not resolve. Reported, never guessed at."""

    file_path: str
    message: str
    line: int = 0

    def format(self) -> str:
        where = f"{self.file_path}:{self.line}" if self.line else self.file_path
        return f"{where}: {self.message}"


@dataclass
class TestSuite:
    """Everything parsed from an automation repository."""

    # Stops pytest trying to collect this as a test class on account of its name.
    __test__ = False

    tests: list[ParsedTest] = field(default_factory=list)
    page_objects: list[PageObject] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)
    files_scanned: int = 0

    @property
    def parse_rate(self) -> float:
        """Share of scanned files that yielded something usable."""
        if not self.files_scanned:
            return 0.0
        failed = len({i.file_path for i in self.issues})
        return round(100.0 * (self.files_scanned - failed) / self.files_scanned, 1)

    @property
    def mapped_tests(self) -> list[ParsedTest]:
        return [t for t in self.tests if t.sequence]

    @property
    def unmapped_tests(self) -> list[ParsedTest]:
        """Tests whose screens could not be resolved.

        Reported explicitly rather than dropped. An unmapped test is invisible to
        coverage analysis, and silently excluding it inflates the apparent gap.
        """
        return [t for t in self.tests if not t.sequence]

    @property
    def mapping_rate(self) -> float:
        if not self.tests:
            return 0.0
        return round(100.0 * len(self.mapped_tests) / len(self.tests), 1)

    def page_object(self, class_name: str) -> PageObject | None:
        return next(
            (p for p in self.page_objects if p.class_name == class_name), None
        )

    def by_language(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for test in self.tests:
            counts[test.language.value] = counts.get(test.language.value, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def confidence_breakdown(self) -> dict[str, int]:
        counts = {c.value: 0 for c in Confidence}
        for test in self.tests:
            counts[test.confidence.value] += 1
        return counts

    def merge(self, other: "TestSuite") -> "TestSuite":
        return TestSuite(
            tests=self.tests + other.tests,
            page_objects=self.page_objects + other.page_objects,
            issues=self.issues + other.issues,
            files_scanned=self.files_scanned + other.files_scanned,
        )

    def summary(self) -> dict[str, Any]:
        return {
            "files_scanned": self.files_scanned,
            "parse_rate_pct": self.parse_rate,
            "tests": len(self.tests),
            "mapped_tests": len(self.mapped_tests),
            "unmapped_tests": len(self.unmapped_tests),
            "mapping_rate_pct": self.mapping_rate,
            "page_objects": len(self.page_objects),
            "bound_page_objects": sum(1 for p in self.page_objects if p.is_bound),
            "by_language": self.by_language(),
            "confidence": self.confidence_breakdown(),
            "issues": len(self.issues),
        }


def relative_id(path: Path, root: Path, suffix: str = "") -> str:
    """Stable test identifier: repo-relative path plus the test name."""
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = path.as_posix()
    return f"{rel}::{suffix}" if suffix else rel
