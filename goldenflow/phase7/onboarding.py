"""Onboarding automation - getting a new app from five months to two weeks.

Phase 0 for the pilot app took eight weeks, most of it spent hand-writing a taxonomy
and a tracking plan by reading the app. Doing that again for every app is what stops
a platform from becoming one.

Almost all of it is derivable. Point this at a production event sample and it drafts
the taxonomy and tracking plan from what the app already emits: screens from observed
tags, events with their properties and inferred types, and which screens each event
fires on.

**Everything produced is a draft requiring review, and the code says so in the
artifact itself.** Two inferences are unsafe to trust silently:

*Sensitivity* decides whether a screen gets occluded. Guessing ``none`` on a screen
that turns out to show card details is a privacy incident, so unmatched screens
default to ``low`` with a review flag and anything keyword-matched escalates rather
than relaxes. Wrong-high costs replay fidelity; wrong-low costs a breach
notification.

*Domains* are guessed from name fragments and will be wrong often enough that they
are emitted as ``unclassified`` rather than a confident mistake - a reviewer renaming
five domains is cheap, a reviewer trusting a wrong one is not.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from goldenflow.phase0.taxonomy import (
    Platform,
    ScreenDefinition,
    Sensitivity,
    Taxonomy,
)
from goldenflow.phase1.tracking_plan import (
    EventSchema,
    EventType,
    PropertySchema,
    PropertyType,
    TrackingPlan,
)

CRITICAL_MARKERS = (
    "login", "signin", "sign_in", "signup", "register", "otp", "password",
    "passcode", "pin", "payment", "card", "cvv", "credential", "biometric",
    "wallet", "bank",
)
HIGH_MARKERS = (
    "address", "profile", "account", "order", "personal", "email", "phone",
    "kyc", "identity", "document", "refund", "delete", "export", "contact",
    "history", "saved", "billing", "invoice",
)
LOW_MARKERS = ("search", "notification", "help", "faq", "settings")

CRITICAL_SCREEN_MARKERS = CRITICAL_MARKERS + (
    "checkout", "confirm", "cart", "refund", "delete", "export",
)

REQUIRED_PROPERTY_THRESHOLD = 0.95
"""A property present on this share of an event's occurrences is treated as required.
Below it, optional - a property missing 10% of the time is not a contract."""

NAVIGATION_NAMES = {"screen_view", "screen_viewed", "page_view", "Screen Viewed"}


def _snake(text: str) -> str:
    spaced = re.sub(r"(?<!^)(?=[A-Z][a-z])", "_", str(text))
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", spaced).lower().strip("_")
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned or "unknown"


def _title(text: str) -> str:
    return " ".join(word.capitalize() for word in _snake(text).split("_"))


def infer_sensitivity(name: str) -> tuple[Sensitivity, str]:
    """Guess a screen's sensitivity, biased toward over-protection.

    Returns ``(sensitivity, reason)``. The reason travels into the scaffolded YAML
    so a reviewer can see *why* and correct it rather than re-deriving it.
    """
    lowered = _snake(name)
    for marker in CRITICAL_MARKERS:
        if marker in lowered:
            return Sensitivity.CRITICAL, f"name contains {marker!r}"
    for marker in HIGH_MARKERS:
        if marker in lowered:
            return Sensitivity.HIGH, f"name contains {marker!r}"
    for marker in LOW_MARKERS:
        if marker in lowered:
            return Sensitivity.LOW, f"name contains {marker!r}"
    # Unmatched defaults to low, never none: an unreviewed `none` on a screen that
    # turns out to show card details is a breach, and this cannot see the screen.
    return Sensitivity.LOW, "no marker matched - defaulted to low pending review"


def infer_property_type(values: Sequence[Any]) -> PropertyType:
    """Infer a property's type from observed values, most specific first."""
    concrete = [v for v in values if v is not None]
    if not concrete:
        return PropertyType.STRING
    if all(isinstance(v, bool) for v in concrete):
        return PropertyType.BOOLEAN
    if all(isinstance(v, int) and not isinstance(v, bool) for v in concrete):
        return PropertyType.INTEGER
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in concrete):
        return PropertyType.NUMBER
    return PropertyType.STRING


@dataclass
class ReviewItem:
    """Something the scaffolder guessed and a human must confirm."""

    kind: str          # sensitivity | domain | page_object | required_property
    subject: str
    detail: str

    def format(self) -> str:
        return f"  [{self.kind:<18}] {self.subject:<28} {self.detail}"


