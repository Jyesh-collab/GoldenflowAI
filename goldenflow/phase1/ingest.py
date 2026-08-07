"""Ingestion - every vendor shape converges on one canonical event.

RudderStack, the Firebase BigQuery export and Mixpanel all describe the same user
action in incompatible envelopes. Normalising at the edge means Phase 2 never learns
that three vendors exist, and swapping one out later is an adapter change rather
than a rewrite of the mining pipeline.

Two deliberate choices:

* **Unresolvable screen tags are ingested, not dropped.** An event whose tag is not
  in the taxonomy becomes an orphan with ``screen_id = None``. Dropping it would
  hide the drift; keeping it lets the quality monitors measure it and Phase 0's
  naming-consistency deficit stay visible.
* **Events with no usable timestamp are rejected**, because an event that cannot be
  ordered cannot participate in a journey, and silently defaulting it to now would
  corrupt every sequence it touches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from goldenflow.phase1.store import CanonicalEvent

ScreenResolver = Callable[[str], Any]

NAVIGATION_EVENTS = {"screen_view", "screen_viewed", "page_view", "Screen Viewed"}


@dataclass
class IngestResult:
    events: list[CanonicalEvent] = field(default_factory=list)
    rejected: list[tuple[dict[str, Any], str]] = field(default_factory=list)
    orphan_tags: dict[str, int] = field(default_factory=dict)

    @property
    def ingested(self) -> int:
        return len(self.events)

    @property
    def orphan_count(self) -> int:
        return sum(self.orphan_tags.values())

    @property
    def acceptance_rate(self) -> float:
        total = self.ingested + len(self.rejected)
        return round(100.0 * self.ingested / total, 1) if total else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "ingested": self.ingested,
            "rejected": len(self.rejected),
            "acceptance_rate": self.acceptance_rate,
            "orphan_events": self.orphan_count,
            "orphan_tags": dict(
                sorted(self.orphan_tags.items(), key=lambda kv: -kv[1])[:10]
            ),
        }


def _as_id(value: Any) -> str | None:
    """Coerce an identifier to a string.

    Firebase emits ``ga_session_id`` as an integer, Mixpanel sometimes emits numeric
    distinct IDs. Left alone, a session would be keyed as ``12345`` in the event
    table and ``"12345"`` in the crash table, and the risk join would silently
    return zero matches - a failure that looks exactly like "no crashes happened".
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


PACKED_DATETIME = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$")
"""A 14-digit ``yyyyMMddHHmmSS`` integer, which is a datetime and not an epoch.

UXCam sends ISO 8601 so this branch is not needed for it, but the guard stays
because the failure is silent and severe: ``20260804153045`` is about 2.0e13, which
the millisecond branch below would divide by 1000 and hand back as the year 2612 -
no exception, no warning, just every journey misordered. Several analytics vendors
export in this format, and a defensive branch costs nothing.
"""


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        digits = str(int(value))
        match = PACKED_DATETIME.match(digits)
        if match is not None:
            year, month, day, hour, minute, second = (int(g) for g in match.groups())
            if 2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
                try:
                    return datetime(year, month, day, hour, minute, second,
                                    tzinfo=timezone.utc)
                except ValueError:
                    return None
        v = float(value)
        # Firebase exports microseconds; most vendors send seconds or milliseconds.
        if v > 1e14:
            v /= 1_000_000
        elif v > 1e11:
            v /= 1000
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


# ------------------------------------------------------------------- adapters


def _adapt_rudderstack(raw: dict[str, Any]) -> dict[str, Any]:
    context = raw.get("context") or {}
    app = context.get("app") or {}
    os_info = context.get("os") or {}
    props = dict(raw.get("properties") or {})
    # RudderStack 'screen' calls carry the screen in `event` or properties.name.
    is_screen = raw.get("type") == "screen"
    return {
        "event_name": "screen_view" if is_screen else raw.get("event"),
        "screen_tag": raw.get("event") if is_screen else props.get("screen_name"),
        "session_id": context.get("sessionId") or props.get("session_id"),
        "user_id": raw.get("userId"),
        "anonymous_id": raw.get("anonymousId"),
        "timestamp": raw.get("originalTimestamp") or raw.get("timestamp"),
        "app_version": app.get("version"),
        "platform": os_info.get("name"),
        "properties": props,
    }


