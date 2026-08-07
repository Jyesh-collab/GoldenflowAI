"""Phase 4 - Locator Resolution.

The layer most similar proposals omit entirely. Analytics gives you screen *names*;
Appium needs *locators*. Without this bridge, "automatically generates Appium
scripts" is not an achievable claim.

Three independent resolution paths, merged rather than chosen between::

    Page Objects  ->  highest confidence, reflects team intent
    Crawl dumps   ->  reaches screens the suite has no Page Object for
    Replay dumps  ->  reaches states a crawler cannot (post-payment, real errors)

Everything is ranked by **stability**, because a locator that works today and breaks
on the next build costs more trust than the missing test would have. Accessibility
IDs first, positional XPaths never proposed.
"""

from goldenflow.phase4.fingerprint import (
    FingerprintReport,
    ScreenFingerprint,
    element_similarity,
    fingerprint,
    match_screens,
    similarity,
)
from goldenflow.phase4.healing import HealingReport, Repair, heal, propose_repair
from goldenflow.phase4.hierarchy import (
    ScreenDump,
    UiElement,
    load_dump_directory,
    parse_page_source,
)
from goldenflow.phase4.locators import (
    ElementLocators,
    Locator,
    Strategy,
    best,
    rank,
)
from goldenflow.phase4.resolver import (
    JourneyResolution,
    ResolutionReport,
    ScreenResolution,
    from_dumps,
    from_page_objects,
    merge,
    resolve,
    resolve_journeys,
)

__all__ = [
    "FingerprintReport", "ScreenFingerprint", "element_similarity", "fingerprint",
    "match_screens", "similarity",
    "HealingReport", "Repair", "heal", "propose_repair",
    "ScreenDump", "UiElement", "load_dump_directory", "parse_page_source",
    "ElementLocators", "Locator", "Strategy", "best", "rank",
    "JourneyResolution", "ResolutionReport", "ScreenResolution", "from_dumps",
    "from_page_objects", "merge", "resolve", "resolve_journeys",
]
