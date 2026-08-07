"""Portfolio rollup - the platform view, and the connector framework.

Two things a platform needs that a single-app tool does not.

**A rollup across tenants**, so a QE director can see where coverage is weakest
without opening six reports - and, critically, so per-app ROI can be compared against
each app's own Phase 0 baseline rather than a portfolio average that hides the
laggard.

**A connector registry**, so adding a new telemetry vendor is a registration rather
than an edit to a hardcoded dict in the ingest path. Phase 1's adapters were fine for
one app; a platform gets asked for Amplitude, Adjust, AppsFlyer and Instabug in its
first year.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterable

from goldenflow.phase1.ingest import ADAPTERS as BUILTIN_ADAPTERS

# --------------------------------------------------------------- connectors


@dataclass
class Connector:
    """One telemetry source and how to normalise it."""

    name: str
    adapter: Callable[[dict[str, Any]], dict[str, Any]]
    kind: str = "events"          # events | risk | both
    auto_capture: bool = False
    """Whether the vendor captures UI interactions without instrumentation.

    Surfaced because it changes the onboarding estimate by weeks, not hours - a
    manual-instrumentation vendor means the app team has to write and ship event
    code before any journey exists.
    """
    notes: str = ""

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name, "kind": self.kind,
            "auto_capture": self.auto_capture, "notes": self.notes,
        }


class ConnectorRegistry:
    """Pluggable telemetry sources.

    Built-ins are registered from Phase 1's adapter table so there is exactly one
    definition of how each vendor is parsed - a second copy would drift.
    """

    def __init__(self) -> None:
        self._connectors: dict[str, Connector] = {}
        self._register_builtins()

    def _register_builtins(self) -> None:
        metadata = {
            "uxcam": (True, "Auto-captures taps, gestures, screens. Session replay."),
            "rudderstack": (False, "CDP. Forwards only what the app already emits."),
            "firebase": (False, "BigQuery export. Manual instrumentation."),
            "mixpanel": (False, "Manual instrumentation."),
            "raw": (False, "Flat JSON, for testing and manual dumps."),
        }
        for name, adapter in BUILTIN_ADAPTERS.items():
            auto, notes = metadata.get(name, (False, ""))
            self._connectors[name] = Connector(
                name=name, adapter=adapter, auto_capture=auto, notes=notes)

    def register(self, connector: Connector, *, replace: bool = False) -> None:
        if connector.name in self._connectors and not replace:
            raise ValueError(
                f"connector {connector.name!r} already registered; pass replace=True "
                f"to override a built-in deliberately"
            )
        self._connectors[connector.name] = connector

    def get(self, name: str) -> Connector | None:
        return self._connectors.get(name)

    def require(self, name: str) -> Connector:
        connector = self.get(name)
        if connector is None:
            raise KeyError(
                f"unknown connector {name!r}; registered: "
                f"{', '.join(sorted(self._connectors))}"
            )
        return connector

    @property
    def names(self) -> list[str]:
        return sorted(self._connectors)

    def auto_capture_sources(self) -> list[str]:
        return sorted(c.name for c in self._connectors.values() if c.auto_capture)

    def summary(self) -> list[dict[str, Any]]:
        return [self._connectors[n].summary() for n in self.names]


# ---------------------------------------------------------------- portfolio


@dataclass
class TenantHealth:
    """One tenant's standing, for the rollup."""

    tenant_id: str
    status: str = "active"
    readiness_score: float | None = None
    journeys: int = 0
    coverage_pct: float | None = None
    traffic_weighted_coverage_pct: float | None = None
    open_gaps: int = 0
    critical_gaps: int = 0
    escaped_defects: int | None = None
    baseline_escaped_defects: float | None = None
    onboarded_on: date | None = None
    live_on: date | None = None

    @property
    def onboarding_days(self) -> int | None:
        if self.onboarded_on and self.live_on:
            return (self.live_on - self.onboarded_on).days
        return None

    @property
    def defect_delta_pct(self) -> float | None:
        """Change against this tenant's own Phase 0 baseline.

        Per-tenant deliberately: a portfolio average lets a strong app hide a weak
        one, and the weak one is the one that needs attention.
        """
        if self.escaped_defects is None or not self.baseline_escaped_defects:
            return None
        baseline = self.baseline_escaped_defects
        return round(100.0 * (self.escaped_defects - baseline) / baseline, 1)

    @property
    def health(self) -> str:
        if self.status != "active":
            return self.status
        if self.coverage_pct is None:
            # An active tenant with no measurement is NOT healthy. Reporting it as
            # such is how a tenant whose pipeline silently stopped sits green on a
            # director's dashboard for a quarter.
            return "no-data"
        if self.critical_gaps or self.coverage_pct < 50:
            return "at-risk"
        return "healthy"