@dataclass
class ScaffoldResult:
    taxonomy: Taxonomy
    tracking_plan: TrackingPlan
    review_items: list[ReviewItem] = field(default_factory=list)
    events_seen: int = 0
    screens_found: int = 0
    unmapped_events: int = 0

    def of_kind(self, kind: str) -> list[ReviewItem]:
        return [r for r in self.review_items if r.kind == kind]

    def contract_errors(self) -> list[str]:
        """Validation errors the draft will produce, predicted up front.

        A scaffold cannot satisfy the taxonomy contract: critical screens must have
        a Page Object bound, and the scaffolder has no way to know what those are.
        Without this, a team runs ``taxonomy validate`` on a fresh draft, sees a wall
        of errors, and concludes the generator is broken rather than that it has
        handed them a to-do list.
        """
        from goldenflow.phase0.taxonomy import Severity

        return [
            issue.format()
            for issue in self.taxonomy.validate_contract()
            if issue.severity is Severity.ERROR
        ]

    @property
    def estimated_review_hours(self) -> float:
        """Rough effort to turn the draft into a signed-off contract.

        Sensitivity decisions are the expensive ones because getting them wrong is a
        privacy incident, so they are weighted an order of magnitude above the rest.
        """
        sensitivity = len(self.of_kind("sensitivity")) * 0.1
        other = (len(self.review_items) - len(self.of_kind("sensitivity"))) * 0.02
        return round(sensitivity + other + 2.0, 1)

    def summary(self) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        for item in self.review_items:
            by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
        return {
            "events_seen": self.events_seen,
            "screens_found": self.screens_found,
            "events_declared": len(self.tracking_plan.events),
            "unmapped_events": self.unmapped_events,
            "review_items": len(self.review_items),
            "by_kind": by_kind,
            "estimated_review_hours": self.estimated_review_hours,
        }

    def format(self, limit: int = 15) -> str:
        summary = self.summary()
        lines = [
            "Onboarding scaffold - DRAFT, requires review",
            "=" * 74,
            f"  events analysed   : {summary['events_seen']:,}",
            f"  screens drafted   : {summary['screens_found']}",
            f"  events drafted    : {summary['events_declared']}",
            f"  review items      : {summary['review_items']} "
            f"({summary['by_kind']})",
            f"  estimated review  : ~{summary['estimated_review_hours']} hours",
            "=" * 74,
        ]
        lines += [item.format() for item in self.review_items[:limit]]
        if len(self.review_items) > limit:
            lines.append(f"  ... and {len(self.review_items) - limit} more")
        errors = self.contract_errors()
        if errors:
            lines += [
                "",
                f"  EXPECTED: this draft fails `taxonomy validate` with "
                f"{len(errors)} error(s).",
                "  All of them are critical screens with no Page Object bound, which",
                "  the scaffolder cannot know. Binding them is the work; the errors",
                "  are the to-do list, not a defect.",
            ]

        lines += [
            "",
            "  Nothing here is signed off. Sensitivity in particular is inferred",
            "  from screen names and decides whether a screen gets occluded -",
            "  review every one before any SDK ships.",
        ]
        return "\n".join(lines)


