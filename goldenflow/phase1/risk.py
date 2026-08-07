"""Risk signals joined into the journey timeline.

A crash report on its own says *what* broke. Joined on session ID and positioned
against the event sequence, it says *where in the user's journey* it broke - which
is the difference between a stack trace and a testable scenario.

That positioning is the whole point of landing crash data in the same store as
events rather than reading it from the vendor at analysis time. Phase 2 uses it to
risk-weight Golden Journeys; Phase 6 uses it for escaped-defect attribution.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_right
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, Field

from goldenflow.phase1.ingest import _parse_timestamp
from goldenflow.phase1.store import CanonicalEvent


class RiskKind(str, Enum):
    CRASH = "crash"
    ANR = "anr"
    ERROR = "error"
    RAGE_TAP = "rage_tap"
    SLOW_FRAME = "slow_frame"

    @property
    def default_fatal(self) -> bool:
        return self in (RiskKind.CRASH, RiskKind.ANR)


class RiskSignal(BaseModel):
    """A production failure, optionally located within a session."""

    signal_id: str = ""
    kind: str
    session_id: str | None = None
    user_id: str | None = None
    timestamp: datetime
    fatal: bool = False
    title: str = ""

    screen_id: str | None = Field(
        default=None,
        description="Screen the user was on when this fired. Populated by "
        "join_risk_signals, not by the vendor - Crashlytics does not know your "
        "canonical screen IDs.",
    )
    position_in_session: int | None = Field(
        default=None, description="Index of the preceding event within the session"
    )

    source: str = "unknown"
    properties: dict[str, Any] = Field(default_factory=dict)

    def finalised(self) -> "RiskSignal":
        data = self.model_copy()
        if not data.signal_id:
            payload = (
                f"{data.session_id or ''}|{data.kind}|{data.title}|"
                f"{data.timestamp.astimezone(timezone.utc).isoformat()}"
            )
            data.signal_id = hashlib.sha256(payload.encode()).hexdigest()[:32]
        return data

    @property
    def located(self) -> bool:
        return self.screen_id is not None


# ------------------------------------------------------------------- adapters


def from_crashlytics(raw: dict[str, Any]) -> RiskSignal | None:
    """Crashlytics BigQuery export shape."""
    ts = _parse_timestamp(raw.get("event_timestamp") or raw.get("timestamp"))
    if ts is None:
        return None
    issue = raw.get("issue_title") or raw.get("title") or ""
    is_anr = str(raw.get("error_type", "")).upper() == "ANR"
    kind = RiskKind.ANR if is_anr else RiskKind.CRASH
    return RiskSignal(
        kind=kind.value,
        session_id=(raw.get("application") or {}).get("session_id")
        or raw.get("session_id"),
        user_id=raw.get("user_id"),
        timestamp=ts,
        fatal=bool(raw.get("is_fatal", kind.default_fatal)),
        title=issue,
        source="crashlytics",
        properties={
            k: v
            for k, v in raw.items()
            if k in {"error_type", "device", "os_version", "app_version"}
        },
    ).finalised()


def from_sentry(raw: dict[str, Any]) -> RiskSignal | None:
    """Sentry issue/event shape."""
    ts = _parse_timestamp(raw.get("timestamp") or raw.get("dateCreated"))
    if ts is None:
        return None
    level = str(raw.get("level", "error")).lower()
    contexts = raw.get("contexts") or {}
    tags = raw.get("tags") or {}
    if isinstance(tags, list):  # Sentry sends tags either way
        tags = {t.get("key"): t.get("value") for t in tags if isinstance(t, dict)}
    return RiskSignal(
        kind=RiskKind.ERROR.value,
        session_id=tags.get("session_id") or (contexts.get("app") or {}).get("session_id"),
        user_id=(raw.get("user") or {}).get("id"),
        timestamp=ts,
        fatal=level == "fatal",
        title=raw.get("title") or raw.get("message") or "",
        source="sentry",
        properties={"level": level, **{k: v for k, v in tags.items() if k != "session_id"}},
    ).finalised()


UXCAM_KIND = {
    "Rage Tap": RiskKind.RAGE_TAP,
    "UI Freeze": RiskKind.SLOW_FRAME,
    "Slow Frames": RiskKind.SLOW_FRAME,
    "ANR": RiskKind.ANR,
}


def from_uxcam(raw: dict[str, Any]) -> RiskSignal | None:
    """UXCam auto-captured failure signals.

    Rage taps and UI freezes are raised without any instrumentation, and they are
    the signals no crash reporter provides: a user jabbing the same dead button six
    times never crashes the app and never appears in Crashlytics, but it is one of
    the strongest indicators of a broken screen there is.

    ``eventScreen`` means these arrive already attributed to a screen, so unlike
    Crashlytics records they need no session-timeline lookup to be located - though
    :func:`join_risk_signals` still positions them for consistency.
    """
    name = raw.get("eventName") or raw.get("event_name") or ""
    kind = UXCAM_KIND.get(name)
    if kind is None:
        return None

    ts = _parse_timestamp(raw.get("eventDate") or raw.get("timestamp"))
    if ts is None:
        return None

    session_property = raw.get("sessionProperty") or {}
    user_property = raw.get("userProperty") or {}
    properties = dict(raw.get("eventProperty") or {})
    if url := raw.get("url"):
        properties["uxcam_session_url"] = url

    return RiskSignal(
        kind=kind.value,
        session_id=session_property.get("sessionId") or raw.get("sessionId"),
        user_id=user_property.get("kUXCam_UserIdentity"),
        timestamp=ts,
        # Neither kills the app; both mean the screen is failing the user.
        fatal=False,
        title=f"{name} on {raw.get('eventScreen') or 'unknown screen'}",
        screen_id=None,   # resolved via the taxonomy in the ingest path
        source="uxcam",
        properties=properties,
    ).finalised()


ADAPTERS = {
    "crashlytics": from_crashlytics,
    "sentry": from_sentry,
    "uxcam": from_uxcam,
}


def normalise_risk_stream(
    records: Iterable[dict[str, Any]], *, source: str
) -> list[RiskSignal]:
    adapter = ADAPTERS.get(source)
    if adapter is None:
        raise ValueError(f"unknown risk source {source!r}")
    # The UXCam adapter returns None for ordinary events, since its export mixes
    # journey steps and failure signals in one stream. Filtering here keeps that
    # detail out of every caller.
    return [signal for raw in records if (signal := adapter(raw)) is not None]


# ----------------------------------------------------------------- the join


def join_risk_signals(
    signals: Iterable[RiskSignal],
    sessions: dict[str, Sequence[CanonicalEvent]],
) -> list[RiskSignal]:
    """Locate each signal within its session timeline.

    A signal is attributed to the last screen the user was observed on at or before
    the signal fired. Attributing to the *next* event instead would be wrong in the
    case that matters most: a crash has no next event, because the app died.

    Signals whose session is unknown, or which precede the session's first event,
    are returned unlocated rather than guessed at. An unlocated crash is still
    useful as a volume signal; a *mislocated* one corrupts the risk weighting of a
    journey that had nothing to do with it.
    """
    ordered_cache: dict[str, tuple[list[datetime], list[CanonicalEvent]]] = {}
    for session_id, events in sessions.items():
        ordered = sorted(events, key=lambda e: e.timestamp)
        ordered_cache[session_id] = (
            [e.timestamp.astimezone(timezone.utc) for e in ordered],
            ordered,
        )

    located: list[RiskSignal] = []
    for signal in signals:
        signal = signal.finalised()
        entry = ordered_cache.get(signal.session_id or "")
        if entry is None:
            located.append(signal)
            continue

        times, events = entry
        idx = bisect_right(times, signal.timestamp.astimezone(timezone.utc)) - 1
        if idx < 0:
            located.append(signal)
            continue

        preceding = events[idx]
        located.append(
            signal.model_copy(
                update={
                    "screen_id": preceding.screen_id,
                    "position_in_session": idx,
                }
            )
        )
    return located


def risk_by_screen(signals: Iterable[RiskSignal]) -> dict[str, int]:
    """Failure counts per screen - the raw input to Phase 2 risk weighting."""
    counts: dict[str, int] = {}
    for s in signals:
        if s.screen_id:
            counts[s.screen_id] = counts.get(s.screen_id, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def localisation_rate(signals: Sequence[RiskSignal]) -> float:
    """Share of signals successfully placed on a screen.

    A low rate usually means session IDs are not propagating from the analytics SDK
    into the crash reporter - the single most common integration defect in Phase 1,
    and invisible unless measured.
    """
    if not signals:
        return 0.0
    return round(100.0 * sum(1 for s in signals if s.located) / len(signals), 1)
