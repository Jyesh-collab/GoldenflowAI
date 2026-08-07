"""Self-healing - detecting broken locators and proposing repairs.

When a build ships, some locators stop matching. The naive fix is to swap in
whatever still finds the element, which is how suites end up full of positional
XPaths that pass today and break next Tuesday.

Three rules constrain what may be proposed:

**Never silently downgrade.** A repair that moves from a resource ID to a positional
XPath is flagged as a downgrade and requires human review, even though it "works".
Silent downgrades are how a suite rots while its pass rate stays green.

**Never propose an ambiguous locator.** If the repair matches three elements it is
not a repair.

**Report confidence, and let the caller set the bar.** Every proposal carries the
element-similarity score behind it, so precision can be measured on a held-out set
rather than asserted.

A broken locator with no acceptable repair is reported as such. That is a worse
outcome than a repair and a better outcome than a bad one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from goldenflow.phase4.fingerprint import element_similarity
from goldenflow.phase4.hierarchy import ScreenDump, UiElement
from goldenflow.phase4.locators import Locator, Strategy, rank

MIN_REPAIR_CONFIDENCE = 0.45
"""Below this, no repair is proposed. Chosen so that matching on element type and
clickability alone (0.20) can never carry a proposal - a repair needs a semantic
signal, not just a shape."""


def matches(locator: Locator, dump: ScreenDump) -> list[UiElement]:
    """Every element in ``dump`` that ``locator`` would find."""
    found: list[UiElement] = []
    for element in dump.elements:
        if locator.strategy is Strategy.ACCESSIBILITY_ID:
            if element.accessibility_id == locator.value:
                found.append(element)
        elif locator.strategy is Strategy.RESOURCE_ID:
            if element.resource_id == locator.value or (
                element.short_resource_id == locator.value.split("/")[-1]
                and element.resource_id
            ):
                found.append(element)
        elif locator.strategy in (Strategy.SEMANTIC_XPATH, Strategy.POSITIONAL_XPATH):
            positional = locator.strategy is Strategy.POSITIONAL_XPATH
            if element.xpath(positional=positional) == locator.value:
                found.append(element)
    return found


@dataclass
class Repair:
    """A proposed replacement for a broken locator."""

    screen_id: str
    element_role: str
    broken: Locator
    replacement: Locator | None
    confidence: float
    reason: str

    @property
    def repairable(self) -> bool:
        return self.replacement is not None

    @property
    def is_downgrade(self) -> bool:
        """Whether the repair trades away stability."""
        if self.replacement is None:
            return False
        return self.replacement.strategy.stability < self.broken.strategy.stability

    @property
    def requires_review(self) -> bool:
        """Downgrades never auto-apply, however confident the match."""
        return not self.repairable or self.is_downgrade

    def to_dict(self) -> dict[str, Any]:
        return {
            "screen_id": self.screen_id,
            "element_role": self.element_role,
            "broken": self.broken.to_dict(),
            "replacement": self.replacement.to_dict() if self.replacement else None,
            "confidence": self.confidence,
            "repairable": self.repairable,
            "is_downgrade": self.is_downgrade,
            "requires_review": self.requires_review,
            "reason": self.reason,
        }

    def format(self) -> str:
        if not self.repairable:
            return (f"  [BROKEN ] {self.screen_id}.{self.element_role}\n"
                    f"            was {self.broken.strategy.value}={self.broken.value}\n"
                    f"            {self.reason}")
        flag = "REVIEW " if self.requires_review else "REPAIR "
        return (
            f"  [{flag}] {self.screen_id}.{self.element_role}  "
            f"(confidence {self.confidence:.2f})\n"
            f"            {self.broken.strategy.value}={self.broken.value}\n"
            f"         -> {self.replacement.strategy.value}={self.replacement.value}"
            + ("   [DOWNGRADE - stability drops]" if self.is_downgrade else "")
        )


@dataclass
class HealingReport:
    repairs: list[Repair] = field(default_factory=list)
    checked: int = 0
    baseline_version: str = ""
    current_version: str = ""

    @property
    def broken(self) -> list[Repair]:
        return [r for r in self.repairs if not r.repairable]

    @property
    def auto_applicable(self) -> list[Repair]:
        return [r for r in self.repairs if r.repairable and not r.requires_review]

    @property
    def needs_review(self) -> list[Repair]:
        return [r for r in self.repairs if r.repairable and r.requires_review]

    @property
    def survival_pct(self) -> float:
        """Share of locators that still work unchanged. The stability ranking's
        empirical justification, measured rather than asserted."""
        if not self.checked:
            return 0.0
        return round(100.0 * (self.checked - len(self.repairs)) / self.checked, 1)

    def survival_by_strategy(self) -> dict[str, str]:
        """Per-strategy survival - the evidence for how locators should be ranked."""
        broken_counts: dict[str, int] = {}
        for repair in self.repairs:
            key = repair.broken.strategy.value
            broken_counts[key] = broken_counts.get(key, 0) + 1
        return {k: f"{v} broken" for k, v in
                sorted(broken_counts.items(), key=lambda kv: -kv[1])}

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_version": self.baseline_version,
            "current_version": self.current_version,
            "checked": self.checked,
            "survival_pct": self.survival_pct,
            "broken": len(self.broken),
            "auto_applicable": len(self.auto_applicable),
            "needs_review": len(self.needs_review),
            "broken_by_strategy": self.survival_by_strategy(),
            "repairs": [r.to_dict() for r in self.repairs],
        }

    def format(self, limit: int = 20) -> str:
        lines = [
            f"Locator healing: {self.baseline_version} -> {self.current_version}",
            "=" * 76,
            f"  checked          : {self.checked} locators",
            f"  survived intact  : {self.survival_pct}%",
            f"  auto-applicable  : {len(self.auto_applicable)}",
            f"  needs review     : {len(self.needs_review)} "
            f"(downgrades never auto-apply)",
            f"  unrepairable     : {len(self.broken)}",
        ]
        if self.survival_by_strategy():
            lines.append(f"  broken by strategy: {self.survival_by_strategy()}")
        lines.append("=" * 76)
        lines += [r.format() for r in self.repairs[:limit]]
        if len(self.repairs) > limit:
            lines.append(f"  ... and {len(self.repairs) - limit} more")
        return "\n".join(lines)


def propose_repair(
    locator: Locator, baseline: ScreenDump, current: ScreenDump
) -> Repair | None:
    """Check one locator against a new build and propose a repair if it broke.

    Returns None when the locator still resolves uniquely - no news is the common
    case and should not generate noise.
    """
    still_matching = matches(locator, current)
    if len(still_matching) == 1:
        return None

    if len(still_matching) > 1:
        return Repair(
            screen_id=locator.screen_id, element_role=locator.element_role,
            broken=locator, replacement=None, confidence=0.0,
            reason=f"now matches {len(still_matching)} elements; it identified one "
                   f"in the baseline. An ambiguous locator is not a locator.",
        )

    original = next(iter(matches(locator, baseline)), None)
    if original is None:
        return Repair(
            screen_id=locator.screen_id, element_role=locator.element_role,
            broken=locator, replacement=None, confidence=0.0,
            reason="did not match in the baseline either; cannot infer intent, so "
                   "no repair is proposed.",
        )

    ranked = sorted(
        ((element, element_similarity(original, element)) for element in current.elements),
        key=lambda pair: -pair[1],
    )
    if not ranked or ranked[0][1] < MIN_REPAIR_CONFIDENCE:
        score = ranked[0][1] if ranked else 0.0
        return Repair(
            screen_id=locator.screen_id, element_role=locator.element_role,
            broken=locator, replacement=None, confidence=score,
            reason=f"no element in the new build resembles the original "
                   f"(best similarity {score:.2f}). The control was probably removed.",
        )

    target, confidence = ranked[0]
    candidates = [c for c in rank(current.locators_for(target)) if c.usable]
    if not candidates:
        return Repair(
            screen_id=locator.screen_id, element_role=locator.element_role,
            broken=locator, replacement=None, confidence=confidence,
            reason="found the element but it has no usable locator - every "
                   "candidate is ambiguous or below the stability floor.",
        )

    replacement = candidates[0]
    replacement.element_role = locator.element_role
    return Repair(
        screen_id=locator.screen_id, element_role=locator.element_role,
        broken=locator, replacement=replacement, confidence=confidence,
        reason=f"matched the original element with similarity {confidence:.2f}",
    )


def heal(
    locators: Sequence[Locator],
    baseline: Mapping[str, ScreenDump],
    current: Mapping[str, ScreenDump],
) -> HealingReport:
    """Check a set of locators against a new build."""
    report = HealingReport(
        baseline_version=next(iter(baseline.values())).app_version if baseline else "",
        current_version=next(iter(current.values())).app_version if current else "",
    )
    for locator in locators:
        base_dump = baseline.get(locator.screen_id)
        curr_dump = current.get(locator.screen_id)
        if base_dump is None or curr_dump is None:
            continue
        report.checked += 1
        repair = propose_repair(locator, base_dump, curr_dump)
        if repair is not None:
            report.repairs.append(repair)

    report.repairs.sort(key=lambda r: (r.repairable, -r.confidence))
    return report
