"""Reporting surfaces - Jira tickets and Grafana metrics.

Both are **payload generators, not integrations**. They emit exactly what would be
POSTed, and stop there. Two reasons: creating tickets in someone's project is an
outward-facing side effect that belongs behind an explicit human action, and a
generator is testable offline while a live client is not.

Wire them up with the Atlassian REST API and a Prometheus pushgateway when you are
ready; nothing here needs to change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from goldenflow.phase3.coverage import CoverageModel
from goldenflow.phase3.gaps import Finding, FindingKind, GapReport, Severity

JIRA_PRIORITY = {
    Severity.CRITICAL: "Highest",
    Severity.HIGH: "High",
    Severity.MEDIUM: "Medium",
    Severity.LOW: "Low",
    Severity.INFO: "Lowest",
}

TICKETABLE = {
    FindingKind.COVERAGE_GAP,
    FindingKind.STALE,
    FindingKind.ASSERTION_GAP,
    FindingKind.UNMAPPED_TEST,
}
"""Obsolete candidates and protected retentions deliberately do not become tickets.
A ticket reading 'delete this test' is an instruction; obsolescence here is only ever
a hypothesis needing a second signal."""


@dataclass
class JiraTicket:
    summary: str
    description: str
    labels: list[str]
    priority: str
    issue_type: str = "Task"

    def to_payload(self, project_key: str) -> dict[str, Any]:
        """The exact body for POST /rest/api/3/issue."""
        return {
            "fields": {
                "project": {"key": project_key},
                "summary": self.summary[:255],
                "description": self.description,
                "issuetype": {"name": self.issue_type},
                "labels": self.labels,
                "priority": {"name": self.priority},
            }
        }


def _describe(finding: Finding) -> str:
    lines = [
        finding.detail,
        "",
        f"Severity: {finding.severity.value}",
        f"Finding type: {finding.kind.value}",
    ]
    if finding.journey_id:
        lines.append(f"Golden Journey: {finding.journey_id}")
    if finding.test_ids:
        lines.append(f"Tests: {', '.join(finding.test_ids[:10])}")
    if finding.evidence:
        lines += ["", "Evidence:", json.dumps(finding.evidence, indent=2)[:1500]]
    if finding.recommendation:
        lines += ["", f"Recommendation: {finding.recommendation}"]
    lines += ["", "Raised automatically by GoldenFlow AI from production telemetry."]
    return "\n".join(lines)


def to_jira_tickets(
    report: GapReport, *, min_severity: Severity = Severity.MEDIUM
) -> list[JiraTicket]:
    """One ticket per actionable finding, above a severity floor.

    The floor matters: a gap report that opens forty Low tickets on its first run
    trains the team to close them unread.
    """
    tickets: list[JiraTicket] = []
    for finding in report.findings:
        if finding.kind not in TICKETABLE:
            continue
        if finding.severity.rank < min_severity.rank:
            continue
        labels = ["goldenflow", finding.kind.value]
        if finding.evidence.get("touches_critical_screen"):
            labels.append("critical-screen")
        tickets.append(JiraTicket(
            summary=f"[GoldenFlow] {finding.title}",
            description=_describe(finding),
            labels=labels,
            priority=JIRA_PRIORITY[finding.severity],
        ))
    return tickets


def write_jira_payloads(
    report: GapReport, path: str | Path, *, project_key: str = "QA",
    min_severity: Severity = Severity.MEDIUM,
) -> int:
    tickets = to_jira_tickets(report, min_severity=min_severity)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps([t.to_payload(project_key) for t in tickets], indent=2),
        encoding="utf-8",
    )
    return len(tickets)


# --------------------------------------------------------------------- Grafana


def _escape(value: Any) -> str:
    """Escape a Prometheus label value: backslash, quote, newline."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def to_prometheus(
    report: GapReport, coverage: CoverageModel, *, app_id: str = "acme-shop"
) -> str:
    """Prometheus exposition format, for a pushgateway behind Grafana.

    This is Grafana in its correct role - displaying GoldenFlow's output. It is not
    a source of journeys, and cannot be: an aggregated metrics store discards the
    per-session ordering that journey reconstruction requires.
    """
    summary = coverage.summary()
    by_severity = report.by_severity()
    lines: list[str] = []

    # `metric_name`, not `name`: Prometheus labels are passed as **kwargs, and a
    # label legitimately called "name" would collide with the parameter.
    def metric(metric_name: str, help_text: str, value: Any, **labels: str) -> None:
        if metric_name not in {ln.split()[2] for ln in lines if ln.startswith("# TYPE")}:
            lines.append(f"# HELP {metric_name} {help_text}")
            lines.append(f"# TYPE {metric_name} gauge")
        label_text = ",".join(f'{k}="{_escape(v)}"' for k, v in
                              {"app": app_id, **labels}.items())
        lines.append(f"{metric_name}{{{label_text}}} {value}")

    metric("goldenflow_transition_coverage_pct",
           "Share of production transitions covered by at least one test",
           summary["transition_coverage_pct"])
    metric("goldenflow_traffic_weighted_coverage_pct",
           "Coverage weighted by production session volume",
           summary["traffic_weighted_coverage_pct"])
    metric("goldenflow_journeys_total", "Golden Journeys in the catalogue",
           summary["journeys"])
    metric("goldenflow_journeys_uncovered",
           "Golden Journeys with no test coverage at all",
           summary["journeys_uncovered"])
    metric("goldenflow_unmapped_tests",
           "Tests whose screens could not be resolved", summary["unmapped_tests"])

    for severity, count in by_severity.items():
        metric("goldenflow_findings", "Gap findings by severity", count,
               severity=severity)
    for kind in FindingKind:
        metric("goldenflow_findings_by_kind", "Gap findings by kind",
               len(report.of_kind(kind)), kind=kind.value)

    for journey_coverage in coverage.journeys[:20]:
        metric("goldenflow_journey_coverage_ratio",
               "Per-journey transition coverage ratio",
               journey_coverage.ratio,
               journey_id=journey_coverage.journey.journey_id,
               name=(journey_coverage.journey.archetype.name or "")[:40])

    return "\n".join(lines) + "\n"


def write_prometheus(
    report: GapReport, coverage: CoverageModel, path: str | Path,
    *, app_id: str = "acme-shop",
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_prometheus(report, coverage, app_id=app_id), encoding="utf-8")
    return target
