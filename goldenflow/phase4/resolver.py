"""Locator resolution - turning journey steps into something Appium can run.

Phase 2 says users walk ``cart -> payment_method``. Phase 3 says no test covers it.
Neither knows how to *find* the checkout button. This module closes that, from three
independent sources:

1. **Page Objects** (primary) - highest confidence, zero extra infrastructure,
   works wherever POM discipline already exists. Also the only source that reflects
   what the automation team has decided the locators *should* be.
2. **Crawl dumps** (gap-filling) - a Robo/Appium crawl reaches screens the suite has
   no Page Object for. This is what makes the Phase 3 payment-failure gap closeable
   rather than merely reported.
3. **Session replay hierarchy** (last resort) - covers states a crawler cannot
   reach: post-payment, error conditions that need a real backend response.

Sources are merged, not chosen between. A screen resolved by two sources that agree
is stronger evidence than either alone, and where they disagree that is worth
knowing before Phase 5 generates against it.

**Unresolved steps are reported with diagnostics, never silently dropped.** A step
missing from the output looks identical to a step that did not exist, and Phase 5
would generate a test with a hole in the middle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from goldenflow.phase0.taxonomy import Taxonomy
from goldenflow.phase2.scoring import GoldenJourney
from goldenflow.phase3.models import TestSuite
from goldenflow.phase4.hierarchy import ScreenDump
from goldenflow.phase4.locators import (
    ElementLocators,
    Locator,
    Strategy,
    best,
    rank,
)

SOURCE_PAGE_OBJECT = "page_object"
SOURCE_CRAWL = "crawl"
SOURCE_REPLAY = "replay"

SOURCE_PRIORITY = {SOURCE_PAGE_OBJECT: 3, SOURCE_CRAWL: 2, SOURCE_REPLAY: 1}


def _infer_strategy(value: str) -> Strategy:
    text = value.strip()
    if text.startswith("~"):
        return Strategy.ACCESSIBILITY_ID
    if ":id/" in text or text.startswith("id="):
        return Strategy.RESOURCE_ID
    if text.startswith("//") or text.startswith("("):
        # An XPath with a positional predicate is positional whatever it looks like.
        return (
            Strategy.POSITIONAL_XPATH
            if any(f"[{n}]" in text for n in range(1, 20))
            else Strategy.SEMANTIC_XPATH
        )
    if text.startswith("**/"):
        return Strategy.CLASS_CHAIN
    return Strategy.ACCESSIBILITY_ID


@dataclass
class ScreenResolution:
    """Every element GoldenFlow can locate on one screen."""

    screen_id: str
    elements: dict[str, ElementLocators] = field(default_factory=dict)
    sources: set[str] = field(default_factory=set)

    @property
    def resolved_elements(self) -> list[ElementLocators]:
        return [e for e in self.elements.values() if e.is_resolved]

    @property
    def unresolved_elements(self) -> list[ElementLocators]:
        return [e for e in self.elements.values() if not e.is_resolved]

    @property
    def is_resolved(self) -> bool:
        """A screen is resolvable if at least one element on it can be located."""
        return bool(self.resolved_elements)

    @property
    def resolution_rate(self) -> float:
        if not self.elements:
            return 0.0
        return round(len(self.resolved_elements) / len(self.elements), 3)

    @property
    def strategy_mix(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for element in self.resolved_elements:
            key = element.resolved.strategy.value
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def diagnostics(self) -> list[str]:
        if not self.elements:
            return [
                f"{self.screen_id}: no locator source reached this screen. Either "
                f"bind a Page Object in config/taxonomy.yaml, extend the crawl to "
                f"reach it (it may need auth or a deep link), or supply a session-"
                f"replay hierarchy."
            ]
        return [e.diagnose() for e in self.unresolved_elements if e.diagnose()]

    def to_dict(self) -> dict[str, Any]:
        return {
            "screen_id": self.screen_id,
            "sources": sorted(self.sources),
            "elements": len(self.elements),
            "resolved": len(self.resolved_elements),
            "resolution_rate": self.resolution_rate,
            "strategy_mix": self.strategy_mix,
            "locators": {
                role: element.resolved.to_dict()
                for role, element in self.elements.items()
                if element.is_resolved
            },
            "diagnostics": self.diagnostics(),
        }


@dataclass
class JourneyResolution:
    """Whether a Golden Journey can be turned into an executable test."""

    journey: GoldenJourney
    steps: list[tuple[str, bool]] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)

    @property
    def resolved_steps(self) -> int:
        return sum(1 for _, ok in self.steps if ok)

    @property
    def ratio(self) -> float:
        return round(self.resolved_steps / len(self.steps), 3) if self.steps else 0.0

    @property
    def executable(self) -> bool:
        """Every step must resolve. A journey with a hole is not executable.

        Partial resolution is not partial success here - Phase 5 cannot generate a
        test that navigates eight screens and guesses at the ninth.
        """
        return bool(self.steps) and self.resolved_steps == len(self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "journey_id": self.journey.journey_id,
            "name": self.journey.archetype.name,
            "score": round(self.journey.score, 1),
            "sessions": self.journey.session_count,
            "steps": len(self.steps),
            "resolved_steps": self.resolved_steps,
            "ratio": self.ratio,
            "executable": self.executable,
            "blocking_screens": self.blocking,
        }


@dataclass
class ResolutionReport:
    screens: dict[str, ScreenResolution] = field(default_factory=dict)
    journeys: list[JourneyResolution] = field(default_factory=list)

    @property
    def executable_journeys(self) -> list[JourneyResolution]:
        return [j for j in self.journeys if j.executable]

    @property
    def step_resolution_pct(self) -> float:
        """Share of all Golden Journey steps with at least one usable locator.

        The Phase 4 exit criterion.
        """
        total = sum(len(j.steps) for j in self.journeys)
        if not total:
            return 0.0
        resolved = sum(j.resolved_steps for j in self.journeys)
        return round(100.0 * resolved / total, 1)

    @property
    def executable_journey_pct(self) -> float:
        if not self.journeys:
            return 0.0
        return round(100.0 * len(self.executable_journeys) / len(self.journeys), 1)

    def blocking_screens(self) -> dict[str, int]:
        """Screens blocking the most journeys - the ranked remediation worklist."""
        counts: dict[str, int] = {}
        for journey in self.journeys:
            for screen in journey.blocking:
                counts[screen] = counts.get(screen, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def summary(self) -> dict[str, Any]:
        return {
            "screens": len(self.screens),
            "screens_resolved": sum(1 for s in self.screens.values() if s.is_resolved),
            "journeys": len(self.journeys),
            "executable_journeys": len(self.executable_journeys),
            "executable_journey_pct": self.executable_journey_pct,
            "step_resolution_pct": self.step_resolution_pct,
            "blocking_screens": self.blocking_screens(),
        }

    def format(self, limit: int = 12) -> str:
        summary = self.summary()
        lines = [
            "Locator Resolution",
            "=" * 76,
            f"  step resolution   : {summary['step_resolution_pct']}% of journey steps",
            f"  executable journeys: {summary['executable_journeys']}/"
            f"{summary['journeys']} ({summary['executable_journey_pct']}%)",
            f"  screens resolved  : {summary['screens_resolved']}/{summary['screens']}",
            "=" * 76,
        ]
        for journey in sorted(self.journeys, key=lambda j: -j.journey.score)[:limit]:
            mark = "OK  " if journey.executable else "GAP "
            lines.append(
                f"  [{mark}] {journey.journey.journey_id}  {journey.ratio:>5.0%}  "
                f"{journey.journey.archetype.name}"
            )
            if journey.blocking:
                lines.append(f"          blocked by: {', '.join(journey.blocking)}")

        blocking = self.blocking_screens()
        if blocking:
            lines += ["", "  Remediation worklist (screens blocking most journeys):"]
            for screen, count in list(blocking.items())[:8]:
                resolution = self.screens.get(screen)
                why = resolution.diagnostics()[0] if resolution and resolution.diagnostics() \
                    else "no locator source reached this screen"
                lines.append(f"    {screen} (blocks {count}): {why}")
        return "\n".join(lines)


# ------------------------------------------------------------------ resolution


def from_page_objects(suite: TestSuite, taxonomy: Taxonomy) -> dict[str, ScreenResolution]:
    """Path 1: the automation repo's own Page Objects."""
    screens: dict[str, ScreenResolution] = {}
    for page_object in suite.page_objects:
        if not page_object.is_bound:
            continue
        screen_id = page_object.screen_id
        resolution = screens.setdefault(screen_id, ScreenResolution(screen_id))
        resolution.sources.add(SOURCE_PAGE_OBJECT)
        for role, value in page_object.locators.items():
            element = resolution.elements.setdefault(
                role.lower(), ElementLocators(screen_id, role.lower())
            )
            element.candidates.append(Locator(
                strategy=_infer_strategy(value),
                value=value.lstrip("~"),
                screen_id=screen_id,
                element_role=role.lower(),
                source=SOURCE_PAGE_OBJECT,
                evidence=f"{page_object.class_name}.{role}",
            ))
    return screens


