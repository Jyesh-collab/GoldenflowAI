"""The journey graph - screens as nodes, observed transitions as weighted edges.

Phase 3 projects every Appium test as a path over this same node set, which turns
coverage analysis into a graph diff rather than a pile of heuristics. That only
works if both sides share one vocabulary, which is why the graph is built from the
Phase 0 taxonomy's canonical screen IDs and nothing else.

Neo4j is the deployment target. This module builds the graph in memory, exports
idempotent Cypher, and - if the driver is installed - can load it directly. The
in-memory form is not a stand-in: Phase 3 needs to run the uncovered-edge query in
tests without a database, so the same query exists in both forms deliberately.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from goldenflow.phase0.taxonomy import Taxonomy
from goldenflow.phase2.mining import DirectlyFollowsGraph, Edge
from goldenflow.phase2.scoring import GoldenJourney


@dataclass
class ScreenNode:
    screen_id: str
    display_name: str = ""
    domain: str = ""
    critical: bool = False
    sensitivity: str = "none"
    visits: int = 0
    entry_count: int = 0
    exit_count: int = 0

    @property
    def is_entry_point(self) -> bool:
        return self.entry_count > 0

    @property
    def exit_rate(self) -> float:
        return round(self.exit_count / self.visits, 3) if self.visits else 0.0


@dataclass
class TransitionEdge:
    source: str
    target: str
    weight: int
    probability: float = 0.0

    @property
    def key(self) -> Edge:
        return Edge(self.source, self.target)


@dataclass
class JourneyGraph:
    screens: dict[str, ScreenNode] = field(default_factory=dict)
    transitions: list[TransitionEdge] = field(default_factory=list)
    journeys: list[GoldenJourney] = field(default_factory=list)
    taxonomy_fingerprint: str = ""

    # ------------------------------------------------------------- queries

    def transition(self, source: str, target: str) -> TransitionEdge | None:
        return next(
            (t for t in self.transitions
             if t.source == source and t.target == target),
            None,
        )

    def uncovered_transitions(
        self, covered: Iterable[tuple[str, str]], *, min_weight: int = 1
    ) -> list[TransitionEdge]:
        """Transitions users walk that no supplied test path covers.

        This is the Phase 3 coverage gap, expressed here so it can be computed and
        tested without a database. The Cypher equivalent is in :meth:`gap_query`.
        """
        covered_set = {(s, t) for s, t in covered}
        gaps = [
            t for t in self.transitions
            if t.weight >= min_weight and (t.source, t.target) not in covered_set
        ]
        gaps.sort(key=lambda t: -t.weight)
        return gaps

    def unreachable_transitions(
        self, covered: Iterable[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        """Test transitions with no production counterpart - obsolete candidates.

        A signal, never a verdict. Phase 5 requires a second corroborating signal
        before proposing any deletion, and the protected registry overrides both.
        """
        observed = {(t.source, t.target) for t in self.transitions}
        return sorted({(s, t) for s, t in covered} - observed)

    def critical_screens_without_traffic(self) -> list[str]:
        return sorted(
            s.screen_id for s in self.screens.values() if s.critical and not s.visits
        )

    # -------------------------------------------------------------- export

    def to_dict(self) -> dict[str, Any]:
        return {
            "taxonomy_fingerprint": self.taxonomy_fingerprint,
            "screens": [
                {
                    "screen_id": s.screen_id, "display_name": s.display_name,
                    "domain": s.domain, "critical": s.critical,
                    "sensitivity": s.sensitivity, "visits": s.visits,
                    "entry_count": s.entry_count, "exit_count": s.exit_count,
                    "exit_rate": s.exit_rate,
                }
                for s in sorted(self.screens.values(), key=lambda s: -s.visits)
            ],
            "transitions": [
                {"source": t.source, "target": t.target,
                 "weight": t.weight, "probability": round(t.probability, 4)}
                for t in sorted(self.transitions, key=lambda t: -t.weight)
            ],
            "journeys": [j.to_dict() for j in self.journeys],
        }

    def write_json(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return target

    def to_cypher(self, *, limit_journeys: int | None = None) -> str:
        """Idempotent Cypher. Safe to re-run as the graph is refreshed each cycle."""
        lines = [
            "// GoldenFlow journey graph",
            f"// taxonomy fingerprint: {self.taxonomy_fingerprint}",
            "CREATE CONSTRAINT screen_id IF NOT EXISTS",
            "  FOR (s:Screen) REQUIRE s.screen_id IS UNIQUE;",
            "CREATE CONSTRAINT journey_id IF NOT EXISTS",
            "  FOR (j:Journey) REQUIRE j.journey_id IS UNIQUE;",
            "",
        ]

        for screen in sorted(self.screens.values(), key=lambda s: s.screen_id):
            lines.append(
                f"MERGE (s:Screen {{screen_id: {_q(screen.screen_id)}}}) SET "
                f"s.display_name = {_q(screen.display_name)}, "
                f"s.domain = {_q(screen.domain)}, "
                f"s.critical = {str(screen.critical).lower()}, "
                f"s.sensitivity = {_q(screen.sensitivity)}, "
                f"s.visits = {screen.visits}, "
                f"s.entry_count = {screen.entry_count}, "
                f"s.exit_count = {screen.exit_count};"
            )

        lines.append("")
        for edge in sorted(self.transitions, key=lambda t: -t.weight):
            lines.append(
                f"MATCH (a:Screen {{screen_id: {_q(edge.source)}}}), "
                f"(b:Screen {{screen_id: {_q(edge.target)}}}) "
                f"MERGE (a)-[r:FOLLOWS]->(b) SET "
                f"r.weight = {edge.weight}, r.probability = {edge.probability:.4f};"
            )

        lines.append("")
        journeys = self.journeys[:limit_journeys] if limit_journeys else self.journeys
        for journey in journeys:
            lines.append(
                f"MERGE (j:Journey {{journey_id: {_q(journey.journey_id)}}}) SET "
                f"j.name = {_q(journey.archetype.name or journey.journey_id)}, "
                f"j.score = {journey.score:.2f}, "
                f"j.sessions = {journey.session_count}, "
                f"j.rank = {journey.rank}, "
                f"j.sequence = {json.dumps(list(journey.sequence))};"
            )
            for position, screen_id in enumerate(journey.sequence):
                lines.append(
                    f"MATCH (j:Journey {{journey_id: {_q(journey.journey_id)}}}), "
                    f"(s:Screen {{screen_id: {_q(screen_id)}}}) "
                    f"MERGE (j)-[st:STEP {{position: {position}}}]->(s);"
                )
        return "\n".join(lines) + "\n"

    @staticmethod
    def gap_query(min_weight: int = 50) -> str:
        """The Phase 3 coverage-gap query, in Cypher.

        Kept beside the in-memory implementation so the two cannot drift apart
        unnoticed.
        """
        return f"""
