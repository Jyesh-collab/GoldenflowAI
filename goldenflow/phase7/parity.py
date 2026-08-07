"""Cross-platform parity - where iOS and Android diverge.

The same product, two codebases, two suites. Journeys that differ between them are
either a deliberate platform convention or a bug nobody noticed, and coverage that
differs between them is almost always an accident.

Three questions this answers:

* **Behaviour parity** - do users walk the same paths on both platforms?
* **Coverage parity** - is a journey tested on one and not the other?
* **Screen parity** - does a screen exist on one platform only?

Coverage asymmetry is the finding that pays for this module. A team writes the
checkout test on Android, ships iOS, and nobody notices the iOS path is untested
until it breaks - because the aggregate coverage number looks fine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from goldenflow.phase2.drift import jensen_shannon_divergence
from goldenflow.phase2.mining import Variant, extract_variants
from goldenflow.phase2.sessionize import Trace

DIVERGENCE_WARN = 0.15
DIVERGENCE_ALERT = 0.30


def split_by_platform(traces: Iterable[Trace]) -> dict[str, list[Trace]]:
    """Group traces by platform, normalising vendor casing."""
    grouped: dict[str, list[Trace]] = {}
    for trace in traces:
        key = (trace.platform or "unknown").strip().lower()
        if key in ("ios", "iphone", "ipad"):
            key = "ios"
        elif key in ("android",):
            key = "android"
        grouped.setdefault(key, []).append(trace)
    return grouped


@dataclass
class JourneyParity:
    """One journey, compared across platforms."""

    sequence: tuple[str, ...]
    counts: dict[str, int] = field(default_factory=dict)
    covered: dict[str, bool] = field(default_factory=dict)

    @property
    def platforms_seen(self) -> list[str]:
        return sorted(p for p, c in self.counts.items() if c > 0)

    @property
    def exclusive_to(self) -> str | None:
        seen = self.platforms_seen
        return seen[0] if len(seen) == 1 else None

    @property
    def coverage_asymmetric(self) -> bool:
        """Tested on one platform and not another, while walked on both."""
        if len(self.platforms_seen) < 2:
            return False
        values = {self.covered.get(p, False) for p in self.platforms_seen}
        return len(values) > 1

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def share(self, platform: str) -> float:
        return round(self.counts.get(platform, 0) / self.total, 3) if self.total else 0.0

    def label(self) -> str:
        return " -> ".join(self.sequence[:5]) + (
            " -> ..." if len(self.sequence) > 5 else "")


@dataclass
class ParityReport:
    platforms: list[str] = field(default_factory=list)
    journeys: list[JourneyParity] = field(default_factory=list)
    divergence: float = 0.0
    screens_by_platform: dict[str, set[str]] = field(default_factory=dict)

    @property
    def severity(self) -> str:
        if self.divergence >= DIVERGENCE_ALERT:
            return "ALERT"
        if self.divergence >= DIVERGENCE_WARN:
            return "WARN"
        return "OK"

    @property
    def exclusive_journeys(self) -> list[JourneyParity]:
        return [j for j in self.journeys if j.exclusive_to]

    @property
    def asymmetric_coverage(self) -> list[JourneyParity]:
        """The finding that pays for this module."""
        return [j for j in self.journeys if j.coverage_asymmetric]

    def exclusive_screens(self) -> dict[str, list[str]]:
        """Screens present on one platform only."""
        result: dict[str, list[str]] = {}
        for platform, screens in self.screens_by_platform.items():
            others: set[str] = set()
            for other, other_screens in self.screens_by_platform.items():
                if other != platform:
                    others |= other_screens
            only = sorted(screens - others)
            if only:
                result[platform] = only
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "platforms": self.platforms,
            "divergence": self.divergence,
            "severity": self.severity,
            "journeys_compared": len(self.journeys),
            "exclusive_journeys": [
                {"sequence": list(j.sequence), "platform": j.exclusive_to,
                 "sessions": j.total}
                for j in self.exclusive_journeys[:20]
            ],
            "asymmetric_coverage": [
                {"sequence": list(j.sequence), "covered": j.covered,
                 "sessions": j.counts}
                for j in self.asymmetric_coverage[:20]
            ],
            "exclusive_screens": self.exclusive_screens(),
        }

    def format(self, limit: int = 10) -> str:
        lines = [
            "Cross-platform parity",
            "=" * 74,
            f"  platforms       : {', '.join(self.platforms) or 'none'}",
            f"  behaviour drift : {self.divergence:.4f}  [{self.severity}]",
            f"  journeys        : {len(self.journeys)} compared",
            "=" * 74,
        ]

        asymmetric = self.asymmetric_coverage
        if asymmetric:
            lines.append(f"  Coverage asymmetry ({len(asymmetric)}) - walked on both, "
                         f"tested on one:")
            for journey in asymmetric[:limit]:
                tested = [p for p, c in journey.covered.items() if c]
                untested = [p for p in journey.platforms_seen if p not in tested]
                lines.append(f"    {journey.label()}")
                lines.append(f"      tested on {', '.join(tested) or 'neither'}; "
                             f"UNTESTED on {', '.join(untested)}")
        else:
            lines.append("  No coverage asymmetry found.")

        exclusive = self.exclusive_journeys
        if exclusive:
            lines.append("")
            lines.append(f"  Platform-exclusive journeys ({len(exclusive)}):")
            for journey in exclusive[:limit]:
                lines.append(f"    [{journey.exclusive_to}] {journey.label()} "
                             f"({journey.total:,} sessions)")

        only = self.exclusive_screens()
        if only:
            lines.append("")
            lines.append("  Platform-exclusive screens:")
            for platform, screens in only.items():
                lines.append(f"    {platform}: {', '.join(screens[:8])}")
        return "\n".join(lines)


def compare_platforms(
    traces: Sequence[Trace],
    *,
    covered_sequences: Mapping[str, set[tuple[str, ...]]] | None = None,
    min_sessions: int = 5,
) -> ParityReport:
    """Compare journeys and coverage across platforms.

    Args:
        traces: All mined traces, carrying ``platform``.
        covered_sequences: ``{platform: {sequence, ...}}`` from each platform's suite.
            Absent means coverage parity is skipped rather than assumed.
        min_sessions: Ignore journeys below this on every platform - a single odd
            session is not a parity finding.
    """
    covered_sequences = covered_sequences or {}
    grouped = {p: t for p, t in split_by_platform(traces).items() if p != "unknown"}

    report = ParityReport(platforms=sorted(grouped))
    if len(grouped) < 2:
        return report

    per_platform: dict[str, dict[tuple[str, ...], int]] = {}
    for platform, platform_traces in grouped.items():
        variants: list[Variant] = extract_variants(platform_traces)
        per_platform[platform] = {v.sequence: v.count for v in variants}
        report.screens_by_platform[platform] = {
            screen for v in variants for screen in v.sequence
        }

    every_sequence: set[tuple[str, ...]] = set()
    for counts in per_platform.values():
        every_sequence |= set(counts)

    for sequence in sorted(every_sequence):
        counts = {p: per_platform[p].get(sequence, 0) for p in grouped}
        if max(counts.values()) < min_sessions:
            continue
        report.journeys.append(JourneyParity(
            sequence=sequence,
            counts=counts,
            covered={
                p: sequence in covered_sequences.get(p, set())
                for p in grouped if counts[p] > 0
            },
        ))

    ordered = sorted(grouped)
    left, right = ordered[0], ordered[1]
    report.divergence = jensen_shannon_divergence(
        {k: float(v) for k, v in per_platform[left].items()},
        {k: float(v) for k, v in per_platform[right].items()},
    )
    report.journeys.sort(key=lambda j: -j.total)
    return report