def _adapt_firebase(raw: dict[str, Any]) -> dict[str, Any]:
    """Firebase BigQuery export: properties arrive as a typed key/value array."""
    props: dict[str, Any] = {}
    for param in raw.get("event_params") or []:
        value = param.get("value") or {}
        props[param.get("key")] = next(
            (
                v
                for v in (
                    value.get("string_value"),
                    value.get("int_value"),
                    value.get("double_value"),
                    value.get("float_value"),
                )
                if v is not None
            ),
            None,
        )
    return {
        "event_name": raw.get("event_name"),
        "screen_tag": props.get("firebase_screen") or props.get("screen_name"),
        "session_id": props.get("ga_session_id") or props.get("session_id"),
        "user_id": raw.get("user_id"),
        "anonymous_id": raw.get("user_pseudo_id"),
        "timestamp": raw.get("event_timestamp"),
        "app_version": (raw.get("app_info") or {}).get("version"),
        "platform": raw.get("platform"),
        "properties": props,
    }


MIXPANEL_RESERVED = {"distinct_id", "time", "token", "insert_id", "mp_lib"}
"""Mixpanel envelope fields that live inside `properties` but are not event
properties. Carrying them through would duplicate identity data into the property
bag that the PII scanner guards."""


def _adapt_mixpanel(raw: dict[str, Any]) -> dict[str, Any]:
    props = dict(raw.get("properties") or {})
    return {
        "event_name": raw.get("event"),
        "screen_tag": props.get("$screen_name") or props.get("screen_name"),
        "session_id": props.get("$session_id") or props.get("session_id"),
        "user_id": props.get("$user_id") or props.get("distinct_id"),
        "anonymous_id": props.get("$device_id"),
        "timestamp": props.get("time"),
        "app_version": props.get("$app_version_string"),
        "platform": props.get("$os"),
        "properties": {
            k: v
            for k, v in props.items()
            if not k.startswith("$") and k not in MIXPANEL_RESERVED
        },
    }


UXCAM_IDENTITY_KEY = "kUXCam_UserIdentity"
UXCAM_RISK_EVENTS = {"Rage Tap", "UI Freeze", "Slow Frames", "ANR"}
"""Events UXCam raises automatically without any instrumentation. They are failure
signals rather than journey steps, and :func:`goldenflow.phase1.risk.from_uxcam`
routes them accordingly."""

UXCAM_SCREEN_EVENTS = {"Screen Viewed", "screen_view", "Screen Changed"}


def _adapt_uxcam(raw: dict[str, Any]) -> dict[str, Any]:
    """UXCam Data Access API event record.

    Shape (verified against developer.uxcam.com/docs/events-endpoint)::

        {"eventId": ..., "eventName": ..., "eventScreen": "Cart",
         "eventDate": "2023-01-22T17:31:57Z",          # ISO 8601
         "url": "https://app.uxcam.com/...",           # session replay deep link
         "sessionProperty": {"sessionId": ..., "hasVideo": true, "durationSec": ...},
         "userProperty": {"kUXCam_UserIdentity": "user_123", ...},
         "eventProperty": {...},
         "device": {"appVersion": ..., "platform": ..., "deviceId": ...}}

    Two things make this materially easier than a manual-instrumentation vendor:

    * **``eventScreen`` is on every event.** UXCam auto-tags the screen an event
      fired on, so the screen sequence falls out of ordering a session's events by
      ``eventDate`` - no per-screen instrumentation required for journeys to exist.
    * **``url`` is a deep link to the session replay.** Carried through as
      ``uxcam_session_url`` so a mined journey, a coverage gap or a generated test
      can point a reviewer at a real user walking the path.

    One honest limit: individual taps and swipes live in the replay *video*, not the
    Events API. What is queryable is named ``logEvent`` calls plus the auto-captured
    signals in :data:`UXCAM_RISK_EVENTS`, each tagged with its screen. That is
    sufficient for journey mining, which operates on screen sequences.
    """
    session_property = raw.get("sessionProperty") or {}
    user_property = raw.get("userProperty") or {}
    device = raw.get("device") or {}
    props = dict(raw.get("eventProperty") or {})

    replay_url = raw.get("url") or session_property.get("url")
    if replay_url:
        props["uxcam_session_url"] = replay_url

    name = raw.get("eventName") or raw.get("event_name")
    screen = raw.get("eventScreen") or raw.get("screen_tag")

    return {
        "event_name": name,
        "screen_tag": screen,
        "session_id": session_property.get("sessionId") or raw.get("sessionId"),
        "user_id": user_property.get(UXCAM_IDENTITY_KEY) or user_property.get("userId"),
        # deviceId is device-scoped and present even before login.
        "anonymous_id": device.get("deviceId") or raw.get("uxcamuserid"),
        "timestamp": raw.get("eventDate") or raw.get("timestamp"),
        "app_version": device.get("appVersion"),
        "platform": device.get("platform"),
        "properties": props,
    }