// Coverage gaps: transitions users walk that no test path covers.
MATCH (a:Screen)-[r:FOLLOWS]->(b:Screen)
WHERE r.weight >= {min_weight}
  AND NOT EXISTS {{
    MATCH (t:Test)-[:COVERS]->(a)
    MATCH (t)-[:COVERS]->(b)
  }}
RETURN a.screen_id AS from_screen,
       b.screen_id AS to_screen,
       r.weight    AS sessions
ORDER BY r.weight DESC;
""".strip()

    def load_into_neo4j(self, uri: str, user: str, password: str) -> int:
        """Execute the Cypher against a live Neo4j. Returns statements run.

        Raises:
            ImportError: if the neo4j driver is not installed.
        """
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise ImportError(
                "neo4j driver required: pip install 'goldenflow[graph]'. "
                "Use to_cypher() or write_json() to export without it."
            ) from exc

        statements = [s.strip() for s in self.to_cypher().split(";") if s.strip()
                      and not s.strip().startswith("//")]
        driver = GraphDatabase.driver(uri, auth=(user, password))
        try:
            with driver.session() as session:
                for statement in statements:
                    session.run(statement)
        finally:
            driver.close()
        return len(statements)


def _q(value: str) -> str:
    """Quote a Cypher string literal."""
    return json.dumps(value)


def build_journey_graph(
    dfg: DirectlyFollowsGraph,
    journeys: Sequence[GoldenJourney],
    *,
    taxonomy: Taxonomy | None = None,
) -> JourneyGraph:
    """Assemble the graph from mining output and the taxonomy."""
    graph = JourneyGraph(
        journeys=list(journeys),
        taxonomy_fingerprint=taxonomy.fingerprint() if taxonomy else "",
    )

    for screen_id, visits in dfg.node_counts.items():
        definition = taxonomy.by_id(screen_id) if taxonomy else None
        graph.screens[screen_id] = ScreenNode(
            screen_id=screen_id,
            display_name=definition.display_name if definition else screen_id,
            domain=definition.domain if definition else "",
            critical=bool(definition.critical) if definition else False,
            sensitivity=definition.sensitivity.value if definition else "none",
            visits=visits,
            entry_count=dfg.start_counts.get(screen_id, 0),
            exit_count=dfg.end_counts.get(screen_id, 0),
        )

    # Screens the taxonomy declares but production never showed. Their absence is
    # itself a finding - a critical screen with no traffic is either dead code or
    # broken instrumentation, and both matter.
    if taxonomy:
        for definition in taxonomy.screens:
            graph.screens.setdefault(definition.screen_id, ScreenNode(
                screen_id=definition.screen_id,
                display_name=definition.display_name,
                domain=definition.domain,
                critical=definition.critical,
                sensitivity=definition.sensitivity.value,
            ))

    for edge, weight in dfg.edge_counts.items():
        graph.transitions.append(TransitionEdge(
            source=edge.source,
            target=edge.target,
            weight=weight,
            probability=dfg.transition_probability(edge.source, edge.target),
        ))
    graph.transitions.sort(key=lambda t: -t.weight)
    return graph