def from_dumps(
    dumps: Mapping[str, ScreenDump], *, source: str = SOURCE_CRAWL
) -> dict[str, ScreenResolution]:
    """Paths 2 and 3: crawl output or session-replay hierarchy."""
    screens: dict[str, ScreenResolution] = {}
    for screen_id, dump in dumps.items():
        resolution = ScreenResolution(screen_id)
        resolution.sources.add(source)
        roles = dump.unique_roles()
        for element in dump.interactive_elements:
            role = roles.get(id(element), element.role)
            entry = resolution.elements.setdefault(role, ElementLocators(screen_id, role))
            for locator in dump.locators_for(element):
                locator.source = source
                locator.element_role = role
                entry.candidates.append(locator)
        screens[screen_id] = resolution
    return screens


def verify_against_dumps(
    screens: Mapping[str, ScreenResolution], dumps: Mapping[str, ScreenDump]
) -> list[str]:
    """Check every claimed locator against a real hierarchy. Returns disagreements.

    Page Object locators arrive as assertions: the class says ``LINE_ITEM`` is
    ``com.acme.shop:id/cart_line_item`` and nothing has ever checked whether that
    identifies one element, two, or none. Trusting the claim is how a suite ends up
    tapping the first of two line items and calling it coverage.

    Where a dump exists for the screen, the claim becomes testable, and the real
    match count replaces the assumed one. Two failure modes surface:

    * **matches nothing** - the Page Object has drifted from the app, or the
      strategy is misdeclared (a resource ID written as an accessibility ID is the
      common one).
    * **matches several** - the locator was never unique and the test has been
      relying on document order.

    Both are reported rather than silently corrected, because either might mean the
    dump is stale rather than the Page Object wrong.
    """
    from goldenflow.phase4.healing import matches as _matches

    disagreements: list[str] = []
    for screen_id, resolution in screens.items():
        dump = dumps.get(screen_id)
        if dump is None:
            continue
        for element in resolution.elements.values():
            for locator in element.candidates:
                found = len(_matches(locator, dump))
                if found == locator.match_count:
                    continue
                if locator.source == SOURCE_PAGE_OBJECT:
                    if found == 0:
                        disagreements.append(
                            f"{screen_id}.{locator.element_role}: Page Object claims "
                            f"{locator.strategy.value}={locator.value!r} but no such "
                            f"element exists in the {dump.app_version or 'current'} "
                            f"hierarchy. The Page Object has drifted, or the strategy "
                            f"is misdeclared."
                        )
                    elif found > 1 and not _looks_like_collection(locator.element_role):
                        disagreements.append(
                            f"{screen_id}.{locator.element_role}: Page Object locator "
                            f"{locator.value!r} matches {found} elements. The test has "
                            f"been relying on document order."
                        )
                locator.match_count = found
    return disagreements