def load_uxcam_export(payload: dict[str, Any] | Iterable[dict[str, Any]]):
    """Unwrap a UXCam Data Access API response.

    The API returns ``{"success": true, "data": [...]}``; a bare list is accepted
    too so a caller can pass an already-unwrapped batch.
    """
    if isinstance(payload, dict):
        if not payload.get("success", True):
            raise ValueError(
                f"UXCam API reported failure: {payload.get('message', 'no message')}"
            )
        return list(payload.get("data") or [])
    return list(payload)


def _adapt_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Already-flat events, as produced by the sample generator."""
    known = {
        "event_name", "event", "screen_tag", "screen", "screen_name", "session_id",
        "user_id", "anonymous_id", "timestamp", "ts", "app_version", "platform",
        "properties",
    }
    props = dict(raw.get("properties") or {})
    props.update({k: v for k, v in raw.items() if k not in known})
    return {
        "event_name": raw.get("event_name") or raw.get("event"),
        "screen_tag": raw.get("screen_tag") or raw.get("screen") or raw.get("screen_name"),
        "session_id": raw.get("session_id"),
        "user_id": raw.get("user_id"),
        "anonymous_id": raw.get("anonymous_id"),
        "timestamp": raw.get("timestamp") or raw.get("ts"),
        "app_version": raw.get("app_version"),
        "platform": raw.get("platform"),
        "properties": props,
    }


ADAPTERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "rudderstack": _adapt_rudderstack,
    "firebase": _adapt_firebase,
    "mixpanel": _adapt_mixpanel,
    "uxcam": _adapt_uxcam,
    "raw": _adapt_raw,
}


# ---------------------------------------------------------------- normalising


def normalise_event(
    raw: dict[str, Any],
    *,
    source: str = "raw",
    screen_resolver: ScreenResolver | None = None,
    taxonomy_fingerprint: str | None = None,
) -> tuple[CanonicalEvent | None, str | None]:
    """Normalise one vendor event. Returns ``(event, rejection_reason)``."""
    adapter = ADAPTERS.get(source)
    if adapter is None:
        return None, f"unknown source {source!r}"

    flat = adapter(raw)

    name = flat.get("event_name")
    if not name:
        return None, "no event name"

    timestamp = _parse_timestamp(flat.get("timestamp"))
    if timestamp is None:
        # An event that cannot be ordered cannot participate in a journey, and
        # defaulting it to now would corrupt every sequence it touches.
        return None, "missing or unparseable timestamp"

    tag = flat.get("screen_tag")
    screen_id: str | None = None
    if tag and screen_resolver is not None:
        resolved = screen_resolver(tag)
        screen_id = getattr(resolved, "screen_id", None)

    event = CanonicalEvent(
        event_name=name,
        event_type="screen_view" if name in NAVIGATION_EVENTS else "interaction",
        screen_id=screen_id,
        screen_tag=tag,
        session_id=_as_id(flat.get("session_id")),
        user_id=_as_id(flat.get("user_id")),
        anonymous_id=_as_id(flat.get("anonymous_id")),
        timestamp=timestamp,
        app_version=flat.get("app_version"),
        platform=flat.get("platform"),
        source=source,
        properties=flat.get("properties") or {},
        taxonomy_fingerprint=taxonomy_fingerprint,
    )
    return event.finalised(), None


def normalise_stream(
    events: Iterable[dict[str, Any]],
    *,
    source: str = "raw",
    screen_resolver: ScreenResolver | None = None,
    taxonomy_fingerprint: str | None = None,
) -> IngestResult:
    """Normalise a batch, tracking rejects and orphans rather than discarding them."""
    result = IngestResult()
    for raw in events:
        event, reason = normalise_event(
            raw,
            source=source,
            screen_resolver=screen_resolver,
            taxonomy_fingerprint=taxonomy_fingerprint,
        )
        if event is None:
            result.rejected.append((raw, reason or "unknown"))
            continue
        if event.screen_tag and event.screen_id is None:
            result.orphan_tags[event.screen_tag] = (
                result.orphan_tags.get(event.screen_tag, 0) + 1
            )
        result.events.append(event)
    return result
