"""Variant clustering - collapsing thousands of paths into archetypes.

A real app produces tens of thousands of distinct screen sequences. Presenting that
to a QE team is useless, and so is arbitrarily truncating it to a top-20 list: the
21st variant is often the same journey with one extra back-navigation.

Clustering collapses near-identical paths into archetypes that a human can reason
about. The target is 95% of sessions explained by fewer than 50 archetypes.

The similarity measure is normalised **longest common subsequence**, which matches
how people actually judge journeys as "the same": order matters, but an extra step
in the middle does not make it a different journey. Levenshtein would penalise
insertions the same as substitutions, which is wrong here - visiting an extra screen
en route to checkout is still the checkout journey.

Clustering is greedy and deterministic: variants are processed in frequency order,
each either joining the most similar existing archetype above the threshold or
seeding a new one. Deterministic matters more than optimal - the archetype a
journey belongs to must not change because the sample changed slightly, or drift
detection becomes noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Sequence

from goldenflow.phase2.mining import Variant

DEFAULT_SIMILARITY_THRESHOLD = 0.65


@lru_cache(maxsize=8192)
def _lcs_length(a: tuple[str, ...], b: tuple[str, ...]) -> int:
    if not a or not b:
        return 0
    previous = [0] * (len(b) + 1)
    for x in a:
        current = [0]
        for j, y in enumerate(b):
            current.append(previous[j] + 1 if x == y else max(current[j], previous[j + 1]))
        previous = current
    return previous[-1]


def sequence_similarity(a: Sequence[str], b: Sequence[str]) -> float:
    """Normalised LCS similarity in [0, 1]. Identical sequences score 1.0."""
    ta, tb = tuple(a), tuple(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return _lcs_length(ta, tb) / max(len(ta), len(tb))


def common_backbone(a: Sequence[str], b: Sequence[str]) -> tuple[str, ...]:
    """The shared ordered skeleton of two sequences."""
    ta, tb = tuple(a), tuple(b)
    if not ta or not tb:
        return ()
    # Standard LCS reconstruction.
    table = [[0] * (len(tb) + 1) for _ in range(len(ta) + 1)]
    for i, x in enumerate(ta, 1):
        for j, y in enumerate(tb, 1):
            table[i][j] = (
                table[i - 1][j - 1] + 1 if x == y
                else max(table[i - 1][j], table[i][j - 1])
            )
    out: list[str] = []
    i, j = len(ta), len(tb)
    while i and j:
        if ta[i - 1] == tb[j - 1]:
            out.append(ta[i - 1])
            i, j = i - 1, j - 1
        elif table[i - 1][j] >= table[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return tuple(reversed(out))


@dataclass
class Archetype:
    """A cluster of near-identical journeys."""

    archetype_id: str
    representative: Variant
    members: list[Variant] = field(default_factory=list)
    name: str = ""
    description: str = ""

    @property
    def sequence(self) -> tuple[str, ...]:
        """The representative path - the most-walked member of the cluster."""
        return self.representative.sequence

    @property
    def session_count(self) -> int:
        return sum(m.count for m in self.members)

    @property
    def user_count(self) -> int:
        return sum(m.user_count for m in self.members)

    @property
    def variant_count(self) -> int:
        return len(self.members)

    @property
    def mean_duration(self) -> float:
        total = sum(m.total_duration for m in self.members)
        sessions = self.session_count
        return round(total / sessions, 1) if sessions else 0.0

    @property
    def terminal_screens(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for member in self.members:
            counts[member.terminal_screen] = (
                counts.get(member.terminal_screen, 0) + member.count
            )
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    @property
    def screens(self) -> set[str]:
        return {screen for member in self.members for screen in member.sequence}

    def backbone(self) -> tuple[str, ...]:
        """The ordered skeleton every member shares.

        More honest than the representative when a cluster is broad: it shows what
        the journeys genuinely have in common rather than what the most popular one
        happened to do.
        """
        if not self.members:
            return ()
        backbone = self.members[0].sequence
        for member in self.members[1:]:
            backbone = common_backbone(backbone, member.sequence)
            if not backbone:
                break
        return backbone

    def label(self) -> str:
        return self.name or " -> ".join(self.sequence[:6])


@dataclass
class ClusteringResult:
    archetypes: list[Archetype] = field(default_factory=list)
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD
    total_sessions: int = 0
    total_variants: int = 0

    @property
    def compression_ratio(self) -> float:
        if not self.archetypes:
            return 0.0
        return round(self.total_variants / len(self.archetypes), 1)

    def coverage_pct(self, archetype: Archetype) -> float:
        if not self.total_sessions:
            return 0.0
        return round(100.0 * archetype.session_count / self.total_sessions, 2)

    def archetypes_for_coverage(self, target_pct: float = 95.0) -> int:
        cumulative = 0.0
        for index, archetype in enumerate(self.archetypes, start=1):
            cumulative += self.coverage_pct(archetype)
            if cumulative >= target_pct:
                return index
        return len(self.archetypes)

    def explained_pct(self, top_n: int) -> float:
        return round(
            sum(self.coverage_pct(a) for a in self.archetypes[:top_n]), 2
        )

    def summary(self) -> dict[str, object]:
        return {
            "archetypes": len(self.archetypes),
            "variants": self.total_variants,
            "sessions": self.total_sessions,
            "compression_ratio": self.compression_ratio,
            "archetypes_for_95pct": self.archetypes_for_coverage(95.0),
            "top_10_explain_pct": self.explained_pct(10),
        }


def _may_merge(
    variant: Variant, archetype: Archetype, preserve_terminals: frozenset[str]
) -> bool:
    """Whether a variant is allowed to join an archetype at all.

    Guards the failure this clustering would otherwise cause. ``home ->
    account_home -> order_history`` and ``home -> account_home -> account_delete``
    share two of three screens, so they score 0.67 similarity and merge - and the
    GDPR erasure journey disappears into a generic "account" archetype.

    That is catastrophic rather than untidy: an invisible journey produces no
    coverage gap in Phase 3, so Phase 5 never generates its test, and the whole
    chain fails silently for exactly the low-traffic, high-consequence flows the
    protected registry exists to defend.

    So when either side terminates on a screen the taxonomy marks critical, the
    terminal screens must match exactly. For those journeys the destination *is*
    the journey.
    """
    variant_end = variant.terminal_screen
    archetype_end = archetype.representative.terminal_screen
    if variant_end in preserve_terminals or archetype_end in preserve_terminals:
        return variant_end == archetype_end
    return True


def cluster_variants(
    variants: Sequence[Variant],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    max_archetypes: int | None = None,
    preserve_terminals: Sequence[str] | None = None,
) -> ClusteringResult:
    """Group variants into archetypes by sequence similarity.

    Args:
        variants: Variants, in any order - they are sorted by frequency internally
            so the result does not depend on input ordering.
        threshold: Minimum similarity to join an existing archetype. Higher means
            more, tighter clusters.
        max_archetypes: Soft cap. Once reached, remaining variants join their
            nearest *eligible* archetype regardless of threshold. It never forces a
            merge that ``preserve_terminals`` forbids - a capacity limit must not
            be able to erase a critical journey.
        preserve_terminals: Screen IDs whose journeys must keep their own
            archetype. Normally the taxonomy's critical screens.
    """
    ordered = sorted(variants, key=lambda v: (-v.count, v.sequence))
    preserve = frozenset(preserve_terminals or ())
    archetypes: list[Archetype] = []

    for variant in ordered:
        best: Archetype | None = None
        # Starts below zero, not at zero: a variant sharing *nothing* with any
        # existing archetype scores exactly 0.0, and with a 0.0 floor it would
        # never be selected - so max_archetypes could never force that merge and
        # the cap would silently fail to cap.
        best_score = -1.0
        eligible = 0
        for archetype in archetypes:
            if not _may_merge(variant, archetype, preserve):
                continue
            eligible += 1
            score = sequence_similarity(variant.sequence, archetype.sequence)
            if score > best_score:
                best, best_score = archetype, score

        at_capacity = (
            max_archetypes is not None
            and len(archetypes) >= max_archetypes
            and eligible > 0
        )
        if best is not None and (best_score >= threshold or at_capacity):
            best.members.append(variant)
        else:
            archetypes.append(Archetype(
                archetype_id=f"aj_{len(archetypes) + 1:03d}",
                representative=variant,
                members=[variant],
            ))

    archetypes.sort(key=lambda a: -a.session_count)
    for index, archetype in enumerate(archetypes, start=1):
        archetype.archetype_id = f"aj_{index:03d}"

    return ClusteringResult(
        archetypes=archetypes,
        threshold=threshold,
        total_sessions=sum(v.count for v in ordered),
        total_variants=len(ordered),
    )
