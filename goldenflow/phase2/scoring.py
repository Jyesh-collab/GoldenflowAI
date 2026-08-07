"""Golden Journey scoring - deciding which journeys actually matter.

Frequency alone is a bad ranking. The most-walked path in a shopping app is usually
browse-and-leave; the path that carries the revenue is walked by a fraction of
users. A ranking that cannot tell those apart will aim the entire test suite at the
wrong target, confidently.

Four components, each normalised to [0, 1] and combined by configurable weight:

===============  =========================================================
frequency        log-scaled session volume
business_value   revenue and conversion attribution, plus taxonomy criticality
risk             crashes, ANRs and errors observed on the journey's screens
exposure         how little the journey is currently covered by tests
===============  =========================================================

**Why a weighted sum rather than a product.** The roadmap sketch multiplied the
terms. Multiplication means any zero component zeroes the whole score - a
high-traffic revenue journey with no recorded crashes would score zero on risk and
vanish from the ranking entirely. That is the opposite of what anyone wants. A
weighted sum degrades gracefully and stays interpretable, which matters because
Phase 6 retunes these weights from outcome data and a human has to be able to
follow what changed.

Every score carries its component breakdown. A ranking nobody can interrogate is a
ranking nobody will act on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from goldenflow.phase0.taxonomy import Taxonomy
from goldenflow.phase2.clustering import Archetype, ClusteringResult


@dataclass
class ScoringWeights:
    """Relative importance of each component. Retuned by Phase 6 from outcomes."""

    frequency: float = 0.30
    business_value: float = 0.30
    risk: float = 0.25
    exposure: float = 0.15

    @property
    def total(self) -> float:
        return self.frequency + self.business_value + self.risk + self.exposure

    def normalised(self) -> "ScoringWeights":
        total = self.total or 1.0
        return ScoringWeights(
            frequency=self.frequency / total,
            business_value=self.business_value / total,
            risk=self.risk / total,
            exposure=self.exposure / total,
        )


@dataclass
class BusinessValueConfig:
    """How monetary and conversion value is attributed to a journey.

    Deliberately human-owned rather than inferred. An algorithm can observe that
    ``order_confirmation`` is rare; only a person can say it is the point of the
    entire application.
    """

    screen_values: dict[str, float] = field(default_factory=dict)
    monetary_properties: tuple[str, ...] = ("order_value", "cart_value", "price")
    critical_screen_bonus: float = 0.5

    def screen_component(self, screens: set[str], taxonomy: Taxonomy | None) -> float:
        explicit = max((self.screen_values.get(s, 0.0) for s in screens), default=0.0)
        if taxonomy is None:
            return explicit
        critical = any(
            (screen := taxonomy.by_id(s)) is not None and screen.critical
            for s in screens
        )
        return min(1.0, explicit + (self.critical_screen_bonus if critical else 0.0))


@dataclass
class ScoreComponents:
    frequency: float = 0.0
    business_value: float = 0.0
    risk: float = 0.0
    exposure: float = 0.0

    def weighted_total(self, weights: ScoringWeights) -> float:
        w = weights.normalised()
        return (
            self.frequency * w.frequency
            + self.business_value * w.business_value
            + self.risk * w.risk
            + self.exposure * w.exposure
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "frequency": round(self.frequency, 3),
            "business_value": round(self.business_value, 3),
            "risk": round(self.risk, 3),
            "exposure": round(self.exposure, 3),
        }


@dataclass
class GoldenJourney:
    """A scored, ranked archetype. The primary output of Phase 2."""

    archetype: Archetype
    components: ScoreComponents
    score: float
    rank: int = 0
    session_share_pct: float = 0.0
    monetary_total: float = 0.0
    risk_events: int = 0

    @property
    def journey_id(self) -> str:
        return self.archetype.archetype_id

    @property
    def name(self) -> str:
        return self.archetype.label()

    @property
    def sequence(self) -> tuple[str, ...]:
        return self.archetype.sequence

    @property
    def session_count(self) -> int:
        return self.archetype.session_count

    def explain(self) -> str:
        """Why this journey scored what it did, in one line per component."""
        c = self.components
        return "\n".join([
            f"{self.journey_id}  score {self.score:.1f}  ({self.session_count:,} sessions, "
            f"{self.session_share_pct:.1f}% of traffic)",
            f"  path       : {' -> '.join(self.sequence)}",
            f"  frequency  : {c.frequency:.2f}  ({self.session_count:,} sessions)",
            f"  value      : {c.business_value:.2f}  "
            f"({self.monetary_total:,.0f} attributed)",
            f"  risk       : {c.risk:.2f}  ({self.risk_events} failure signals)",
            f"  exposure   : {c.exposure:.2f}  (1.0 = no test coverage known)",
        ])

    def to_dict(self) -> dict[str, Any]:
        return {
            "journey_id": self.journey_id,
            "rank": self.rank,
            "name": self.archetype.name or None,
            "score": round(self.score, 2),
            "sequence": list(self.sequence),
            "backbone": list(self.archetype.backbone()),
            "sessions": self.session_count,
            "session_share_pct": round(self.session_share_pct, 2),
            "users": self.archetype.user_count,
            "variants": self.archetype.variant_count,
            "mean_duration_s": self.archetype.mean_duration,
            "monetary_total": round(self.monetary_total, 2),
            "risk_events": self.risk_events,
            "components": self.components.as_dict(),
            "terminal_screens": self.archetype.terminal_screens,
        }


def _normalise(values: Mapping[str, float]) -> dict[str, float]:
    """Scale to [0, 1] by the observed maximum.

    Relative rather than absolute: a journey is high-risk *compared to the others in
    this app*, which is the only comparison that makes sense across a fintech app
    and a media app.
    """
    largest = max(values.values(), default=0.0)
    if largest <= 0:
        return {k: 0.0 for k in values}
    return {k: v / largest for k, v in values.items()}


def score_journeys(
    clustering: ClusteringResult,
    *,
    taxonomy: Taxonomy | None = None,
    risk_by_screen: Mapping[str, int] | None = None,
    monetary_by_archetype: Mapping[str, float] | None = None,
    coverage_by_archetype: Mapping[str, float] | None = None,
    weights: ScoringWeights | None = None,
    value_config: BusinessValueConfig | None = None,
) -> list[GoldenJourney]:
    """Score and rank archetypes.

    Args:
        clustering: Output of :func:`cluster_variants`.
        taxonomy: Used for the ``critical`` screen flag.
        risk_by_screen: Failure counts per screen, from Phase 1's risk join.
        monetary_by_archetype: Attributed monetary value per archetype.
        coverage_by_archetype: Test coverage in [0, 1]. Absent until Phase 3, so
            exposure defaults to 1.0 - every journey is treated as uncovered, which
            is the correct prior before the suite has been analysed.
        weights: Component weights. Phase 6 retunes these.
        value_config: How business value is attributed.
    """
    weights = weights or ScoringWeights()
    value_config = value_config or BusinessValueConfig()
    risk_by_screen = risk_by_screen or {}
    monetary_by_archetype = monetary_by_archetype or {}
    coverage_by_archetype = coverage_by_archetype or {}

    archetypes = clustering.archetypes
    if not archetypes:
        return []

    raw_frequency = {
        a.archetype_id: math.log1p(a.session_count) for a in archetypes
    }
    raw_risk = {
        a.archetype_id: sum(risk_by_screen.get(s, 0) for s in a.screens)
        for a in archetypes
    }
    raw_monetary = {
        a.archetype_id: monetary_by_archetype.get(a.archetype_id, 0.0)
        for a in archetypes
    }

    norm_frequency = _normalise(raw_frequency)
    norm_risk = _normalise(raw_risk)
    norm_monetary = _normalise(raw_monetary)

    journeys: list[GoldenJourney] = []
    for archetype in archetypes:
        aid = archetype.archetype_id
        value = max(
            norm_monetary[aid],
            value_config.screen_component(archetype.screens, taxonomy),
        )
        components = ScoreComponents(
            frequency=norm_frequency[aid],
            business_value=value,
            risk=norm_risk[aid],
            exposure=1.0 - coverage_by_archetype.get(aid, 0.0),
        )
        journeys.append(GoldenJourney(
            archetype=archetype,
            components=components,
            score=100.0 * components.weighted_total(weights),
            session_share_pct=clustering.coverage_pct(archetype),
            monetary_total=raw_monetary[aid],
            risk_events=raw_risk[aid],
        ))

    journeys.sort(key=lambda j: (-j.score, j.journey_id))
    for rank, journey in enumerate(journeys, start=1):
        journey.rank = rank
    return journeys


def monetary_by_archetype(
    clustering: ClusteringResult,
    traces_by_sequence: Mapping[tuple[str, ...], float],
) -> dict[str, float]:
    """Roll per-sequence monetary totals up to archetypes."""
    totals: dict[str, float] = {}
    for archetype in clustering.archetypes:
        totals[archetype.archetype_id] = sum(
            traces_by_sequence.get(member.sequence, 0.0)
            for member in archetype.members
        )
    return totals


def top_journeys(journeys: Sequence[GoldenJourney], limit: int = 20) -> list[GoldenJourney]:
    return list(journeys[:limit])