@dataclass
class PortfolioReport:
    tenants: list[TenantHealth] = field(default_factory=list)
    onboarding_target_days: int = 14

    @property
    def active(self) -> list[TenantHealth]:
        return [t for t in self.tenants if t.status == "active"]

    @property
    def at_risk(self) -> list[TenantHealth]:
        return [t for t in self.tenants if t.health == "at-risk"]

    @property
    def mean_onboarding_days(self) -> float | None:
        durations = [t.onboarding_days for t in self.tenants
                     if t.onboarding_days is not None]
        return round(sum(durations) / len(durations), 1) if durations else None

    @property
    def meets_onboarding_target(self) -> bool | None:
        mean = self.mean_onboarding_days
        return mean <= self.onboarding_target_days if mean is not None else None

    def weakest(self, limit: int = 3) -> list[TenantHealth]:
        rated = [t for t in self.active if t.coverage_pct is not None]
        return sorted(rated, key=lambda t: t.coverage_pct)[:limit]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenants": len(self.tenants),
            "active": len(self.active),
            "at_risk": len(self.at_risk),
            "mean_onboarding_days": self.mean_onboarding_days,
            "onboarding_target_days": self.onboarding_target_days,
            "meets_onboarding_target": self.meets_onboarding_target,
            "per_tenant": [
                {
                    "tenant_id": t.tenant_id, "health": t.health,
                    "readiness": t.readiness_score, "journeys": t.journeys,
                    "coverage_pct": t.coverage_pct,
                    "traffic_weighted_coverage_pct": t.traffic_weighted_coverage_pct,
                    "open_gaps": t.open_gaps, "critical_gaps": t.critical_gaps,
                    "defect_delta_pct": t.defect_delta_pct,
                    "onboarding_days": t.onboarding_days,
                }
                for t in self.tenants
            ],
        }

    def format(self) -> str:
        lines = [
            "Portfolio",
            "=" * 86,
            f"  {len(self.active)} active of {len(self.tenants)} tenant(s), "
            f"{len(self.at_risk)} at risk",
        ]
        mean = self.mean_onboarding_days
        if mean is not None:
            verdict = "MET" if self.meets_onboarding_target else "MISSED"
            lines.append(f"  mean onboarding: {mean} days "
                         f"(target {self.onboarding_target_days}) - {verdict}")
        lines += [
            "=" * 86,
            f"  {'tenant':<16}{'health':<10}{'ready':>7}{'journeys':>10}"
            f"{'cov%':>7}{'gaps':>6}{'crit':>6}{'defects':>9}",
            "-" * 86,
        ]
        for tenant in self.tenants:
            ready = "-" if tenant.readiness_score is None else f"{tenant.readiness_score:.0f}"
            coverage = "-" if tenant.coverage_pct is None else f"{tenant.coverage_pct:.0f}"
            delta = tenant.defect_delta_pct
            defects = "-" if delta is None else f"{delta:+.0f}%"
            lines.append(
                f"  {tenant.tenant_id:<16}{tenant.health:<10}{ready:>7}"
                f"{tenant.journeys:>10}{coverage:>7}{tenant.open_gaps:>6}"
                f"{tenant.critical_gaps:>6}{defects:>9}"
            )
        weakest = self.weakest()
        if weakest:
            lines += ["", "  Weakest coverage - where platform effort should go:"]
            for tenant in weakest:
                lines.append(f"    {tenant.tenant_id}: {tenant.coverage_pct:.0f}% "
                             f"({tenant.critical_gaps} critical gap(s))")
        return "\n".join(lines)


def build_portfolio(
    healths: Iterable[TenantHealth], *, onboarding_target_days: int = 14
) -> PortfolioReport:
    report = PortfolioReport(onboarding_target_days=onboarding_target_days)
    report.tenants = sorted(healths, key=lambda t: t.tenant_id)
    return report
