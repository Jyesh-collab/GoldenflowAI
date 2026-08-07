"""Phase 2 - Journey Intelligence Engine (Agent 1).

The research core. Production telemetry in, Golden Journeys out.

This is process mining applied to mobile QE, and the mapping is exact::

    user journey    ->  trace / event log
    Golden Journey  ->  discovered process model
    Journey Drift   ->  concept drift
    coverage gap    ->  conformance checking   (Phase 3)

**Deterministic algorithms do the analysis; the LLM only interprets.** Every number
in a journey report comes from the pipeline below. The model contributes the
sentence around it, and can be swapped out or removed without changing a single
figure. That split is what makes the output auditable.

Pipeline::

    events -> sessionize -> mine (DFG + variants) -> cluster -> score -> name
                                                        |
                                                        +-> drift (vs. baseline)
                                                        +-> graph (Neo4j)
"""

from goldenflow.phase2.clustering import (
    Archetype,
    ClusteringResult,
    cluster_variants,
    sequence_similarity,
)
from goldenflow.phase2.drift import (
    DriftReport,
    DriftThresholds,
    detect_drift,
    jensen_shannon_divergence,
    population_stability_index,
)
from goldenflow.phase2.graph import JourneyGraph, build_journey_graph
from goldenflow.phase2.mining import (
    DirectlyFollowsGraph,
    Edge,
    Variant,
    build_dfg,
    coverage_curve,
    entropy,
    extract_variants,
    pm4py_available,
    variants_for_coverage,
)
from goldenflow.phase2.naming import (
    JourneyDescription,
    LlmJourneyNamer,
    RuleBasedNamer,
    name_journeys,
)
from goldenflow.phase2.scoring import (
    BusinessValueConfig,
    GoldenJourney,
    ScoringWeights,
    score_journeys,
)
from goldenflow.phase2.sessionize import Trace, sessionize, sessionize_store

__all__ = [
    "Archetype", "ClusteringResult", "cluster_variants", "sequence_similarity",
    "DriftReport", "DriftThresholds", "detect_drift",
    "jensen_shannon_divergence", "population_stability_index",
    "JourneyGraph", "build_journey_graph",
    "DirectlyFollowsGraph", "Edge", "Variant", "build_dfg", "coverage_curve",
    "entropy", "extract_variants", "pm4py_available", "variants_for_coverage",
    "JourneyDescription", "LlmJourneyNamer", "RuleBasedNamer", "name_journeys",
    "BusinessValueConfig", "GoldenJourney", "ScoringWeights", "score_journeys",
    "Trace", "sessionize", "sessionize_store",
]
