"""Weight auto-tuning - the part that makes "self-evolving" a measurement.

Phase 2 scores journeys by a weighted sum of frequency, business value, risk and
exposure. Those weights started as a guess. This module replaces the guess with
evidence: if risk-weighted journeys turn out to predict escaped defects better than
frequency-weighted ones, risk earns more weight.

Three rules keep this from becoming an unaccountable feedback loop:

**Bounded movement.** No single tuning round may move a weight by more than
:data:`MAX_STEP`. A model that can swing its own priorities arbitrarily between runs
produces a ranking nobody can plan against, and reviewers stop trusting it.

**Floors.** No component may fall below :data:`MIN_WEIGHT`. Driving a term to zero
makes the system permanently blind to it, and the evidence that would have corrected
that is exactly the evidence it can no longer see.

**Every change is explained and reversible.** :class:`WeightProposal` carries the
before, the after, the evidence and a plain-language reason, and nothing applies
itself - Phase 6 proposes, a human accepts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from goldenflow.phase2.scoring import ScoringWeights
from goldenflow.phase6.outcomes import DefectOrigin, OutcomeReport

MAX_STEP = 0.05
"""Largest single-round change to any one weight."""

MIN_WEIGHT = 0.05
"""Floor. A component driven to zero can never earn its way back."""

MIN_EVIDENCE = 10
"""Minimum attributed defects before tuning is allowed to move anything at all.

Below this, apparent signal is sampling noise. Tuning on six defects produces
confident nonsense - the same failure mode the PSI calibration in Phase 2 exposed.
"""


@dataclass
class ComponentEvidence:
    """How well one scoring component predicted trouble."""

    component: str
    high_score_defects: int = 0
    """Defects on journeys this component ranked highly."""

    low_score_defects: int = 0
    """Defects on journeys this component ranked low - the misses."""

    @property
    def total(self) -> int:
        return self.high_score_defects + self.low_score_defects

    @property
    def hit_rate(self) -> float | None:
        """Share of defects this component saw coming."""
        return round(self.high_score_defects / self.total, 3) if self.total else None


@dataclass
class WeightProposal:
    """A proposed weight change, with its justification."""

    before: ScoringWeights
    after: ScoringWeights
    evidence: list[ComponentEvidence] = field(default_factory=list)
    reason: str = ""
    applied: bool = False
    proposed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def deltas(self) -> dict[str, float]:
        return {
            "frequency": round(self.after.frequency - self.before.frequency, 4),
            "business_value": round(
                self.after.business_value - self.before.business_value, 4),
            "risk": round(self.after.risk - self.before.risk, 4),
            "exposure": round(self.after.exposure - self.before.exposure, 4),
        }

    @property
    def is_noop(self) -> bool:
        return all(abs(d) < 1e-9 for d in self.deltas.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposed_at": self.proposed_at.isoformat(),
            "before": vars(self.before),
            "after": vars(self.after),
            "deltas": self.deltas,
            "reason": self.reason,
            "applied": self.applied,
            "evidence": [
                {"component": e.component, "hit_rate": e.hit_rate,
                 "defects_seen": e.high_score_defects, "defects_missed": e.low_score_defects}
                for e in self.evidence
            ],
        }

    def format(self) -> str:
        if self.is_noop:
            return f"No weight change proposed.\n  {self.reason}"
        lines = ["Weight proposal", "=" * 66, f"  {self.reason}", ""]
        for name, delta in self.deltas.items():
            before = getattr(self.before, name)
            after = getattr(self.after, name)
            arrow = "up" if delta > 0 else ("down" if delta < 0 else "  ")
            lines.append(f"  {name:<16} {before:.3f} -> {after:.3f}  "
                         f"({delta:+.3f} {arrow})")
        if self.evidence:
            lines += ["", "  Evidence:"]
            for item in self.evidence:
                rate = "n/a" if item.hit_rate is None else f"{item.hit_rate:.1%}"
                lines.append(f"    {item.component:<16} hit rate {rate:>6}  "
                             f"({item.high_score_defects} seen, "
                             f"{item.low_score_defects} missed)")
        lines += ["", "  Nothing is applied automatically. Review and accept."]
        return "\n".join(lines)


def gather_evidence(
    report: OutcomeReport,
    component_scores: dict[str, dict[str, float]],
    *,
    high_threshold: float = 0.5,
) -> list[ComponentEvidence]:
    """Score each component on whether it ranked the defect-producing journeys highly.

    Args:
        report: Attributed outcomes.
        component_scores: ``{journey_id: {component: normalised_score}}`` from Phase 2.
        high_threshold: Above this a component is treated as having flagged the journey.
    """
    components = ("frequency", "business_value", "risk", "exposure")
    evidence = {c: ComponentEvidence(component=c) for c in components}

    for defect in report.in_scope_defects:
        scores = component_scores.get(defect.journey_id or "")
        if not scores:
            continue
        for component in components:
            if scores.get(component, 0.0) >= high_threshold:
                evidence[component].high_score_defects += 1
            else:
                evidence[component].low_score_defects += 1
    return list(evidence.values())


def propose_weights(
    current: ScoringWeights,
    report: OutcomeReport,
    evidence: Sequence[ComponentEvidence],
) -> WeightProposal:
    """Propose a bounded weight adjustment from measured hit rates.

    Components that saw defects coming gain weight; components that missed lose it.
    Movement is capped and floored, and refused outright below :data:`MIN_EVIDENCE`.
    """
    proposal = WeightProposal(before=current, after=current, evidence=list(evidence))

    attributed = len(report.in_scope_defects)
    if attributed < MIN_EVIDENCE:
        proposal.reason = (
            f"Insufficient evidence: {attributed} attributed defect(s), "
            f"{MIN_EVIDENCE} required. Tuning on a handful of incidents produces "
            f"confident nonsense, so nothing moves."
        )
        return proposal

    rated = [e for e in evidence if e.hit_rate is not None]
    if not rated:
        proposal.reason = "No component had any scored journeys to judge."
        return proposal

    mean_rate = sum(e.hit_rate for e in rated) / len(rated)
    updated = ScoringWeights(**vars(current))

    for item in rated:
        # Move toward the components that predicted better than average.
        adjustment = max(-MAX_STEP, min(MAX_STEP, (item.hit_rate - mean_rate) * 0.2))
        new_value = max(MIN_WEIGHT, getattr(updated, item.component) + adjustment)
        setattr(updated, item.component, round(new_value, 4))

    proposal.after = updated
    best = max(rated, key=lambda e: e.hit_rate)
    worst = min(rated, key=lambda e: e.hit_rate)
    proposal.reason = (
        f"Tuned on {attributed} attributed defect(s). "
        f"{best.component} predicted best ({best.hit_rate:.0%} hit rate), "
        f"{worst.component} worst ({worst.hit_rate:.0%}). "
        f"Movement capped at {MAX_STEP} per round, floor {MIN_WEIGHT}."
    )
    return proposal


def apply_proposal(proposal: WeightProposal) -> ScoringWeights:
    """Accept a proposal. Explicit, because nothing here tunes itself."""
    proposal.applied = True
    return proposal.after
