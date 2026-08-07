"""Sessionization - turning an event stream into ordered traces.

A trace is one user's journey through the app: an ordered sequence of screens with
the interactions that happened on each. Everything in Phase 2 operates on traces,
and every mining result is only as good as this step.

Three decisions here materially change what gets mined:

**Consecutive duplicate screens collapse.** Real SDKs emit ``screen_view`` on every
re-render, rotation and tab return. Left alone, ``Home -> Home -> Home -> Cart``
becomes a distinct variant from ``Home -> Cart``, and the variant space explodes
into thousands of near-identical paths that mean nothing. Collapsing is not
cosmetic - it is the difference between 50 archetypes and 5,000.

**Long gaps split a session.** A user who backgrounds the app for two hours and
returns is on a second journey, whatever the SDK's session ID says. Vendors disagree
about this and some never expire a session at all.

**Events with no session ID are excluded, and counted.** They cannot be ordered
against anything. Dropping them silently would let a broken SDK integration look
like a quiet week.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from goldenflow.phase1.store import CanonicalEvent

DEFAULT_INACTIVITY_TIMEOUT = timedelta(minutes=30)
MIN_TRACE_LENGTH = 2
"""A single-screen trace has no transitions and contributes nothing to a
directly-follows graph. Kept in the counts, excluded from mining."""


@dataclass
class Step:
    """One screen visit, with the interactions observed while on it."""

    screen_id: str
    entered_at: datetime
    exited_at: datetime
    events: list[CanonicalEvent] = field(default_factory=list)

    @property
    def dwell_seconds(self) -> float:
        return (self.exited_at - self.entered_at).total_seconds()

    @property
    def interactions(self) -> list[str]:
        return [e.event_name for e in self.events if e.event_type != "screen_view"]

    def property_sum(self, name: str) -> float:
        """Total a numeric property across this step - e.g. order_value."""
        total = 0.0
        for event in self.events:
            value = event.properties.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                total += float(value)
        return total


@dataclass
class Trace:
    """One journey: an ordered sequence of screen visits within a session."""

    session_id: str
    user_id: str | None
    steps: list[Step] = field(default_factory=list)
    app_version: str | None = None
    platform: str | None = None
    anonymous_id: str | None = None
    """Device-scoped ID (CleverTap ``objectId``). Present even pre-login, so it is
    the only identity available for journeys that never authenticate."""

    @property
    def identified(self) -> bool:
        return self.user_id is not None

    @property
    def sequence(self) -> tuple[str, ...]:
        """The screen sequence. This is the unit of analysis for everything after."""
        return tuple(step.screen_id for step in self.steps)

    @property
    def started_at(self) -> datetime:
        return self.steps[0].entered_at

    @property
    def ended_at(self) -> datetime:
        return self.steps[-1].exited_at

    @property
    def duration_seconds(self) -> float:
        return (self.ended_at - self.started_at).total_seconds()

    @property
    def length(self) -> int:
        return len(self.steps)

    @property
    def terminal_screen(self) -> str:
        return self.steps[-1].screen_id

    @property
    def all_interactions(self) -> list[str]:
        return [name for step in self.steps for name in step.interactions]

    def contains(self, screen_id: str) -> bool:
        return screen_id in self.sequence

    def property_sum(self, name: str) -> float:
        return sum(step.property_sum(name) for step in self.steps)


@dataclass
class SessionizationResult:
    traces: list[Trace] = field(default_factory=list)
    events_without_session: int = 0
    events_without_screen: int = 0
    traces_split_on_inactivity: int = 0
    traces_too_short: int = 0

    @property
    def usable(self) -> list[Trace]:
        return [t for t in self.traces if t.length >= MIN_TRACE_LENGTH]

    def summary(self) -> dict[str, object]:
        usable = self.usable
        return {
            "traces": len(self.traces),
            "usable_traces": len(usable),
            "too_short": self.traces_too_short,
            "split_on_inactivity": self.traces_split_on_inactivity,
            "events_without_session": self.events_without_session,
            "events_without_screen": self.events_without_screen,
            "mean_length": round(
                sum(t.length for t in usable) / len(usable), 2
            ) if usable else 0.0,
        }


def sessionize(
    events: Iterable[CanonicalEvent],
    *,
    inactivity_timeout: timedelta = DEFAULT_INACTIVITY_TIMEOUT,
    collapse_repeats: bool = True,
) -> SessionizationResult:
    """Group events into ordered traces.

    Args:
        events: Canonical events, in any order.
        inactivity_timeout: Gap after which a session is treated as a new journey.
        collapse_repeats: Merge consecutive visits to the same screen. Leave this on
            unless you specifically want to study re-render behaviour.
    """
    result = SessionizationResult()
    by_session: dict[str, list[CanonicalEvent]] = defaultdict(list)

    for event in events:
        if not event.session_id:
            result.events_without_session += 1
            continue
        if not event.screen_id:
            # Orphan tags are already measured in Phase 1; here they simply cannot
            # be placed on a journey.
            result.events_without_screen += 1
            continue
        by_session[event.session_id].append(event)

    for session_id, session_events in by_session.items():
        session_events.sort(key=lambda e: (e.timestamp, e.event_id))
        segments = _split_on_inactivity(session_events, inactivity_timeout)
        if len(segments) > 1:
            result.traces_split_on_inactivity += len(segments) - 1

        for index, segment in enumerate(segments):
            suffix = f"#{index}" if index else ""
            trace = _build_trace(f"{session_id}{suffix}", segment, collapse_repeats)
            if trace.length < MIN_TRACE_LENGTH:
                result.traces_too_short += 1
            result.traces.append(trace)

    result.traces.sort(key=lambda t: t.started_at)
    return result


def _split_on_inactivity(
    events: Sequence[CanonicalEvent], timeout: timedelta
) -> list[list[CanonicalEvent]]:
    segments: list[list[CanonicalEvent]] = [[events[0]]]
    for previous, current in zip(events, events[1:]):
        if current.timestamp - previous.timestamp > timeout:
            segments.append([current])
        else:
            segments[-1].append(current)
    return segments


def _build_trace(
    session_id: str, events: Sequence[CanonicalEvent], collapse_repeats: bool
) -> Trace:
    steps: list[Step] = []
    for event in events:
        screen = event.screen_id
        if steps and collapse_repeats and steps[-1].screen_id == screen:
            steps[-1].events.append(event)
            steps[-1].exited_at = max(steps[-1].exited_at, event.timestamp)
            continue
        steps.append(Step(
            screen_id=screen,
            entered_at=event.timestamp,
            exited_at=event.timestamp,
            events=[event],
        ))

    first = events[0]
    return Trace(
        session_id=session_id,
        user_id=_resolved_identity(events),
        anonymous_id=first.anonymous_id,
        steps=steps,
        app_version=first.app_version,
        platform=first.platform,
    )


def _resolved_identity(events: Sequence[CanonicalEvent]) -> str | None:
    """The user this session belongs to, once identity is known.

    Identity is revealed *during* a session, not at its start: a user opens the app
    anonymously, walks splash -> login -> otp_verify, and only then does the SDK
    learn who they are. Taking the first event's ``user_id`` therefore returns None
    for every session containing a login - which is precisely the set of sessions
    where a user was identified.

    The effect was silent and directional: ``Variant.user_count`` undercounted the
    authentication and checkout journeys specifically, because those are the ones
    that begin anonymous.

    The last non-null value wins, since a later login supersedes a stale one.
    """
    for event in reversed(events):
        if event.user_id:
            return event.user_id
    return None


def sessionize_store(store, **kwargs) -> SessionizationResult:
    """Convenience wrapper for sessionizing everything in an EventStore."""
    return sessionize(store.iter_events(), **kwargs)