COLLECTION_HINTS = ("item", "row", "cell", "card", "entry", "option", "tile", "chip")


def _looks_like_collection(role: str) -> bool:
    """Whether a non-unique locator is plausibly intentional.

    ``LINE_ITEM`` matching both cart rows is not a defect - it is a list locator
    used with ``find_elements``. Flagging it would bury the real findings under
    noise, and a report people learn to skim protects nobody.

    This is a naming heuristic, not proof. It only suppresses the *warning*: the
    real match count is still recorded, so an ambiguous locator can never be
    proposed as a single-element target for generation.
    """
    lowered = role.lower()
    return lowered.endswith("s") or any(hint in lowered for hint in COLLECTION_HINTS)


def merge(*sources: Mapping[str, ScreenResolution]) -> dict[str, ScreenResolution]:
    """Combine resolutions from every path.

    Merged, not chosen between: two sources agreeing on a locator is stronger
    evidence than either alone, and disagreement is worth surfacing before
    generation rather than after.
    """
    merged: dict[str, ScreenResolution] = {}
    for source in sources:
        for screen_id, resolution in source.items():
            target = merged.setdefault(screen_id, ScreenResolution(screen_id))
            target.sources |= resolution.sources
            for role, element in resolution.elements.items():
                entry = target.elements.setdefault(
                    role, ElementLocators(screen_id, role)
                )
                entry.candidates.extend(element.candidates)

    for resolution in merged.values():
        _align_roles_across_sources(resolution)
        for element in resolution.elements.values():
            element.candidates = rank(element.candidates)
    return merged


