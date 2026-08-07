"""Screen fingerprinting - recognising the same screen across app versions.

Resource IDs churn. Layouts get an extra row. Modules get renamed. If screen
identity is pinned to any of those, the whole graph re-keys itself on every release
and the coverage history resets to zero - which looks exactly like a catastrophic
regression and is in fact a rename.

A fingerprint is therefore built from what survives a redesign, weighted by how much
it survives:

* **accessibility IDs** - deliberate, semantic, maintained for screen readers
* **element type composition** - a cart is a list plus a total plus a button
                                 whatever the styling
* **text content** - "Checkout" stays "Checkout" across a re-skin

Resource IDs are deliberately excluded. They are the thing most likely to have
changed, and including them would make the fingerprint agree with itself only when
nothing happened.

Similarity is weighted Jaccard over those signals, so partial evolution degrades
gracefully instead of flipping identity at some arbitrary edit distance.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from goldenflow.phase4.hierarchy import ScreenDump, UiElement

WEIGHTS = {"accessibility": 0.55, "composition": 0.25, "text": 0.20}
MATCH_THRESHOLD = 0.55
"""Below this, two dumps are treated as different screens. Set from the observation
that a genuine redesign keeps most accessibility IDs; a different screen shares
almost none."""


@dataclass
class ScreenFingerprint:
    screen_id: str
    app_version: str
    accessibility_ids: frozenset[str] = frozenset()
    composition: tuple[tuple[str, int], ...] = ()
    texts: frozenset[str] = frozenset()

    @property
    def digest(self) -> str:
        payload = "|".join([
            ",".join(sorted(self.accessibility_ids)),
            ",".join(f"{k}:{v}" for k, v in self.composition),
        ])
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, object]:
        return {
            "screen_id": self.screen_id,
            "app_version": self.app_version,
            "digest": self.digest,
            "accessibility_ids": sorted(self.accessibility_ids),
            "composition": dict(self.composition),
        }


def _normalise_text(value: str) -> str:
    return " ".join(value.split()).lower()[:60]


def fingerprint(dump: ScreenDump) -> ScreenFingerprint:
    accessibility: set[str] = set()
    texts: set[str] = set()
    composition: dict[str, int] = {}

    for element in dump.elements:
        if element.accessibility_id:
            accessibility.add(element.accessibility_id)
        if element.text:
            texts.add(_normalise_text(element.text))
        composition[element.short_type] = composition.get(element.short_type, 0) + 1

    return ScreenFingerprint(
        screen_id=dump.screen_id,
        app_version=dump.app_version,
        accessibility_ids=frozenset(accessibility),
        composition=tuple(sorted(composition.items())),
        texts=frozenset(texts),
    )


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def _composition_similarity(
    a: tuple[tuple[str, int], ...], b: tuple[tuple[str, int], ...]
) -> float:
    """Bag similarity over element types - shared count over total count."""
    da, db = dict(a), dict(b)
    keys = set(da) | set(db)
    if not keys:
        return 1.0
    shared = sum(min(da.get(k, 0), db.get(k, 0)) for k in keys)
    total = sum(max(da.get(k, 0), db.get(k, 0)) for k in keys)
    return shared / total if total else 0.0


def similarity(a: ScreenFingerprint, b: ScreenFingerprint) -> float:
    """Weighted similarity in [0, 1]."""
    score = (
        WEIGHTS["accessibility"] * _jaccard(a.accessibility_ids, b.accessibility_ids)
        + WEIGHTS["composition"] * _composition_similarity(a.composition, b.composition)
        + WEIGHTS["text"] * _jaccard(a.texts, b.texts)
    )
    return round(score, 4)


@dataclass
class ScreenMatch:
    screen_id: str
    matched_to: str | None
    score: float
    verdict: str  # matched | renamed | new | changed | not_captured

    @property
    def stable(self) -> bool:
        return self.verdict in ("matched", "renamed")

    @property
    def comparable(self) -> bool:
        """Whether this screen was present in both captures.

        ``not_captured`` screens are excluded from the stability denominator: a
        crawler that did not reach a screen this run proves nothing about whether
        the screen still exists, and counting it as instability would report a
        catastrophic regression every time a crawl times out.
        """
        return self.verdict != "not_captured"


@dataclass
class FingerprintReport:
    matches: list[ScreenMatch] = field(default_factory=list)
    baseline_version: str = ""
    current_version: str = ""

    @property
    def comparable_matches(self) -> list[ScreenMatch]:
        return [m for m in self.matches if m.comparable]

    @property
    def stability_pct(self) -> float:
        """Identity stability over screens captured in *both* builds."""
        comparable = self.comparable_matches
        if not comparable:
            return 0.0
        stable = sum(1 for m in comparable if m.stable)
        return round(100.0 * stable / len(comparable), 1)

    def of_verdict(self, verdict: str) -> list[ScreenMatch]:
        return [m for m in self.matches if m.verdict == verdict]

    def format(self) -> str:
        uncaptured = self.of_verdict("not_captured")
        lines = [
            f"Screen fingerprinting: {self.baseline_version} -> {self.current_version}",
            "=" * 72,
            f"  identity stability: {self.stability_pct}% "
            f"(over {len(self.comparable_matches)} screen(s) captured in both builds)",
        ]
        if uncaptured:
            lines.append(
                f"  not captured      : {len(uncaptured)} screen(s) absent from the "
                f"current crawl - status unknown, not evidence of removal"
            )
        for match in self.matches:
            target = match.matched_to or "-"
            lines.append(f"  [{match.verdict:12}] {match.screen_id:<22} -> "
                         f"{target:<22} {match.score:.3f}")
        return "\n".join(lines)


def match_screens(
    baseline: Mapping[str, ScreenDump],
    current: Mapping[str, ScreenDump],
    *,
    threshold: float = MATCH_THRESHOLD,
) -> FingerprintReport:
    """Match screens across two app versions by structural identity.

    Verdicts:

    ``matched``       same ID, fingerprint still agrees
    ``renamed``       fingerprint reappears under a different ID - the case that
                      would otherwise silently reset a screen's coverage history
    ``changed``       same ID captured in both builds, but the fingerprint diverged
                      far enough that identity is doubtful
    ``not_captured``  present in the baseline, absent from the current capture.
                      **Not** "removed": a crawler that did not reach a screen
                      proves nothing about whether it still exists, and calling it
                      removal would send someone to delete a live Page Object.
    ``new``           present only in the current capture
    """
    base_prints = {sid: fingerprint(dump) for sid, dump in baseline.items()}
    curr_prints = {sid: fingerprint(dump) for sid, dump in current.items()}

    report = FingerprintReport(
        baseline_version=next(iter(baseline.values())).app_version if baseline else "",
        current_version=next(iter(current.values())).app_version if current else "",
    )
    claimed: set[str] = set()

    for screen_id, base in base_prints.items():
        direct = curr_prints.get(screen_id)
        if direct is not None:
            score = similarity(base, direct)
            if score >= threshold:
                claimed.add(screen_id)
                report.matches.append(
                    ScreenMatch(screen_id, screen_id, score, "matched")
                )
                continue

        ranked = sorted(
            ((sid, similarity(base, fp)) for sid, fp in curr_prints.items()
             if sid not in claimed),
            key=lambda kv: -kv[1],
        )
        if ranked and ranked[0][1] >= threshold:
            target, score = ranked[0]
            claimed.add(target)
            report.matches.append(
                ScreenMatch(screen_id, target, score,
                            "matched" if target == screen_id else "renamed")
            )
        elif direct is not None:
            # Captured in both builds but the fingerprint diverged: a real identity
            # change, and comparable evidence.
            claimed.add(screen_id)
            report.matches.append(
                ScreenMatch(screen_id, screen_id, similarity(base, direct), "changed")
            )
        else:
            best_score = ranked[0][1] if ranked else 0.0
            report.matches.append(
                ScreenMatch(screen_id, None, best_score, "not_captured")
            )

    for screen_id in curr_prints:
        if screen_id not in claimed:
            report.matches.append(ScreenMatch(screen_id, None, 0.0, "new"))

    report.matches.sort(key=lambda m: (m.verdict, m.screen_id))
    return report


def element_similarity(a: UiElement, b: UiElement) -> float:
    """How likely two elements are the same control across versions.

    Used by self-healing to find where a broken locator's target went.
    """
    score = 0.0
    if a.accessibility_id and a.accessibility_id == b.accessibility_id:
        score += 0.45
    if a.text and _normalise_text(a.text) == _normalise_text(b.text):
        score += 0.25
    if a.short_type == b.short_type:
        score += 0.15
    if a.short_resource_id and a.short_resource_id == b.short_resource_id:
        score += 0.10
    if a.clickable == b.clickable:
        score += 0.05
    return round(score, 4)
