"""Locators and their stability.

A journey step says "the user tapped checkout on the cart screen". Appium needs to
be told *how* to find that button, and there are usually five ways - four of which
will break on the next redesign.

The ranking below is the opinionated core of this phase:

=====================  =====  ==========================================
strategy               score  why
=====================  =====  ==========================================
accessibility_id        1.00  Semantic, set deliberately, survives layout
                              changes. Also the thing screen readers use,
                              so it tends to be maintained.
resource_id             0.80  Stable within a package but churns on
                              refactors and module moves.
semantic_xpath          0.55  Anchored to text or a stable attribute.
                              Survives layout, breaks on copy changes.
class_chain             0.45  iOS-specific, structural.
positional_xpath        0.15  Index-based. Breaks when anything is
                              inserted above it.
coordinates             0.00  Never proposed. Breaks on any device whose
                              screen differs from the one it was recorded on.
=====================  =====  ==========================================

**Uniqueness gates everything.** A locator matching three elements is not a locator,
whatever its strategy, so ``usable`` requires exactly one match. A non-unique
accessibility ID scores worse than a unique resource ID, because a test that taps
the wrong one of three matches fails in a way that looks like a product bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Strategy(str, Enum):
    ACCESSIBILITY_ID = "accessibility_id"
    RESOURCE_ID = "resource_id"
    SEMANTIC_XPATH = "semantic_xpath"
    CLASS_CHAIN = "class_chain"
    POSITIONAL_XPATH = "positional_xpath"
    COORDINATES = "coordinates"

    @property
    def stability(self) -> float:
        return {
            Strategy.ACCESSIBILITY_ID: 1.00,
            Strategy.RESOURCE_ID: 0.80,
            Strategy.SEMANTIC_XPATH: 0.55,
            Strategy.CLASS_CHAIN: 0.45,
            Strategy.POSITIONAL_XPATH: 0.15,
            Strategy.COORDINATES: 0.00,
        }[self]

    @property
    def appium_by(self) -> str:
        """The AppiumBy constant a generated test would use."""
        return {
            Strategy.ACCESSIBILITY_ID: "AppiumBy.ACCESSIBILITY_ID",
            Strategy.RESOURCE_ID: "AppiumBy.ID",
            Strategy.SEMANTIC_XPATH: "AppiumBy.XPATH",
            Strategy.CLASS_CHAIN: "AppiumBy.IOS_CLASS_CHAIN",
            Strategy.POSITIONAL_XPATH: "AppiumBy.XPATH",
            Strategy.COORDINATES: "AppiumBy.XPATH",
        }[self]

    @property
    def is_fragile(self) -> bool:
        return self.stability < 0.5


MIN_USABLE_STABILITY = 0.5
"""Below this a locator is reported but not proposed for generation. Phase 5
generating a positional XPath produces a test that passes today and fails on the
next build, which costs more trust than the missing test would have."""


@dataclass
class Locator:
    """One way to find one element."""

    strategy: Strategy
    value: str
    screen_id: str
    element_role: str = ""
    match_count: int = 1
    source: str = "unknown"
    evidence: str = ""

    @property
    def unique(self) -> bool:
        return self.match_count == 1

    @property
    def stability(self) -> float:
        """Strategy stability, penalised for ambiguity.

        Ambiguity is a harder failure than fragility: a fragile locator breaks
        loudly on the next build, an ambiguous one taps the wrong element and fails
        in a way that looks like a product bug.
        """
        if self.match_count == 0:
            return 0.0
        if self.match_count > 1:
            return round(self.strategy.stability / (1 + self.match_count), 3)
        return self.strategy.stability

    @property
    def usable(self) -> bool:
        return self.unique and self.stability >= MIN_USABLE_STABILITY

    def as_appium(self) -> str:
        return f'({self.strategy.appium_by}, "{self.value}")'

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "value": self.value,
            "screen_id": self.screen_id,
            "element_role": self.element_role,
            "stability": self.stability,
            "unique": self.unique,
            "usable": self.usable,
            "source": self.source,
        }

    def format(self) -> str:
        flag = "  " if self.usable else " !"
        matches = "" if self.unique else f" [{self.match_count} matches]"
        return (f"{flag}{self.strategy.value:<18} {self.stability:>5.2f}  "
                f"{self.value}{matches}")


def rank(candidates: list[Locator]) -> list[Locator]:
    """Best first: stability, then uniqueness, then determinism by value."""
    return sorted(candidates, key=lambda c: (-c.stability, c.match_count, c.value))


def best(candidates: list[Locator]) -> Locator | None:
    """The strongest usable locator, or None if every candidate is unusable.

    Returning None rather than the least-bad option is deliberate. A caller that
    receives a positional XPath will use it; a caller that receives None reports an
    unresolvable step, which is the honest outcome.
    """
    return next((c for c in rank(candidates) if c.usable), None)


@dataclass
class ElementLocators:
    """Every way found to locate one logical element."""

    screen_id: str
    element_role: str
    candidates: list[Locator] = field(default_factory=list)

    @property
    def resolved(self) -> Locator | None:
        return best(self.candidates)

    @property
    def is_resolved(self) -> bool:
        return self.resolved is not None

    @property
    def fallbacks(self) -> list[Locator]:
        """Usable alternatives behind the primary - the input to self-healing."""
        ranked = [c for c in rank(self.candidates) if c.usable]
        return ranked[1:]

    def diagnose(self) -> str:
        """Why this element could not be resolved. Actionable, not just 'failed'."""
        if self.is_resolved:
            return ""
        if not self.candidates:
            return (f"{self.screen_id}.{self.element_role}: no candidates found in any "
                    f"source. Add an accessibility ID in the app, or extend the crawl "
                    f"to reach this screen.")
        ambiguous = [c for c in self.candidates if not c.unique]
        if ambiguous and len(ambiguous) == len(self.candidates):
            worst = max(c.match_count for c in ambiguous)
            return (f"{self.screen_id}.{self.element_role}: every candidate is "
                    f"ambiguous (up to {worst} matches). The element needs a unique "
                    f"accessibility ID.")
        fragile = rank(self.candidates)[0]
        return (f"{self.screen_id}.{self.element_role}: only fragile candidates "
                f"(best is {fragile.strategy.value}, stability {fragile.stability}). "
                f"Generating from this would produce a test that breaks on the next "
                f"build.")
