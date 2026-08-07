"""Audit lineage and cost governance.

Two things a system that writes code into someone's repository owes them.

**Lineage.** For every generated artifact: which journey, which sessions, which
locator sources, which model, which prompt, which reviewer approved it. Required for
regulated environments, and required by anyone conducting a post-incident review who
needs to know why a test existed. An artifact whose origin cannot be reconstructed is
not auditable, and in a regulated org that makes it unmergeable.

**Cost.** LLM spend per generated test, device-farm minutes per validation, warehouse
scan per mining run. A system that quietly costs more than the QE time it saves is a
failure however good its findings are - and without per-unit accounting nobody
notices until the invoice.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


@dataclass
class LineageRecord:
    """The full provenance of one generated artifact."""

    artifact_id: str
    artifact_type: str  # test | deletion | gap_report | journey_catalogue
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    journey_id: str | None = None
    sessions: int = 0
    taxonomy_fingerprint: str = ""
    locator_sources: list[str] = field(default_factory=list)

    generator: str = ""
    model: str = ""
    prompt_hash: str = ""
    """Hash of the exact prompt, not the prompt itself. Prompts can carry screen
    names and property keys; the hash proves which version ran without duplicating
    that content into a second store."""

    validation_runs: int = 0
    validation_profiles: list[str] = field(default_factory=list)

    reviewer: str = ""
    review_decision: str = ""  # approved | rejected | pending
    reviewed_at: datetime | None = None
    rejection_reason: str = ""

    @property
    def complete(self) -> bool:
        """Whether this record could actually answer "why does this test exist?"."""
        return bool(self.journey_id and self.generator and self.taxonomy_fingerprint)

    @property
    def human_approved(self) -> bool:
        return self.review_decision == "approved" and bool(self.reviewer)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "created_at": self.created_at.isoformat(),
            "journey_id": self.journey_id,
            "sessions": self.sessions,
            "taxonomy_fingerprint": self.taxonomy_fingerprint,
            "locator_sources": self.locator_sources,
            "generator": self.generator,
            "model": self.model,
            "prompt_hash": self.prompt_hash,
            "validation_runs": self.validation_runs,
            "validation_profiles": self.validation_profiles,
            "reviewer": self.reviewer,
            "review_decision": self.review_decision,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "rejection_reason": self.rejection_reason,
            "complete": self.complete,
        }


def hash_prompt(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


@dataclass
class AuditLog:
    """Append-only lineage store."""

    records: list[LineageRecord] = field(default_factory=list)

    def add(self, record: LineageRecord) -> None:
        self.records.append(record)

    def for_artifact(self, artifact_id: str) -> LineageRecord | None:
        return next((r for r in self.records if r.artifact_id == artifact_id), None)

    @property
    def incomplete(self) -> list[LineageRecord]:
        return [r for r in self.records if not r.complete]

    @property
    def unreviewed(self) -> list[LineageRecord]:
        return [r for r in self.records if r.review_decision == "pending"]

    def rejections(self) -> list[LineageRecord]:
        """Rejected artifacts. Labelled training data - Phase 6.4's whole point is
        that a reviewer saying no is information, not just a blocked merge."""
        return [r for r in self.records if r.review_decision == "rejected"]

    def rejection_themes(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.rejections():
            reason = record.rejection_reason.strip().lower() or "unspecified"
            counts[reason] = counts.get(reason, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def summary(self) -> dict[str, Any]:
        approved = sum(1 for r in self.records if r.human_approved)
        return {
            "records": len(self.records),
            "complete_lineage": len(self.records) - len(self.incomplete),
            "approved": approved,
            "rejected": len(self.rejections()),
            "pending": len(self.unreviewed),
            "acceptance_rate": round(approved / len(self.records), 3)
            if self.records else None,
            "rejection_themes": self.rejection_themes(),
        }

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"summary": self.summary(),
                        "records": [r.to_dict() for r in self.records]}, indent=2),
            encoding="utf-8",
        )
        return target


# ------------------------------------------------------------------- cost


@dataclass
class CostRates:
    """Unit costs. Defaults are illustrative - set them from real invoices."""

    llm_per_1k_input_tokens: float = 0.003
    llm_per_1k_output_tokens: float = 0.015
    device_minute: float = 0.05
    warehouse_per_tb_scanned: float = 5.0


@dataclass
class CostEvent:
    category: str  # generation | validation | mining
    units: float
    unit: str
    cost: float
    artifact_id: str = ""


@dataclass
class CostLedger:
    """Per-unit accounting, so the economics are visible before the invoice."""

    rates: CostRates = field(default_factory=CostRates)
    events: list[CostEvent] = field(default_factory=list)

    def record_generation(
        self, artifact_id: str, input_tokens: int, output_tokens: int
    ) -> CostEvent:
        cost = (input_tokens / 1000 * self.rates.llm_per_1k_input_tokens
                + output_tokens / 1000 * self.rates.llm_per_1k_output_tokens)
        event = CostEvent("generation", input_tokens + output_tokens, "tokens",
                          round(cost, 4), artifact_id)
        self.events.append(event)
        return event

    def record_validation(self, artifact_id: str, device_minutes: float) -> CostEvent:
        event = CostEvent("validation", device_minutes, "device-minutes",
                          round(device_minutes * self.rates.device_minute, 4),
                          artifact_id)
        self.events.append(event)
        return event

    def record_mining(self, terabytes_scanned: float) -> CostEvent:
        event = CostEvent("mining", terabytes_scanned, "TB",
                          round(terabytes_scanned * self.rates.warehouse_per_tb_scanned, 4))
        self.events.append(event)
        return event

    @property
    def total(self) -> float:
        return round(sum(e.cost for e in self.events), 2)

    def by_category(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for event in self.events:
            totals[event.category] = round(
                totals.get(event.category, 0.0) + event.cost, 4)
        return dict(sorted(totals.items(), key=lambda kv: -kv[1]))

    def cost_per_artifact(self, artifact_ids: Iterable[str]) -> float | None:
        ids = set(artifact_ids)
        if not ids:
            return None
        relevant = sum(e.cost for e in self.events if e.artifact_id in ids)
        return round(relevant / len(ids), 4)

    def unit_economics(
        self, *, merged_tests: int, qe_hours_saved_per_test: float = 3.0,
        qe_hourly_rate: float = 60.0,
    ) -> dict[str, Any]:
        """Cost against the QE time a merged test plausibly replaces.

        ``qe_hours_saved_per_test`` is an assumption, not a measurement, and is
        surfaced in the output so nobody mistakes it for one.
        """
        saved = merged_tests * qe_hours_saved_per_test * qe_hourly_rate
        return {
            "total_cost": self.total,
            "merged_tests": merged_tests,
            "cost_per_merged_test": round(self.total / merged_tests, 2)
            if merged_tests else None,
            "assumed_hours_saved_per_test": qe_hours_saved_per_test,
            "assumed_value": round(saved, 2),
            "net": round(saved - self.total, 2),
            "caveat": "hours-saved is an assumption; measure it against the Phase 0 "
                      "baseline before quoting the net figure",
        }

    def format(self) -> str:
        lines = ["Cost ledger", "=" * 60, f"  total: {self.total:,.2f}", ""]
        for category, cost in self.by_category().items():
            lines.append(f"  {category:<14} {cost:>10,.2f}")
        return "\n".join(lines)


def lineage_from_validation(
    results: Sequence[Any], *, taxonomy_fingerprint: str = ""
) -> AuditLog:
    """Build an audit log from Phase 5 validation results."""
    log = AuditLog()
    for result in results:
        spec = result.test.spec
        provenance = spec.provenance
        log.add(LineageRecord(
            artifact_id=spec.test_name,
            artifact_type="test",
            journey_id=provenance.journey_id if provenance else None,
            sessions=provenance.sessions if provenance else 0,
            taxonomy_fingerprint=(provenance.taxonomy_fingerprint if provenance
                                  else taxonomy_fingerprint) or taxonomy_fingerprint,
            locator_sources=provenance.locator_sources if provenance else [],
            generator=result.test.generator,
            model=provenance.model if provenance else "",
            validation_runs=len(result.executed_runs),
            validation_profiles=sorted(result.profiles),
            review_decision="pending",
        ))
    return log
