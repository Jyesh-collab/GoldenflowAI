"""Screen taxonomy - the contract that makes everything else possible.

A journey is a sequence of screens observed in production. A test is a sequence of
screens exercised by automation. Those two things can only ever be compared if both
sides agree on what a screen *is*. That agreement is this file.

The taxonomy binds three vocabularies together::

    analytics screen tag  ->  canonical screen ID  ->  Page Object class
    (what the app emits)      (what GoldenFlow uses)   (what Appium drives)

Every validation rule here exists because breaking it silently corrupts the join
rather than failing loudly. A tag mapped to two screens does not raise an error at
runtime - it quietly merges two journeys into one and nobody notices for months.
That is why this is CI-enforced from Phase 0 rather than checked at analysis time.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

import yaml
from pydantic import BaseModel, Field, field_validator

SCREEN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
"""Canonical screen IDs are snake_case. Enforced, not suggested - the ID is a join
key and inconsistent casing produces silent mismatches."""


class Platform(str, Enum):
    ANDROID = "android"
    IOS = "ios"
    BOTH = "both"

    def covers(self, other: "Platform") -> bool:
        """Whether a screen on this platform is exercised by a test on ``other``."""
        return self is Platform.BOTH or other is Platform.BOTH or self is other


class Sensitivity(str, Enum):
    """Data sensitivity of a screen, which drives the session-replay occlusion policy.

    This is not documentation. Session replay records real user screens, so
    ``HIGH`` and ``CRITICAL`` carry a hard occlusion requirement enforced by
    :meth:`Taxonomy.validate`.
    """

    NONE = "none"
    LOW = "low"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def requires_occlusion(self) -> bool:
        return self in (Sensitivity.HIGH, Sensitivity.CRITICAL)

    @property
    def requires_full_screen_occlusion(self) -> bool:
        """CRITICAL screens (payment, auth, health) are masked entirely rather than
        field-by-field. Field-level masking fails open when a new field is added."""
        return self is Sensitivity.CRITICAL


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(BaseModel):
    """A single taxonomy defect. ERROR fails CI; WARNING is reported and tracked."""

    severity: Severity
    rule: str
    message: str
    screen_id: str | None = None

    def format(self) -> str:
        where = f" [{self.screen_id}]" if self.screen_id else ""
        return f"{self.severity.value.upper():7} {self.rule}{where}: {self.message}"


class ScreenDefinition(BaseModel):
    """One canonical screen, and everything needed to join it across the stack."""

    screen_id: str = Field(description="Canonical snake_case identifier - the join key")
    display_name: str = Field(description="Human-readable name used in reports")
    domain: str = Field(description="Functional area, e.g. checkout, auth, browse")
    platform: Platform = Platform.BOTH

    analytics_tags: list[str] = Field(
        default_factory=list,
        description="Screen tags the app SDK emits. A screen with none can never "
        "appear in a mined journey.",
    )
    page_objects: list[str] = Field(
        default_factory=list,
        description="Page Object classes driving this screen. Empty means no "
        "automation binding exists yet - a coverage gap, not an error.",
    )

    sensitivity: Sensitivity = Sensitivity.NONE
    occlusion_required: bool = False
    critical: bool = Field(
        default=False,
        description="Business-critical screen. Feeds Golden Journey scoring in Phase 2 "
        "and protects against traffic-based pruning in Phase 5.",
    )
    notes: str = ""

    @field_validator("screen_id")
    @classmethod
    def _check_id_format(cls, v: str) -> str:
        if not SCREEN_ID_PATTERN.match(v):
            raise ValueError(
                f"screen_id {v!r} must be snake_case matching {SCREEN_ID_PATTERN.pattern}"
            )
        return v

    @property
    def is_automatable(self) -> bool:
        """Whether this screen can currently be turned into an executable test step."""
        return bool(self.page_objects)

    @property
    def is_observable(self) -> bool:
        """Whether this screen can currently appear in a mined production journey."""
        return bool(self.analytics_tags)


class Taxonomy(BaseModel):
    """A versioned collection of screen definitions plus the domains they may use."""

    version: str
    app_id: str
    domains: list[str] = Field(default_factory=list)
    screens: list[ScreenDefinition] = Field(default_factory=list)

    # ---------------------------------------------------------------- loading

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Taxonomy":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(raw)

    def to_yaml(self, path: str | Path) -> None:
        payload = self.model_dump(mode="json", exclude_defaults=False)
        Path(path).write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    # ---------------------------------------------------------------- lookups

    def by_id(self, screen_id: str) -> ScreenDefinition | None:
        return next((s for s in self.screens if s.screen_id == screen_id), None)

    def resolve_tag(self, analytics_tag: str) -> ScreenDefinition | None:
        """Map an observed analytics tag back to its canonical screen.

        This is the single most-called function in the whole system - every mined
        event passes through it.
        """
        return next((s for s in self.screens if analytics_tag in s.analytics_tags), None)

    def resolve_page_object(self, page_object: str) -> ScreenDefinition | None:
        """Map a Page Object class back to its canonical screen (used in Phase 3)."""
        return next((s for s in self.screens if page_object in s.page_objects), None)

    @property
    def known_tags(self) -> set[str]:
        return {tag for s in self.screens for tag in s.analytics_tags}

    def fingerprint(self) -> str:
        """Stable content hash. Used to detect taxonomy drift between the app repo
        and the automation repo - if these diverge, every join silently degrades."""
        canonical = json.dumps(
            [
                {
                    "screen_id": s.screen_id,
                    "analytics_tags": sorted(s.analytics_tags),
                    "page_objects": sorted(s.page_objects),
                    "platform": s.platform.value,
                }
                for s in sorted(self.screens, key=lambda s: s.screen_id)
            ],
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    # ------------------------------------------------------------- validation

    def validate_contract(self) -> list[ValidationIssue]:
        """Run every contract rule. ERROR issues must fail the CI job.

        Rules are deliberately strict about anything that would corrupt a join and
        lenient about anything that merely represents work not yet done.
        """
        issues: list[ValidationIssue] = []
        issues.extend(self._rule_unique_screen_ids())
        issues.extend(self._rule_unique_analytics_tags())
        issues.extend(self._rule_unique_page_objects())
        issues.extend(self._rule_known_domains())
        issues.extend(self._rule_occlusion_matches_sensitivity())
        issues.extend(self._rule_observable())
        issues.extend(self._rule_automatable())
        return issues

    def _rule_unique_screen_ids(self) -> Iterable[ValidationIssue]:
        seen: dict[str, int] = {}
        for s in self.screens:
            seen[s.screen_id] = seen.get(s.screen_id, 0) + 1
        for screen_id, count in seen.items():
            if count > 1:
                yield ValidationIssue(
                    severity=Severity.ERROR,
                    rule="unique_screen_ids",
                    screen_id=screen_id,
                    message=f"defined {count} times; screen_id is the primary join key",
                )

    def _rule_unique_analytics_tags(self) -> Iterable[ValidationIssue]:
        owners: dict[str, list[str]] = {}
        for s in self.screens:
            for tag in s.analytics_tags:
                owners.setdefault(tag, []).append(s.screen_id)
        for tag, screen_ids in owners.items():
            if len(screen_ids) > 1:
                yield ValidationIssue(
                    severity=Severity.ERROR,
                    rule="unique_analytics_tags",
                    message=(
                        f"analytics tag {tag!r} maps to {len(screen_ids)} screens "
                        f"({', '.join(sorted(screen_ids))}). Ambiguous tags silently "
                        f"merge distinct journeys instead of failing."
                    ),
                )

    def _rule_unique_page_objects(self) -> Iterable[ValidationIssue]:
        owners: dict[str, list[str]] = {}
        for s in self.screens:
            for po in s.page_objects:
                owners.setdefault(po, []).append(s.screen_id)
        for po, screen_ids in owners.items():
            if len(screen_ids) > 1:
                yield ValidationIssue(
                    severity=Severity.ERROR,
                    rule="unique_page_objects",
                    message=(
                        f"Page Object {po!r} is bound to {len(screen_ids)} screens "
                        f"({', '.join(sorted(screen_ids))}); coverage attribution "
                        f"would be ambiguous"
                    ),
                )

    def _rule_known_domains(self) -> Iterable[ValidationIssue]:
        if not self.domains:
            return
        allowed = set(self.domains)
        for s in self.screens:
            if s.domain not in allowed:
                yield ValidationIssue(
                    severity=Severity.ERROR,
                    rule="known_domains",
                    screen_id=s.screen_id,
                    message=f"domain {s.domain!r} is not in the declared domain list",
                )

    def _rule_occlusion_matches_sensitivity(self) -> Iterable[ValidationIssue]:
        for s in self.screens:
            if s.sensitivity.requires_occlusion and not s.occlusion_required:
                yield ValidationIssue(
                    severity=Severity.ERROR,
                    rule="occlusion_policy",
                    screen_id=s.screen_id,
                    message=(
                        f"sensitivity is {s.sensitivity.value!r} but occlusion_required "
                        f"is false. Session replay would record this screen."
                    ),
                )

    def _rule_observable(self) -> Iterable[ValidationIssue]:
        for s in self.screens:
            if not s.is_observable:
                yield ValidationIssue(
                    severity=Severity.WARNING,
                    rule="observable",
                    screen_id=s.screen_id,
                    message="no analytics_tags; this screen can never appear in a "
                    "mined journey",
                )

    def _rule_automatable(self) -> Iterable[ValidationIssue]:
        for s in self.screens:
            if not s.is_automatable:
                severity = Severity.WARNING if not s.critical else Severity.ERROR
                yield ValidationIssue(
                    severity=severity,
                    rule="automatable",
                    screen_id=s.screen_id,
                    message=(
                        "no page_objects bound; journey steps on this screen cannot "
                        "be resolved to executable locators in Phase 4"
                        + (" (screen is marked critical)" if s.critical else "")
                    ),
                )

    # ---------------------------------------------------------------- summary

    def summary(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "version": self.version,
            "fingerprint": self.fingerprint(),
            "screens": len(self.screens),
            "observable": sum(1 for s in self.screens if s.is_observable),
            "automatable": sum(1 for s in self.screens if s.is_automatable),
            "critical": sum(1 for s in self.screens if s.critical),
            "requiring_occlusion": sum(
                1 for s in self.screens if s.sensitivity.requires_occlusion
            ),
            "domains": len(self.domains),
        }
