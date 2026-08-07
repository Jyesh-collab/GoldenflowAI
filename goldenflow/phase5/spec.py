"""Test specifications - the contract between resolution and generation.

A spec is everything needed to write one test, assembled deterministically before an
LLM is involved: which journey, which screens, which locators, which assertions,
which conventions to follow.

The split matters. If the model is asked to decide *what* to test it will invent
plausible-looking journeys; if it is handed a spec and asked only to render it in the
repo's idiom, its failure mode becomes bad syntax rather than fabricated coverage.
Every fact in a generated test traces back to a spec field, and every spec field
traces back to production data or a resolved locator.

Specs carry **provenance**: the journey, the session count, the score, the locator
source, the model version. A generated test whose origin cannot be reconstructed is
not reviewable, and Phase 6 needs the lineage to attribute escaped defects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from goldenflow.phase2.scoring import GoldenJourney
from goldenflow.phase4.locators import Locator
from goldenflow.phase4.resolver import ScreenResolution

ACTION_VERBS = {
    "tap": ("tap", "taps"),
    "input": ("enter", "enters"),
    "assert": ("verify", "verifies"),
    "navigate": ("open", "opens"),
}


ASSERTION_PREFIXES = ("is_", "has_", "can_")
ASSERTION_SUFFIXES = ("_count", "_total", "_id", "_text")
ASSERTION_NAMES = {
    "title", "subtotal", "price", "order_id", "order_total", "text", "label",
    "result_count", "item_count", "card_count", "order_count",
}


@dataclass
class StepSpec:
    """One screen visit within a generated test."""

    screen_id: str
    display_name: str
    page_object: str | None = None
    page_object_methods: list[str] = field(default_factory=list)
    locators: dict[str, Locator] = field(default_factory=dict)
    interactions: list[str] = field(default_factory=list)
    assertions: list[str] = field(default_factory=list)
    critical: bool = False

    @property
    def assertion_method(self) -> str | None:
        """A Page Object method whose return value is worth asserting on.

        Without this the generator emits ``assert page is not None``, which cannot
        fail - instantiating a Page Object always returns an object. That is exactly
        the vacuous coverage Phase 3's ``assertion_gap`` analysis exists to catch,
        and a generator that produces it would be shipping the defect it is meant to
        close.
        """
        for method in self.page_object_methods:
            if (
                method in ASSERTION_NAMES
                or method.startswith(ASSERTION_PREFIXES)
                or method.endswith(ASSERTION_SUFFIXES)
            ):
                return method
        return None

    @property
    def action_method(self) -> str | None:
        """A method that drives the app forward, for the navigation line."""
        assertion = self.assertion_method
        for method in self.page_object_methods:
            if method != assertion and not method.startswith(ASSERTION_PREFIXES):
                if method not in ASSERTION_NAMES and not method.endswith(
                    ASSERTION_SUFFIXES
                ):
                    return method
        return None

    @property
    def resolvable(self) -> bool:
        return bool(self.locators) or self.page_object is not None

    @property
    def primary_locator(self) -> Locator | None:
        """The most stable locator on this screen - the natural interaction target."""
        if not self.locators:
            return None
        return max(self.locators.values(), key=lambda loc: loc.stability)

    def to_dict(self) -> dict[str, Any]:
        return {
            "screen_id": self.screen_id,
            "display_name": self.display_name,
            "page_object": self.page_object,
            "critical": self.critical,
            "locators": {k: v.to_dict() for k, v in self.locators.items()},
            "interactions": self.interactions,
            "assertions": self.assertions,
        }


@dataclass
class Provenance:
    """Where a generated test came from. Required for review and for Phase 6."""

    journey_id: str
    journey_name: str
    sessions: int
    session_share_pct: float
    score: float
    risk_events: int
    locator_sources: list[str] = field(default_factory=list)
    taxonomy_fingerprint: str = ""
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    generator: str = "goldenflow"
    model: str = "rule-based"

    def to_dict(self) -> dict[str, Any]:
        return {
            "journey_id": self.journey_id,
            "journey_name": self.journey_name,
            "sessions": self.sessions,
            "session_share_pct": self.session_share_pct,
            "score": self.score,
            "risk_events": self.risk_events,
            "locator_sources": self.locator_sources,
            "taxonomy_fingerprint": self.taxonomy_fingerprint,
            "generated_at": self.generated_at.isoformat(),
            "generator": self.generator,
            "model": self.model,
        }

    def as_docstring(self) -> str:
        """Provenance as a test docstring - visible to whoever reviews the PR."""
        return (
            f"{self.journey_name}\n\n"
            f"Generated by GoldenFlow from production telemetry.\n"
            f"Journey {self.journey_id}: {self.sessions:,} sessions "
            f"({self.session_share_pct:.1f}% of traffic), score {self.score:.1f}"
            + (f", {self.risk_events} failure signal(s) observed"
               if self.risk_events else "")
            + f".\nLocators from: {', '.join(self.locator_sources) or 'unknown'}."
        )


@dataclass
class TestSpec:
    """Everything needed to generate exactly one test."""

    # Stops pytest trying to collect this as a test class on account of its name.
    __test__ = False

    test_name: str
    steps: list[StepSpec] = field(default_factory=list)
    provenance: Provenance | None = None
    tags: list[str] = field(default_factory=list)
    target_path: str = ""

    @property
    def executable(self) -> bool:
        return bool(self.steps) and all(s.resolvable for s in self.steps)

    @property
    def unresolvable_steps(self) -> list[str]:
        return [s.screen_id for s in self.steps if not s.resolvable]

    @property
    def touches_critical(self) -> bool:
        return any(s.critical for s in self.steps)

    @property
    def page_objects(self) -> list[str]:
        seen: list[str] = []
        for step in self.steps:
            if step.page_object and step.page_object not in seen:
                seen.append(step.page_object)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_name": self.test_name,
            "target_path": self.target_path,
            "tags": self.tags,
            "executable": self.executable,
            "touches_critical": self.touches_critical,
            "unresolvable_steps": self.unresolvable_steps,
            "steps": [s.to_dict() for s in self.steps],
            "provenance": self.provenance.to_dict() if self.provenance else None,
        }


def _snake(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in text.lower())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


def build_spec(
    journey: GoldenJourney,
    screens: dict[str, ScreenResolution],
    *,
    taxonomy: Any = None,
    page_object_for: dict[str, str] | None = None,
    page_object_methods: dict[str, list[str]] | None = None,
    taxonomy_fingerprint: str = "",
) -> TestSpec:
    """Assemble a spec for one Golden Journey.

    Deterministic and offline. Nothing here calls a model; the model's job begins
    only once every fact is already fixed.
    """
    page_object_for = page_object_for or {}
    page_object_methods = page_object_methods or {}
    sources: set[str] = set()
    steps: list[StepSpec] = []

    for screen_id in journey.sequence:
        definition = taxonomy.by_id(screen_id) if taxonomy else None
        resolution = screens.get(screen_id)
        locators: dict[str, Locator] = {}
        if resolution is not None:
            sources |= resolution.sources
            for role, element in resolution.elements.items():
                if element.is_resolved:
                    locators[role] = element.resolved

        page_object = page_object_for.get(screen_id)
        step = StepSpec(
            screen_id=screen_id,
            display_name=definition.display_name if definition else screen_id,
            page_object=page_object,
            page_object_methods=page_object_methods.get(page_object or "", []),
            locators=locators,
            critical=bool(definition.critical) if definition else False,
        )
        # Assert on every critical screen the journey passes through: a test that
        # navigates a payment screen without checking anything is coverage on the
        # graph and catches nothing in practice.
        if step.critical and (locators or step.assertion_method):
            step.assertions.append(f"{step.display_name} is reachable and populated")
        steps.append(step)

    # Only add a terminal assertion if the last step has none. A critical final
    # screen already carries one, and both resolve to the same call - two identical
    # asserts with different messages reads as sloppy and checks nothing extra.
    if steps and not steps[-1].assertions:
        steps[-1].assertions.append(f"journey completes at {steps[-1].display_name}")

    name = _snake(journey.archetype.name or journey.journey_id)
    return TestSpec(
        test_name=f"test_{name}",
        steps=steps,
        tags=["goldenflow", "generated"] + (["critical"] if any(
            s.critical for s in steps) else []),
        provenance=Provenance(
            journey_id=journey.journey_id,
            journey_name=journey.archetype.name or journey.journey_id,
            sessions=journey.session_count,
            session_share_pct=round(journey.session_share_pct, 2),
            score=round(journey.score, 1),
            risk_events=journey.risk_events,
            locator_sources=sorted(sources),
            taxonomy_fingerprint=taxonomy_fingerprint,
        ),
    )


def build_specs(
    journeys: Sequence[GoldenJourney],
    screens: dict[str, ScreenResolution],
    **kwargs: Any,
) -> list[TestSpec]:
    return [build_spec(j, screens, **kwargs) for j in journeys]