def scaffold(
    events: Iterable[dict[str, Any]],
    *,
    app_id: str,
    platform: Platform = Platform.BOTH,
) -> ScaffoldResult:
    """Draft a taxonomy and tracking plan from a production event sample."""
    screens_seen: Counter[str] = Counter()
    event_names: Counter[str] = Counter()
    event_screens: defaultdict[str, set[str]] = defaultdict(set)
    event_properties: defaultdict[str, defaultdict[str, list[Any]]] = defaultdict(
        lambda: defaultdict(list)
    )
    total = 0

    for raw in events:
        total += 1
        name = raw.get("event_name") or raw.get("event") or ""
        tag = raw.get("screen_tag") or raw.get("screen") or raw.get("eventScreen")
        properties = dict(raw.get("properties") or {})

        if tag:
            screens_seen[tag] += 1
        if not name:
            continue
        event_names[name] += 1
        if tag:
            event_screens[name].add(tag)
        for key, value in properties.items():
            event_properties[name][key].append(value)

    review: list[ReviewItem] = []
    screens: list[ScreenDefinition] = []

    for tag, _count in screens_seen.most_common():
        screen_id = _snake(tag)
        sensitivity, reason = infer_sensitivity(tag)
        critical = any(m in screen_id for m in CRITICAL_SCREEN_MARKERS)

        screens.append(ScreenDefinition(
            screen_id=screen_id,
            display_name=_title(tag),
            domain="unclassified",
            platform=platform,
            analytics_tags=[tag],
            page_objects=[],
            sensitivity=sensitivity,
            occlusion_required=sensitivity.requires_occlusion,
            critical=critical,
            notes=f"DRAFT: sensitivity inferred ({reason}). Confirm before rollout.",
        ))
        review.append(ReviewItem(
            "sensitivity", screen_id,
            f"inferred {sensitivity.value} ({reason})"))
        review.append(ReviewItem(
            "domain", screen_id, "domain is 'unclassified' - assign one"))
        review.append(ReviewItem(
            "page_object", screen_id,
            "no Page Object bound - required before Phase 4 can resolve it"))

    tag_to_screen = {tag: _snake(tag) for tag in screens_seen}
    schemas: list[EventSchema] = []

    for name, count in event_names.most_common():
        observed = event_properties[name]
        properties: list[PropertySchema] = []
        for key, values in observed.items():
            present = sum(1 for v in values if v is not None)
            required = (present / count) >= REQUIRED_PROPERTY_THRESHOLD
            property_name = _snake(key)
            try:
                properties.append(PropertySchema(
                    name=property_name,
                    type=infer_property_type(values),
                    required=required,
                    description=f"DRAFT: seen on {present}/{count} occurrences",
                ))
            except ValueError:
                review.append(ReviewItem(
                    "required_property", f"{name}.{key}",
                    "property name is not snake_case and was dropped"))

        is_navigation = name in NAVIGATION_NAMES
        allowed = sorted(
            tag_to_screen[t] for t in event_screens[name] if t in tag_to_screen
        )
        schemas.append(EventSchema(
            name=_snake(name),
            type=EventType.SCREEN_VIEW if is_navigation else EventType.INTERACTION,
            description=f"DRAFT: observed {count:,} times in the sample",
            # A screen_view must be emittable anywhere; for interactions, restricting
            # to observed screens is a guess that a rare path can invalidate.
            screens=["*"] if is_navigation or not allowed else allowed,
            properties=properties,
        ))
        if not is_navigation and allowed:
            review.append(ReviewItem(
                "screens", _snake(name),
                f"restricted to {len(allowed)} observed screen(s) - widen if a rare "
                f"path uses it elsewhere"))

    taxonomy = Taxonomy(
        version="0.1.0-draft",
        app_id=app_id,
        domains=["unclassified"],
        screens=screens,
    )
    plan = TrackingPlan(
        version="0.1.0-draft",
        app_id=app_id,
        common_properties=[
            PropertySchema(name="session_id", type=PropertyType.STRING, required=True,
                           description="Groups events into a journey"),
            PropertySchema(name="timestamp", type=PropertyType.TIMESTAMP, required=True,
                           description="Orders the sequence"),
        ],
        events=schemas,
    )

    return ScaffoldResult(
        taxonomy=taxonomy,
        tracking_plan=plan,
        review_items=review,
        events_seen=total,
        screens_found=len(screens),
        unmapped_events=sum(1 for n in event_names if not event_screens[n]),
    )


def write_scaffold(result: ScaffoldResult, directory: str | Path) -> dict[str, Path]:
    """Write the drafts, with a header making their status unmistakable."""
    from pathlib import Path as _Path

    target = _Path(directory)
    target.mkdir(parents=True, exist_ok=True)

    taxonomy_path = target / "taxonomy.draft.yaml"
    result.taxonomy.to_yaml(taxonomy_path)

    banner = (
        f"# DRAFT - generated by GoldenFlow onboarding on "
        f"{datetime.now(timezone.utc).date()}\n"
        f"# Inferred from a production event sample. NOT signed off.\n"
        f"# Sensitivity decides whether a screen is occluded in session replay -\n"
        f"# review every value before any SDK ships. See docs/pii-policy.md.\n"
    )
    taxonomy_path.write_text(banner + taxonomy_path.read_text(encoding="utf-8"),
                             encoding="utf-8")

    import yaml as _yaml
    plan_path = target / "tracking-plan.draft.yaml"
    plan_path.write_text(
        banner + _yaml.safe_dump(
            result.tracking_plan.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    return {"taxonomy": taxonomy_path, "tracking_plan": plan_path}