def _align_roles_across_sources(resolution: ScreenResolution) -> None:
    """Fold together roles that are demonstrably the same element.

    A Page Object calls it ``CONTINUE`` and names ``~payment_continue``; the crawl
    calls it ``payment_continue`` from the same content-desc. Left apart they
    inflate the element count, split the evidence, and make a screen look less
    resolved than it is.

    Only **unique** locators are admissible as identity evidence. A shared
    accessibility ID across three payment rows proves they are similar, not that
    they are the same control - joining on it would fold wallet and cash-on-delivery
    into the card row and silently generate a test that taps the wrong one.
    """
    owner_of_value: dict[tuple[Strategy, str], str] = {}
    for role, element in resolution.elements.items():
        for locator in element.candidates:
            if locator.unique:
                owner_of_value.setdefault((locator.strategy, locator.value), role)

    remap: dict[str, str] = {}
    for role, element in resolution.elements.items():
        for locator in element.candidates:
            if not locator.unique:
                continue
            owner = owner_of_value.get((locator.strategy, locator.value))
            if owner is not None and owner != role:
                remap[role] = owner
                break

    for source_role, target_role in remap.items():
        moved = resolution.elements.pop(source_role, None)
        if moved is None:
            continue
        target = resolution.elements.get(target_role)
        if target is None:
            resolution.elements[target_role] = moved
            continue
        seen = {(c.strategy, c.value) for c in target.candidates}
        target.candidates.extend(
            c for c in moved.candidates if (c.strategy, c.value) not in seen
        )


def resolve_journeys(
    journeys: Sequence[GoldenJourney], screens: Mapping[str, ScreenResolution]
) -> ResolutionReport:
    """Decide which Golden Journeys can be generated as executable tests."""
    report = ResolutionReport(screens=dict(screens))
    for journey in journeys:
        resolution = JourneyResolution(journey=journey)
        for screen_id in journey.sequence:
            screen = screens.get(screen_id)
            if screen is None:
                # A screen no source reached is an *unresolved* screen, not an
                # absent one. Leaving it out of report.screens would report
                # "18/18 resolved" while eight screens block journeys - exactly the
                # kind of number that ends up in a status update.
                screen = ScreenResolution(screen_id)
                report.screens[screen_id] = screen
            ok = screen.is_resolved
            resolution.steps.append((screen_id, ok))
            if not ok and screen_id not in resolution.blocking:
                resolution.blocking.append(screen_id)
        report.journeys.append(resolution)
    return report


def resolve(
    journeys: Sequence[GoldenJourney],
    *,
    suite: TestSuite | None = None,
    taxonomy: Taxonomy | None = None,
    crawl_dumps: Mapping[str, ScreenDump] | None = None,
    replay_dumps: Mapping[str, ScreenDump] | None = None,
) -> ResolutionReport:
    """Run all three resolution paths and report what is executable."""
    layers: list[Mapping[str, ScreenResolution]] = []
    if suite is not None and taxonomy is not None:
        layers.append(from_page_objects(suite, taxonomy))
    if crawl_dumps:
        layers.append(from_dumps(crawl_dumps, source=SOURCE_CRAWL))
    if replay_dumps:
        layers.append(from_dumps(replay_dumps, source=SOURCE_REPLAY))
    return resolve_journeys(journeys, merge(*layers))
