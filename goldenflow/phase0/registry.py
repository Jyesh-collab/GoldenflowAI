"""Protected test registry - the guardrail against traffic-based pruning.

Phase 5 can propose deleting tests it judges obsolete. Its primary signal is
production traffic, and that signal is actively misleading for exactly the tests
that matter most:

    account deletion, refunds, payment failure recovery, GDPR data export,
    accessibility flows, regulatory journeys, disaster recovery

These are low-volume by design. A pruner ranking by usage would delete the
safety-critical coverage first and the results would look like a success metric -
suite size down, runtime down, pass rate up.

This registry exists in Phase 0, months before anything can propose a deletion,
so the guardrail is never retrofitted onto a system that has already caused harm.

Every entry requires a written justification. That is deliberate friction: a
registry that is cheap to append to becomes a dumping ground and stops meaning
anything.
"""

from __future__ import annotations

import fnmatch
import re
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

import yaml
from pydantic import BaseModel, Field, field_validator

MIN_REASON_LENGTH = 25
"""Justifications shorter than this are almost always 'important' or 'do not
delete', which record a conclusion without its reasoning."""


class ProtectionCategory(str, Enum):
    REGULATORY = "regulatory"
    FINANCIAL = "financial"
    DATA_RIGHTS = "data_rights"
    ACCESSIBILITY = "accessibility"
    SECURITY = "security"
    SAFETY = "safety"
    DISASTER_RECOVERY = "disaster_recovery"


class ProtectedTest(BaseModel):
    """One protection rule. Matches test identifiers by glob or regex."""

    pattern: str = Field(
        description="Glob against the test identifier, or 're:<regex>' for a regex"
    )
    category: ProtectionCategory
    reason: str = Field(description="Why traffic is a bad signal for this test")
    owner: str = Field(description="Team or individual accountable for this entry")
    added_on: date
    review_by: date = Field(
        description="Protections expire. An unreviewed registry silently ossifies."
    )

    @field_validator("reason")
    @classmethod
    def _substantive_reason(cls, v: str) -> str:
        if len(v.strip()) < MIN_REASON_LENGTH:
            raise ValueError(
                f"reason must be at least {MIN_REASON_LENGTH} characters and explain "
                f"why low production traffic does not imply low importance; got {v!r}"
            )
        return v.strip()

    def matches(self, test_id: str) -> bool:
        if self.pattern.startswith("re:"):
            return re.search(self.pattern[3:], test_id) is not None
        return fnmatch.fnmatch(test_id, self.pattern)

    def is_stale(self, today: date | None = None) -> bool:
        return (today or date.today()) > self.review_by


class ProtectionDecision(BaseModel):
    """The answer Phase 5 gets back before it may propose a deletion."""

    test_id: str
    protected: bool
    entry: ProtectedTest | None = None

    @property
    def explanation(self) -> str:
        if not self.protected or self.entry is None:
            return f"{self.test_id}: not protected, deletion may be proposed"
        return (
            f"{self.test_id}: PROTECTED under {self.entry.category.value} "
            f"(pattern {self.entry.pattern!r}, owner {self.entry.owner}). "
            f"{self.entry.reason}"
        )


class ProtectedTestRegistry(BaseModel):
    """The registry itself. Loaded from YAML and consulted by Phase 5."""

    app_id: str
    entries: list[ProtectedTest] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ProtectedTestRegistry":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(raw)

    def match(self, test_id: str) -> ProtectedTest | None:
        return next((e for e in self.entries if e.matches(test_id)), None)

    def decide(self, test_id: str) -> ProtectionDecision:
        entry = self.match(test_id)
        return ProtectionDecision(
            test_id=test_id, protected=entry is not None, entry=entry
        )

    def is_protected(self, test_id: str) -> bool:
        return self.match(test_id) is not None

    def assert_deletable(self, test_id: str) -> None:
        """Raise if a caller tries to delete a protected test.

        Phase 5's deletion path must call this. Returning a boolean would make the
        check easy to forget; raising makes omission loud.
        """
        entry = self.match(test_id)
        if entry is not None:
            raise PermissionError(
                f"Refusing to propose deletion of {test_id!r}: protected under "
                f"{entry.category.value} by {entry.owner}. {entry.reason}"
            )

    def partition(self, test_ids: Iterable[str]) -> tuple[list[str], list[str]]:
        """Split candidates into ``(deletable, protected)``."""
        deletable: list[str] = []
        protected: list[str] = []
        for test_id in test_ids:
            (protected if self.is_protected(test_id) else deletable).append(test_id)
        return deletable, protected

    def stale_entries(self, today: date | None = None) -> list[ProtectedTest]:
        """Entries past their review date - the registry's own maintenance backlog."""
        return [e for e in self.entries if e.is_stale(today)]

    def coverage_by_category(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.entries:
            counts[e.category.value] = counts.get(e.category.value, 0) + 1
        return counts

    def summary(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "entries": len(self.entries),
            "by_category": self.coverage_by_category(),
            "stale": len(self.stale_entries()),
        }
