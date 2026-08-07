"""Shared builders for Phase 2 tests.

Kept separate so test modules never import one another - a cross-import makes
collection order significant and turns one broken module into a cascade.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from goldenflow.phase1.store import CanonicalEvent
from goldenflow.phase2.mining import Variant
from goldenflow.phase2.sessionize import Trace, sessionize

T0 = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)


def ev(screen: str, offset: int, session: str = "s1", name: str = "screen_view",
       user: str = "u1", **props) -> CanonicalEvent:
    """One canonical event, `offset` seconds after the fixed epoch."""
    return CanonicalEvent(
        event_name=name,
        event_type="screen_view" if name == "screen_view" else "tap",
        screen_id=screen, screen_tag=screen, session_id=session, user_id=user,
        timestamp=T0 + timedelta(seconds=offset), properties=props,
    ).finalised()


def variant(*screens: str, count: int = 1) -> Variant:
    return Variant(sequence=tuple(screens), count=count)


def trace(*screens: str, session: str = "s1") -> Trace:
    """A trace walking `screens` in order, ten seconds apart."""
    return sessionize([ev(s, i * 10, session) for i, s in enumerate(screens)]).traces[0]
