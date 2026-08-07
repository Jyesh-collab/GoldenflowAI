"""Baseline metrics - the immutable "before".

Phase 6 claims a measured reduction in escaped defects. That claim is only
defensible against a baseline captured *before* any GoldenFlow work began, by
people who did not yet know which numbers would later be quoted.

Snapshots are therefore write-once. :meth:`BaselineSnapshot.save` refuses to
overwrite an existing file, and every snapshot carries a checksum so tampering
after the fact is detectable. This is not distrust of the team - it is what makes
the Phase 6 result citable by someone who was not in the room.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

Direction = Literal["higher_is_better", "lower_is_better"]

METRIC_DIRECTION: dict[str, Direction] = {
    "automated_coverage_pct": "higher_is_better",
    "golden_journey_coverage_pct": "higher_is_better",
    "escaped_defects_per_release": "lower_is_better",
    "mean_time_to_detect_hours": "lower_is_better",
    "regression_suite_runtime_minutes": "lower_is_better",
    "flaky_test_pct": "lower_is_better",
    "manual_authoring_hours_per_sprint": "lower_is_better",
    "total_test_count": "higher_is_better",
}
"""Which way is good. Without this a comparison cannot tell improvement from
regression - a shrinking suite is a win in Phase 5 and a loss in Phase 1."""


class BaselineSnapshot(BaseModel):
    """Pre-GoldenFlow measurement of a single app's QE posture."""

    app_id: str
    release_version: str
    captured_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    captured_by: str = ""

    automated_coverage_pct: float = Field(
        default=0.0, description="% of documented test cases automated"
    )
    golden_journey_coverage_pct: float = Field(
        default=0.0,
        description="% of top-20 journeys covered. Zero before Phase 2 - the "
        "journeys are not yet known, which is itself the point.",
    )
    escaped_defects_per_release: float = 0.0
    mean_time_to_detect_hours: float = 0.0
    regression_suite_runtime_minutes: float = 0.0
    flaky_test_pct: float = 0.0
    manual_authoring_hours_per_sprint: float = 0.0
    total_test_count: int = 0

    notes: str = ""

    # ------------------------------------------------------------- integrity

    def checksum(self) -> str:
        payload = self.model_dump(mode="json", exclude={"notes"})
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()[:16]

    def save(self, path: str | Path, *, force: bool = False) -> Path:
        """Write the snapshot. Refuses to clobber an existing baseline.

        Raises:
            FileExistsError: if the target exists and ``force`` is not set. A
                baseline that can be quietly rewritten is not a baseline.
        """
        target = Path(path)
        if target.exists() and not force:
            raise FileExistsError(
                f"{target} already exists. Baselines are write-once - a rewritable "
                f"baseline cannot support a Phase 6 improvement claim. Pass "
                f"force=True only to correct a capture error, and record why."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self.model_dump(mode="json")
        payload["_checksum"] = self.checksum()
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path) -> "BaselineSnapshot":
        """Load a snapshot and verify its checksum."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        recorded = raw.pop("_checksum", None)
        snapshot = cls.model_validate(raw)
        if recorded is not None and recorded != snapshot.checksum():
            raise ValueError(
                f"Baseline checksum mismatch in {path}: recorded {recorded}, "
                f"computed {snapshot.checksum()}. The file has been edited since "
                f"capture and must not be used as evidence."
            )
        return snapshot

    def compare(self, later: "BaselineSnapshot") -> "BaselineComparison":
        return BaselineComparison.build(self, later)


class MetricDelta(BaseModel):
    metric: str
    before: float
    after: float
    direction: Direction

    @property
    def absolute(self) -> float:
        return round(self.after - self.before, 3)

    @property
    def relative_pct(self) -> float | None:
        if self.before == 0:
            return None
        return round(100.0 * (self.after - self.before) / abs(self.before), 1)

    @property
    def improved(self) -> bool:
        if self.absolute == 0:
            return False
        return (
            self.absolute > 0
            if self.direction == "higher_is_better"
            else self.absolute < 0
        )

    def format(self) -> str:
        rel = f" ({self.relative_pct:+.1f}%)" if self.relative_pct is not None else ""
        mark = "+" if self.improved else ("-" if self.absolute != 0 else "=")
        return (
            f"  [{mark}] {self.metric:<38} {self.before:>10.2f} -> "
            f"{self.after:>10.2f}{rel}"
        )


class BaselineComparison(BaseModel):
    """Phase 0 baseline versus a later measurement. Consumed by Phase 6."""

    app_id: str
    before_version: str
    after_version: str
    deltas: list[MetricDelta] = Field(default_factory=list)

    @classmethod
    def build(
        cls, before: BaselineSnapshot, after: BaselineSnapshot
    ) -> "BaselineComparison":
        if before.app_id != after.app_id:
            raise ValueError(
                f"Cannot compare snapshots from different apps: "
                f"{before.app_id!r} vs {after.app_id!r}"
            )
        deltas = [
            MetricDelta(
                metric=metric,
                before=float(getattr(before, metric)),
                after=float(getattr(after, metric)),
                direction=direction,
            )
            for metric, direction in METRIC_DIRECTION.items()
        ]
        return cls(
            app_id=before.app_id,
            before_version=before.release_version,
            after_version=after.release_version,
            deltas=deltas,
        )

    @property
    def improved_count(self) -> int:
        return sum(1 for d in self.deltas if d.improved)

    @property
    def regressed_count(self) -> int:
        return sum(1 for d in self.deltas if d.absolute != 0 and not d.improved)

    def format(self) -> str:
        lines = [
            f"Baseline comparison - {self.app_id}",
            f"{self.before_version} -> {self.after_version}",
            "=" * 74,
        ]
        lines += [d.format() for d in self.deltas]
        lines += [
            "-" * 74,
            f"  {self.improved_count} improved, {self.regressed_count} regressed, "
            f"{len(self.deltas) - self.improved_count - self.regressed_count} unchanged",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "before_version": self.before_version,
            "after_version": self.after_version,
            "improved": self.improved_count,
            "regressed": self.regressed_count,
            "deltas": [
                {
                    "metric": d.metric,
                    "before": d.before,
                    "after": d.after,
                    "absolute": d.absolute,
                    "relative_pct": d.relative_pct,
                    "improved": d.improved,
                }
                for d in self.deltas
            ],
        }
