"""Phase 7 - Multi-App Scale & Productization.

Turns a system that works for one app into a platform an organisation adopts. That is
the difference between a successful pilot and a product, and it is mostly about
removing the platform team from the critical path.

* :mod:`~goldenflow.phase7.tenancy` - per-app config isolation and thin RBAC. The
  isolation that matters is blast radius, not privacy: a bad taxonomy edit for one
  app must not re-key another's journey graph.
* :mod:`~goldenflow.phase7.onboarding` - drafts a taxonomy and tracking plan from a
  production event sample. This is what takes a new app from months to weeks.
  Everything it produces is a draft that says so, because sensitivity decides
  occlusion and guessing wrong is a privacy incident.
* :mod:`~goldenflow.phase7.parity` - iOS/Android journey and coverage divergence.
* :mod:`~goldenflow.phase7.portfolio` - connector registry and cross-tenant rollup,
  with ROI measured against each app's own baseline rather than a portfolio average
  that hides the laggard.
"""

from goldenflow.phase7.onboarding import (
    ReviewItem,
    ScaffoldResult,
    infer_property_type,
    infer_sensitivity,
    scaffold,
    write_scaffold,
)
from goldenflow.phase7.parity import (
    JourneyParity,
    ParityReport,
    compare_platforms,
    split_by_platform,
)
from goldenflow.phase7.portfolio import (
    Connector,
    ConnectorRegistry,
    PortfolioReport,
    TenantHealth,
    build_portfolio,
)
from goldenflow.phase7.tenancy import (
    Permission,
    PermissionDenied,
    Principal,
    Role,
    TenantConfig,
    TenantRegistry,
    default_registry,
)

__all__ = [
    "ReviewItem", "ScaffoldResult", "infer_property_type", "infer_sensitivity",
    "scaffold", "write_scaffold",
    "JourneyParity", "ParityReport", "compare_platforms", "split_by_platform",
    "Connector", "ConnectorRegistry", "PortfolioReport", "TenantHealth",
    "build_portfolio",
    "Permission", "PermissionDenied", "Principal", "Role", "TenantConfig",
    "TenantRegistry", "default_registry",
]
