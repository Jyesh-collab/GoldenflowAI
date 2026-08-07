"""Process mining - discovering the process users actually follow.

This is where "Journey Intelligence" stops being a slogan and becomes a named,
citable technique. The problem is a solved one in the process mining literature:

    user journey        ->  trace / event log
    Golden Journey      ->  discovered process model
    Journey Drift       ->  concept drift
    coverage gap        ->  conformance checking

The core structures - the directly-follows graph and the variant set - are
implemented here directly rather than delegated. They are simple enough that a
dependency would obscure more than it saves, and Phase 3's gap analysis needs to
reason about the edges directly.

PM4Py is used for what it is genuinely good at: Inductive Miner process discovery
and alignment-based conformance checking. That bridge is optional - :func:`discover_process_model`
degrades to a clear error if PM4Py is absent, and nothing else in the module depends
on it.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from goldenflow.phase2.sessionize import Trace

START = "__start__"
END = "__end__"


@dataclass(frozen=True)
class Edge:
    source: str
    target: str

    def __str__(self) -> str:
        return f"{self.source} -> {self.target}"


@dataclass
class DirectlyFollowsGraph:
    """The workhorse structure: which screen follows which, and how often.

    Phase 3 computes coverage gaps as a diff over these edges, so this is the
    shared vocabulary between production reality and the test suite.
    """

    node_counts: dict[str, int] = field(default_factory=dict)
    edge_counts: dict[Edge, int] = field(default_factory=dict)
    start_counts: dict[str, int] = field(default_factory=dict)
    end_counts: dict[str, int] = field(default_factory=dict)
    trace_count: int = 0

    @property
    def nodes(self) -> set[str]:
        return set(self.node_counts)

    @property
    def edges(self) -> set[Edge]:
        return set(self.edge_counts)

    def edge_weight(self, source: str, target: str) -> int:
        return self.edge_counts.get(Edge(source, target), 0)

    def outgoing(self, screen: str) -> dict[str, int]:
        return {
            e.target: c for e, c in self.edge_counts.items() if e.source == screen
        }

    def transition_probability(self, source: str, target: str) -> float:
        """P(next = target | current = source). The Markov view of the journey."""
        total = sum(self.outgoing(source).values())
        return self.edge_weight(source, target) / total if total else 0.0

    def top_edges(self, limit: int = 20) -> list[tuple[Edge, int]]:
        return sorted(self.edge_counts.items(), key=lambda kv: -kv[1])[:limit]

    def filter_noise(self, min_weight: int) -> "DirectlyFollowsGraph":
        """Drop rare edges.

        Real DFGs have a long tail of one-off transitions from misfired events and
        genuinely odd behaviour. Keeping them makes the graph unreadable; dropping
        them too aggressively erases exactly the rare-but-critical paths the
        protected registry exists to defend. Default thresholds elsewhere are
        deliberately low for that reason.
        """
        kept = {e: c for e, c in self.edge_counts.items() if c >= min_weight}
        live = {e.source for e in kept} | {e.target for e in kept}
        return DirectlyFollowsGraph(
            node_counts={n: c for n, c in self.node_counts.items() if n in live},
            edge_counts=kept,
            start_counts=dict(self.start_counts),
            end_counts=dict(self.end_counts),
            trace_count=self.trace_count,
        )

    def summary(self) -> dict[str, Any]:
        return {
            "traces": self.trace_count,
            "screens": len(self.node_counts),
            "transitions": len(self.edge_counts),
            "entry_points": len(self.start_counts),
            "exit_points": len(self.end_counts),
            "top_transitions": [
                {"edge": str(e), "count": c} for e, c in self.top_edges(5)
            ],
        }


def build_dfg(traces: Iterable[Trace]) -> DirectlyFollowsGraph:
    """Build the directly-follows graph from traces."""
    dfg = DirectlyFollowsGraph()
    nodes: Counter[str] = Counter()
    edges: Counter[Edge] = Counter()
    starts: Counter[str] = Counter()
    ends: Counter[str] = Counter()
    count = 0

    for trace in traces:
        sequence = trace.sequence
        if len(sequence) < 2:
            continue
        count += 1
        nodes.update(sequence)
        starts[sequence[0]] += 1
        ends[sequence[-1]] += 1
        edges.update(Edge(a, b) for a, b in zip(sequence, sequence[1:]))

    dfg.node_counts = dict(nodes)
    dfg.edge_counts = dict(edges)
    dfg.start_counts = dict(starts)
    dfg.end_counts = dict(ends)
    dfg.trace_count = count
    return dfg


# ------------------------------------------------------------------- variants


@dataclass
class Variant:
    """A distinct screen sequence, and how many sessions walked it."""

    sequence: tuple[str, ...]
    count: int
    user_count: int = 0
    total_duration: float = 0.0
    example_sessions: list[str] = field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.sequence)

    @property
    def mean_duration(self) -> float:
        return round(self.total_duration / self.count, 1) if self.count else 0.0

    @property
    def terminal_screen(self) -> str:
        return self.sequence[-1]

    def label(self, limit: int = 5) -> str:
        if self.length <= limit:
            return " -> ".join(self.sequence)
        head = " -> ".join(self.sequence[: limit - 1])
        return f"{head} -> ... -> {self.sequence[-1]}"


def extract_variants(traces: Iterable[Trace], *, max_examples: int = 3) -> list[Variant]:
    """Collapse traces into distinct sequences, ordered by frequency."""
    grouped: dict[tuple[str, ...], list[Trace]] = defaultdict(list)
    for trace in traces:
        if trace.length >= 2:
            grouped[trace.sequence].append(trace)

    variants = [
        Variant(
            sequence=sequence,
            count=len(group),
            user_count=len({t.user_id for t in group if t.user_id}),
            total_duration=sum(t.duration_seconds for t in group),
            example_sessions=[t.session_id for t in group[:max_examples]],
        )
        for sequence, group in grouped.items()
    ]
    variants.sort(key=lambda v: (-v.count, v.sequence))
    return variants


def coverage_curve(variants: Sequence[Variant]) -> list[tuple[int, float]]:
    """Cumulative share of sessions explained by the top-N variants.

    The shape of this curve decides whether journey mining is viable for an app at
    all. A curve that reaches 95% in 40 variants is a tractable product; one that
    needs 4,000 means the app has no stable journeys to speak of, and the honest
    answer is to say so rather than to ship an arbitrary top-20 list.
    """
    total = sum(v.count for v in variants)
    if not total:
        return []
    curve, cumulative = [], 0
    for index, variant in enumerate(variants, start=1):
        cumulative += variant.count
        curve.append((index, round(100.0 * cumulative / total, 2)))
    return curve


def variants_for_coverage(variants: Sequence[Variant], target_pct: float) -> int:
    """How many variants are needed to explain ``target_pct`` of sessions."""
    for index, pct in coverage_curve(variants):
        if pct >= target_pct:
            return index
    return len(variants)


def entropy(variants: Sequence[Variant]) -> float:
    """Shannon entropy of the variant distribution, in bits.

    A low value means behaviour concentrates on a few paths and testing them is
    high-leverage. A high value means it does not, and no top-N list will be
    representative however it is scored.
    """
    total = sum(v.count for v in variants)
    if not total:
        return 0.0
    return round(
        -sum(
            (v.count / total) * math.log2(v.count / total)
            for v in variants
            if v.count
        ),
        3,
    )


def dropoff_points(traces: Iterable[Trace]) -> dict[str, int]:
    """Where journeys end, ranked. The raw material for funnel analysis."""
    counts: Counter[str] = Counter(t.terminal_screen for t in traces if t.length >= 2)
    return dict(counts.most_common())


# --------------------------------------------------------------- PM4Py bridge


def to_pm4py_log(traces: Iterable[Trace]):
    """Convert traces to a PM4Py-compatible DataFrame event log.

    Raises:
        ImportError: if pandas is unavailable.
    """
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError(
            "pandas is required for the PM4Py bridge: pip install 'goldenflow[mining]'"
        ) from exc

    rows = [
        {
            "case:concept:name": trace.session_id,
            "concept:name": step.screen_id,
            "time:timestamp": step.entered_at,
        }
        for trace in traces
        for step in trace.steps
    ]
    return pd.DataFrame(rows)


def discover_process_model(traces: Iterable[Trace], *, noise_threshold: float = 0.2):
    """Run PM4Py's Inductive Miner to discover a sound process model.

    Returns ``(process_tree, petri_net, initial_marking, final_marking)``.

    This is the one place a heavyweight dependency earns its place: the Inductive
    Miner guarantees a *sound* model (no deadlocks, every transition reachable),
    which a hand-rolled DFG does not. Phase 3 uses the resulting model for
    alignment-based conformance checking against the test suite.

    Raises:
        ImportError: if PM4Py is not installed.
    """
    try:
        import pm4py
    except ImportError as exc:
        raise ImportError(
            "pm4py is required for process discovery: pip install 'goldenflow[mining]'. "
            "The directly-follows graph and variant analysis work without it."
        ) from exc

    log = to_pm4py_log(traces)
    formatted = pm4py.format_dataframe(
        log,
        case_id="case:concept:name",
        activity_key="concept:name",
        timestamp_key="time:timestamp",
    )
    tree = pm4py.discover_process_tree_inductive(
        formatted, noise_threshold=noise_threshold
    )
    net, initial, final = pm4py.convert_to_petri_net(tree)
    return tree, net, initial, final


def pm4py_available() -> bool:
    try:
        import pm4py  # noqa: F401
        return True
    except ImportError:
        return False
