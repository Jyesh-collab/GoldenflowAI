"""Multi-tenancy - one platform, many apps, separated blast radius.

Phases 0-6 assume one app. Running three means every piece of configuration that was
a single file becomes per-tenant: taxonomy, tracking plan, protected registry,
scoring weights, drift thresholds, baselines.

The isolation that matters is not data privacy between teams - they generally work
for the same company - it is **blast radius**. A bad taxonomy edit for the payments
app must not re-key the shopping app's journey graph, and a scoring-weight experiment
on one tenant must not silently reprioritise another's test suite.

RBAC here is deliberately thin and guards exactly one thing: the operations that are
hard to undo. Reading a gap report needs no permission worth enforcing in code;
approving a test deletion does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from goldenflow.phase2.scoring import ScoringWeights


class Role(str, Enum):
    VIEWER = "viewer"
    """Read gap reports and journey catalogues. The default."""

    ENGINEER = "engineer"
    """Run mining, generation and validation. Cannot approve deletions."""

    OWNER = "owner"
    """Approve deletions and edit the protected registry."""

    PLATFORM = "platform"
    """Onboard tenants and change platform defaults."""

    @property
    def rank(self) -> int:
        return {"viewer": 1, "engineer": 2, "owner": 3, "platform": 4}[self.value]


class Permission(str, Enum):
    READ = "read"
    GENERATE = "generate"
    APPROVE_DELETION = "approve_deletion"
    EDIT_REGISTRY = "edit_registry"
    ONBOARD_TENANT = "onboard_tenant"


ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.VIEWER: {Permission.READ},
    Role.ENGINEER: {Permission.READ, Permission.GENERATE},
    Role.OWNER: {
        Permission.READ, Permission.GENERATE,
        Permission.APPROVE_DELETION, Permission.EDIT_REGISTRY,
    },
    Role.PLATFORM: set(Permission),
}


class PermissionDenied(PermissionError):
    """Raised rather than returned. The same reasoning as Phase 0's
    ``assert_deletable``: a boolean is easy to forget to check."""


@dataclass
class Principal:
    """Who is asking."""

    name: str
    role: Role = Role.VIEWER
    tenants: tuple[str, ...] = ()
    """Tenants this principal may act on. Empty means all - platform staff only."""

    def permissions(self) -> set[Permission]:
        return ROLE_PERMISSIONS[self.role]

    def may(self, permission: Permission, tenant_id: str | None = None) -> bool:
        if permission not in self.permissions():
            return False
        if tenant_id and self.tenants and tenant_id not in self.tenants:
            return False
        return True

    def require(self, permission: Permission, tenant_id: str | None = None) -> None:
        if self.may(permission, tenant_id):
            return
        scope = f" on tenant {tenant_id!r}" if tenant_id else ""
        raise PermissionDenied(
            f"{self.name} ({self.role.value}) may not {permission.value}{scope}. "
            f"Required role: "
            f"{min((r for r, p in ROLE_PERMISSIONS.items() if permission in p), key=lambda r: r.rank).value}."
        )


class TenantConfig(BaseModel):
    """Everything that varies per app.

    Paths are relative to the tenant root, so a tenant is a directory and moving one
    between environments is a copy rather than a migration.
    """

    tenant_id: str
    display_name: str = ""
    platform: str = "both"          # android | ios | both
    owner_team: str = ""

    # Deliberately empty, and filled in per-tenant below. Defaulting these to a
    # shared path such as `config/taxonomy.yaml` would mean two tenants created
    # without explicit configuration silently share a taxonomy - exactly the blast
    # radius this module exists to prevent, handed out by default.
    taxonomy_path: str = ""
    tracking_plan_path: str = ""
    registry_path: str = ""
    suite_path: str = ""
    store_path: str = ""

    source: str = "uxcam"
    weights: dict[str, float] = Field(default_factory=dict)
    drift_thresholds: dict[str, float] = Field(default_factory=dict)
    readiness_threshold: float = 70.0

    onboarded_at: str = ""
    status: str = "active"          # onboarding | active | paused

    @field_validator("tenant_id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not v or not all(c.isalnum() or c in "-_" for c in v):
            raise ValueError(f"tenant_id {v!r} must be a non-empty slug")
        return v

    @model_validator(mode="after")
    def _default_paths_per_tenant(self) -> "TenantConfig":
        """Fill unset paths with tenant-scoped defaults.

        A new tenant is isolated by construction; sharing config with another tenant
        becomes a choice someone had to write down, which
        :meth:`TenantRegistry.isolation_check` will then flag.
        """
        base = f"config/tenants/{self.tenant_id}"
        if not self.taxonomy_path:
            self.taxonomy_path = f"{base}/taxonomy.yaml"
        if not self.tracking_plan_path:
            self.tracking_plan_path = f"{base}/tracking-plan.yaml"
        if not self.registry_path:
            self.registry_path = f"{base}/protected-tests.yaml"
        if not self.store_path:
            self.store_path = f"artifacts/{self.tenant_id}/events.db"
        return self

    def scoring_weights(self) -> ScoringWeights:
        """Per-tenant weights, falling back to the platform default.

        A shopping app and a banking app should not share a risk weighting, and
        Phase 6's tuning proposals are per-tenant for the same reason.
        """
        defaults = ScoringWeights()
        return ScoringWeights(
            frequency=self.weights.get("frequency", defaults.frequency),
            business_value=self.weights.get("business_value", defaults.business_value),
            risk=self.weights.get("risk", defaults.risk),
            exposure=self.weights.get("exposure", defaults.exposure),
        )

    def resolve(self, root: str | Path, attribute: str) -> Path:
        """Resolve a configured path against the tenant root."""
        value = getattr(self, attribute)
        if not value:
            raise ValueError(f"tenant {self.tenant_id!r} has no {attribute} configured")
        return Path(root) / value

    def summary(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "display_name": self.display_name or self.tenant_id,
            "platform": self.platform,
            "owner_team": self.owner_team,
            "status": self.status,
            "source": self.source,
            "custom_weights": bool(self.weights),
        }


class TenantRegistry(BaseModel):
    """All tenants on the platform."""

    tenants: list[TenantConfig] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "TenantRegistry":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(raw)

    def to_yaml(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False),
            encoding="utf-8",
        )
        return target

    def get(self, tenant_id: str) -> TenantConfig | None:
        return next((t for t in self.tenants if t.tenant_id == tenant_id), None)

    def require(self, tenant_id: str) -> TenantConfig:
        tenant = self.get(tenant_id)
        if tenant is None:
            known = ", ".join(t.tenant_id for t in self.tenants) or "none"
            raise KeyError(f"unknown tenant {tenant_id!r}; known: {known}")
        return tenant

    def add(self, tenant: TenantConfig, principal: Principal | None = None) -> None:
        if principal is not None:
            principal.require(Permission.ONBOARD_TENANT)
        if self.get(tenant.tenant_id):
            raise ValueError(f"tenant {tenant.tenant_id!r} already exists")
        self.tenants.append(tenant)

    def visible_to(self, principal: Principal) -> list[TenantConfig]:
        if not principal.tenants:
            return list(self.tenants)
        return [t for t in self.tenants if t.tenant_id in principal.tenants]

    @property
    def active(self) -> list[TenantConfig]:
        return [t for t in self.tenants if t.status == "active"]

    def isolation_check(self) -> list[str]:
        """Configuration that two tenants share when they should not.

        A shared store or taxonomy means one team's change re-keys another's journey
        graph - the failure this module exists to prevent, and one that presents as
        a mysterious coverage collapse rather than an error.
        """
        problems: list[str] = []
        for attribute in ("store_path", "taxonomy_path", "suite_path"):
            seen: dict[str, str] = {}
            for tenant in self.tenants:
                value = getattr(tenant, attribute)
                if not value:
                    continue
                if value in seen:
                    problems.append(
                        f"{attribute} {value!r} is shared by {seen[value]!r} and "
                        f"{tenant.tenant_id!r}; a change to one silently affects "
                        f"the other"
                    )
                seen[value] = tenant.tenant_id
        return problems

    def summary(self) -> dict[str, Any]:
        platforms: dict[str, int] = {}
        for tenant in self.tenants:
            platforms[tenant.platform] = platforms.get(tenant.platform, 0) + 1
        return {
            "tenants": len(self.tenants),
            "active": len(self.active),
            "by_platform": platforms,
            "isolation_problems": len(self.isolation_check()),
        }


def default_registry(tenant_ids: Iterable[str]) -> TenantRegistry:
    return TenantRegistry(tenants=[TenantConfig(tenant_id=t) for t in tenant_ids])
