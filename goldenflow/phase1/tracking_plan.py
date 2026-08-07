"""The tracking plan - an event schema contract, reviewed like an API.

This is the interface between the app team and GoldenFlow. The app promises to emit
these events, with these properties, of these types, on these screens. GoldenFlow
promises to build on nothing else.

Without a plan, "instrumentation" means whatever each squad shipped that quarter,
and Phase 2 discovers the inconsistency after the mining pipeline is already built.
With one, drift is detected on the next batch and attributed to a specific event.

Two distinct validations live here:

* :meth:`TrackingPlan.validate_plan` - is the *contract itself* coherent?
* :meth:`TrackingPlan.validate_stream` - does *observed production traffic* honour it?

The second is the one that runs continuously. A contract nobody checks against
reality is documentation.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

import yaml
from pydantic import BaseModel, Field, field_validator

EVENT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
PROPERTY_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")


class PropertyType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    TIMESTAMP = "timestamp"

    def accepts(self, value: Any) -> bool:
        """Type check with the tolerance a real event stream requires.

        Deliberately permissive in one direction only: an integer satisfies
        ``number`` because JSON does not distinguish them, but a numeric string
        does *not* satisfy ``integer`` - that is genuine schema drift and the
        most common cause of silent aggregation failures downstream.
        """
        if value is None:
            return True  # nullability is governed by `required`, not by type
        match self:
            case PropertyType.STRING:
                return isinstance(value, str)
            case PropertyType.INTEGER:
                return isinstance(value, int) and not isinstance(value, bool)
            case PropertyType.NUMBER:
                return isinstance(value, (int, float)) and not isinstance(value, bool)
            case PropertyType.BOOLEAN:
                return isinstance(value, bool)
            case PropertyType.TIMESTAMP:
                # datetime included because the store hands back parsed objects;
                # without it, conformance against the canonical store reads 0%.
                return isinstance(value, (str, int, float, datetime))
        return False


class EventType(str, Enum):
    SCREEN_VIEW = "screen_view"
    INTERACTION = "interaction"
    SYSTEM = "system"
    ERROR = "error"


class ViolationKind(str, Enum):
    UNKNOWN_EVENT = "unknown_event"
    MISSING_REQUIRED_PROPERTY = "missing_required_property"
    TYPE_MISMATCH = "type_mismatch"
    UNDECLARED_PROPERTY = "undeclared_property"
    WRONG_SCREEN = "wrong_screen"


class PropertySchema(BaseModel):
    name: str
    type: PropertyType
    required: bool = False
    description: str = ""

    @field_validator("name")
    @classmethod
    def _snake_case(cls, v: str) -> str:
        if not PROPERTY_NAME_PATTERN.match(v):
            raise ValueError(f"property name {v!r} must be snake_case")
        return v


class EventSchema(BaseModel):
    name: str
    type: EventType
    description: str = ""
    screens: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Canonical screen IDs this event may fire on; ['*'] means any. "
        "Constraining this catches copy-paste instrumentation bugs.",
    )
    properties: list[PropertySchema] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _snake_case(cls, v: str) -> str:
        if not EVENT_NAME_PATTERN.match(v):
            raise ValueError(f"event name {v!r} must be snake_case")
        return v

    @property
    def allows_any_screen(self) -> bool:
        return "*" in self.screens

    def allows_screen(self, screen_id: str | None) -> bool:
        return self.allows_any_screen or (screen_id in self.screens)

    def property_by_name(self, name: str) -> PropertySchema | None:
        return next((p for p in self.properties if p.name == name), None)

    @property
    def required_properties(self) -> list[PropertySchema]:
        return [p for p in self.properties if p.required]


class Violation(BaseModel):
    kind: ViolationKind
    event_name: str
    detail: str
    property_name: str | None = None

    def format(self) -> str:
        return f"{self.kind.value:26} {self.event_name:22} {self.detail}"


class StreamValidationReport(BaseModel):
    """Result of checking observed traffic against the contract."""

    events_checked: int
    violations: list[Violation] = Field(default_factory=list)
    violation_counts: dict[str, int] = Field(default_factory=dict)
    unknown_event_names: list[str] = Field(default_factory=list)

    @property
    def conformance_pct(self) -> float:
        """Share of events with no violation. The headline Phase 1 number."""
        if not self.events_checked:
            return 0.0
        offending = sum(self.violation_counts.values())
        return round(100.0 * max(0, self.events_checked - offending) / self.events_checked, 1)

    @property
    def clean(self) -> bool:
        return not self.violations

    def format(self, limit: int = 15) -> str:
        lines = [
            f"Tracking plan conformance: {self.conformance_pct}% "
            f"({self.events_checked:,} events checked)",
        ]
        if self.clean:
            lines.append("No violations.")
            return "\n".join(lines)
        lines.append(f"{len(self.violations)} distinct violation(s):")
        for v in self.violations[:limit]:
            lines.append(f"  {v.format()}")
        if len(self.violations) > limit:
            lines.append(f"  ... and {len(self.violations) - limit} more")
        if self.unknown_event_names:
            lines.append(
                f"Undeclared events seen in production: "
                f"{', '.join(self.unknown_event_names[:8])}"
            )
        return "\n".join(lines)


class TrackingPlan(BaseModel):
    """The versioned event contract."""

    version: str
    app_id: str
    common_properties: list[PropertySchema] = Field(
        default_factory=list,
        description="Properties required on every event regardless of type",
    )
    events: list[EventSchema] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "TrackingPlan":
        return cls.model_validate(
            yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        )

    def event(self, name: str) -> EventSchema | None:
        return next((e for e in self.events if e.name == name), None)

    @property
    def event_names(self) -> set[str]:
        return {e.name for e in self.events}

    def expected_properties(self, schema: EventSchema) -> dict[str, PropertySchema]:
        merged = {p.name: p for p in self.common_properties}
        merged.update({p.name: p for p in schema.properties})
        return merged

    # ------------------------------------------------------------ plan itself

    def validate_plan(self) -> list[str]:
        """Check the contract is internally coherent. Returns error strings."""
        errors: list[str] = []

        duplicates = [n for n, c in Counter(e.name for e in self.events).items() if c > 1]
        errors += [f"event {n!r} is declared more than once" for n in duplicates]

        common = {p.name for p in self.common_properties}
        for schema in self.events:
            names = [p.name for p in schema.properties]
            dupes = [n for n, c in Counter(names).items() if c > 1]
            errors += [
                f"event {schema.name!r} declares property {n!r} more than once"
                for n in dupes
            ]
            # Redefining a common property per-event is how two teams end up with
            # the same name meaning two different things.
            clash = common.intersection(names)
            errors += [
                f"event {schema.name!r} redefines common property {n!r}" for n in clash
            ]
            if schema.type is EventType.SCREEN_VIEW and not schema.allows_any_screen:
                errors.append(
                    f"event {schema.name!r} is a screen_view but restricts screens; "
                    f"navigation events must be emittable from any screen"
                )
        return errors

    # ---------------------------------------------------------------- stream

    def validate_stream(
        self,
        events: Iterable[dict[str, Any]],
        *,
        screen_resolver: Any = None,
        strict_properties: bool = False,
    ) -> StreamValidationReport:
        """Check observed traffic against the contract.

        Args:
            events: Canonical or raw events. ``properties`` may be a nested dict or
                flattened into the top level; both shapes occur in real exports.
            screen_resolver: Optional callable mapping a screen tag to a canonical
                screen ID, normally ``taxonomy.resolve_tag``. Without it the
                ``WRONG_SCREEN`` rule is skipped rather than guessed at.
            strict_properties: Report properties not declared in the plan. Off by
                default - undeclared properties are usually harmless additions, and
                failing on them makes the contract hostile to iterate against.
        """
        seen: set[tuple] = set()
        violations: list[Violation] = []
        counts: Counter[str] = Counter()
        unknown_names: set[str] = set()
        checked = 0

        def record(v: Violation) -> None:
            key = (v.kind, v.event_name, v.property_name, v.detail)
            if key not in seen:
                seen.add(key)
                violations.append(v)

        for raw in events:
            checked += 1
            name = raw.get("event_name") or raw.get("event") or ""
            props = dict(raw.get("properties") or {})
            # Flattened exports put properties alongside the envelope fields.
            for key, value in raw.items():
                if key not in {"event_name", "event", "properties"}:
                    props.setdefault(key, value)

            schema = self.event(name)
            if schema is None:
                unknown_names.add(name)
                counts[ViolationKind.UNKNOWN_EVENT.value] += 1
                record(Violation(
                    kind=ViolationKind.UNKNOWN_EVENT,
                    event_name=name,
                    detail="emitted in production but not declared in the tracking plan",
                ))
                continue

            expected = self.expected_properties(schema)
            offended = False

            for prop in expected.values():
                value = props.get(prop.name)
                if prop.required and value is None:
                    offended = True
                    record(Violation(
                        kind=ViolationKind.MISSING_REQUIRED_PROPERTY,
                        event_name=name,
                        property_name=prop.name,
                        detail=f"required property {prop.name!r} absent",
                    ))
                elif value is not None and not prop.type.accepts(value):
                    offended = True
                    record(Violation(
                        kind=ViolationKind.TYPE_MISMATCH,
                        event_name=name,
                        property_name=prop.name,
                        detail=f"{prop.name!r} expected {prop.type.value}, "
                               f"got {type(value).__name__}",
                    ))

            if strict_properties:
                for key in props:
                    if key not in expected and key not in _ENVELOPE_FIELDS:
                        offended = True
                        record(Violation(
                            kind=ViolationKind.UNDECLARED_PROPERTY,
                            event_name=name,
                            property_name=key,
                            detail=f"property {key!r} is not declared in the plan",
                        ))

            if screen_resolver is not None and not schema.allows_any_screen:
                tag = props.get("screen_tag") or props.get("screen")
                screen = screen_resolver(tag) if tag else None
                screen_id = getattr(screen, "screen_id", None)
                if screen_id is not None and not schema.allows_screen(screen_id):
                    offended = True
                    record(Violation(
                        kind=ViolationKind.WRONG_SCREEN,
                        event_name=name,
                        detail=f"fired on {screen_id!r}; plan allows "
                               f"{', '.join(schema.screens)}",
                    ))

            if offended:
                counts["property_violations"] = counts.get("property_violations", 0) + 1

        return StreamValidationReport(
            events_checked=checked,
            violations=violations,
            violation_counts=dict(counts),
            unknown_event_names=sorted(unknown_names),
        )


_ENVELOPE_FIELDS = {
    "session_id", "user_id", "anonymous_id", "timestamp", "ts", "received_at",
    "app_version", "platform", "screen_tag", "screen", "screen_name", "event_id",
}
"""Transport-level fields that are never event properties, so they must not be
reported as undeclared ones."""
