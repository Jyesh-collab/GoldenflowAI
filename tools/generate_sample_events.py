"""Generate a SYNTHETIC production sample for exercising the Phase 0/1 pipeline.

This is not production data and must never be presented as such. It exists so the
readiness audit, ingestion, quality monitors and (later) the Phase 2 mining pipeline
can be developed and demonstrated before a real telemetry export is available.

The generator deliberately reproduces the defects a real export contains, because a
pipeline that only ever sees clean input proves nothing:

  * rare flows that never appear in a bounded sample (refunds, data export)
  * screens instrumented in the app but absent from the taxonomy (tag drift)
  * a small fraction of events with no session_id
  * anonymous traffic with no user_id
  * screens that emit navigation events but no interaction events

Emitted properties conform to config/tracking-plan.yaml, and carry no personal data.
Use --inject-pii to deliberately plant a policy violation and watch the scanner
catch it.

    python tools/generate_sample_events.py --sessions 4000
    python tools/generate_sample_events.py --sessions 500 --inject-pii \
        --out artifacts/tainted-events.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# (weight, [screen_id, ...]) - the journeys real users actually walk.
JOURNEYS: list[tuple[float, list[str]]] = [
    (0.28, ["splash", "home", "category_list", "product_list", "product_detail"]),
    (0.18, ["home", "product_detail", "cart"]),
    (0.14, ["splash", "home", "search", "product_detail", "cart",
            "delivery_address", "payment_method", "order_review",
            "order_confirmation"]),
    (0.10, ["splash", "login", "otp_verify", "home"]),
    (0.09, ["home", "search", "product_list", "product_detail"]),
    (0.07, ["home", "account_home", "order_history"]),
    (0.05, ["home", "product_detail", "cart", "delivery_address",
            "payment_method", "payment_failure"]),
    (0.04, ["home", "help_center", "contact_support"]),
    (0.03, ["splash", "onboarding_carousel", "signup", "otp_verify", "home"]),
    (0.02, ["home", "account_home", "order_history", "refund_request"]),
    # Rare-but-real flows. These are the journeys the protected test registry
    # exists for: a fraction of a percent of traffic, disproportionate
    # consequence. They must appear in the sample precisely because a naive
    # traffic ranking would dismiss them.
    (0.010, ["home", "account_home", "address_book"]),
    (0.008, ["home", "account_home", "profile_edit"]),
    (0.006, ["home", "account_home", "saved_cards"]),
    (0.005, ["splash", "login", "forgot_password"]),
    (0.004, ["home", "notification_center"]),
    (0.003, ["home", "account_home", "data_export"]),
    (0.002, ["home", "account_home", "account_delete"]),
]

PAYMENT_TYPES = ["card", "wallet", "upi", "cod"]
FAILURE_CODES = ["insufficient_funds", "card_declined", "gateway_timeout", "3ds_failed"]
REASON_CODES = ["damaged", "wrong_item", "late_delivery", "changed_mind"]
FILTER_TYPES = ["price", "brand", "rating", "availability"]
TOPICS = ["order_status", "payment", "returns", "account"]


def _props(event: str, rng: random.Random) -> dict:
    """Properties matching config/tracking-plan.yaml. No personal data, by design."""
    match event:
        case "banner_tap":
            return {"banner_id": f"bnr_{rng.randint(1, 12)}", "position": rng.randint(0, 5)}
        case "category_tap":
            return {"category_id": f"cat_{rng.randint(1, 40)}"}
        case "search_submit":
            # Length only. Search terms routinely contain names and order numbers.
            return {"query_length": rng.randint(3, 24), "result_count": rng.randint(0, 200)}
        case "suggestion_tap":
            return {"position": rng.randint(0, 9)}
        case "filter_apply":
            return {"filter_type": rng.choice(FILTER_TYPES),
                    "result_count": rng.randint(0, 150)}
        case "product_tap":
            return {"product_id": f"sku_{rng.randint(1000, 9999)}",
                    "position": rng.randint(0, 30)}
        case "variant_select":
            return {"variant_id": f"var_{rng.randint(1, 8)}"}
        case "add_to_cart":
            return {"product_id": f"sku_{rng.randint(1000, 9999)}",
                    "quantity": rng.randint(1, 3),
                    "price": round(rng.uniform(4.99, 249.99), 2)}
        case "quantity_change":
            return {"product_id": f"sku_{rng.randint(1000, 9999)}",
                    "quantity": rng.randint(1, 5)}
        case "checkout_start":
            return {"cart_value": round(rng.uniform(9.99, 599.99), 2),
                    "item_count": rng.randint(1, 6)}
        case "address_select":
            return {"address_id": f"addr_{rng.randint(1, 4)}"}
        case "payment_select":
            return {"payment_type": rng.choice(PAYMENT_TYPES)}
        case "place_order":
            return {"order_value": round(rng.uniform(9.99, 599.99), 2),
                    "item_count": rng.randint(1, 6)}
        case "order_completed":
            return {"order_id": f"ord_{rng.randint(100000, 999999)}",
                    "order_value": round(rng.uniform(9.99, 599.99), 2)}
        case "payment_failed":
            return {"failure_code": rng.choice(FAILURE_CODES),
                    "payment_type": rng.choice(PAYMENT_TYPES),
                    "retry_count": rng.randint(0, 2)}
        case "retry_payment":
            return {"retry_count": rng.randint(1, 3)}
        case "login_submit" | "signup_submit":
            return {"method": rng.choice(["password", "otp", "social"])}
        case "otp_submit":
            return {"attempt": rng.randint(1, 3)}
        case "ticket_submit":
            return {"topic": rng.choice(TOPICS)}
        case "refund_submit":
            return {"order_id": f"ord_{rng.randint(100000, 999999)}",
                    "reason_code": rng.choice(REASON_CODES)}
    return {}


# Interaction events per screen. Screens absent here emit navigation only, which is
# exactly the action_coverage deficit the readiness audit is designed to surface.
ACTIONS: dict[str, list[str]] = {
    "home": ["banner_tap", "category_tap"],
    "search": ["search_submit", "suggestion_tap"],
    "product_list": ["filter_apply", "product_tap"],
    "product_detail": ["add_to_cart", "variant_select"],
    "cart": ["quantity_change", "checkout_start"],
    "delivery_address": ["address_select"],
    "payment_method": ["payment_select"],
    "order_review": ["place_order"],
    "order_confirmation": ["order_completed"],
    "payment_failure": ["payment_failed", "retry_payment"],
    "login": ["login_submit"],
    "otp_verify": ["otp_submit"],
    "signup": ["signup_submit"],
    "refund_request": ["refund_submit"],
    "contact_support": ["ticket_submit"],
}

# Tags the app emits that the taxonomy does not know about. Every real export has
# these; they are the naming_consistency and orphan_rate signal.
DRIFT_TAGS = ["HomeV2", "LegacyCheckout", "ExperimentalPLP", "DebugScreen"]

APP_VERSIONS = ["8.2.0", "8.2.1", "8.3.0"]
CRASH_TITLES = [
    "NullPointerException in CartAdapter.onBind",
    "IndexOutOfBoundsException in ProductListView",
    "OutOfMemoryError decoding product image",
    "IllegalStateException in PaymentFragment",
]


def _drifted_journeys() -> list[tuple[float, list[str]]]:
    """A plausible next-quarter world, for demonstrating drift detection.

    Three simultaneous changes of the kind that actually happen: a new payment
    route ships and takes share, payment failures spike after a gateway change,
    and the classic search-to-purchase path loses traffic to it.
    """
    shifted: list[tuple[float, list[str]]] = []
    for weight, path in JOURNEYS:
        if path[-1] == "order_confirmation":
            weight *= 0.35                      # traffic moves to the new route
        elif path[-1] == "payment_failure":
            weight *= 4.0                       # gateway regression
        shifted.append((weight, path))
    # The new wallet route, which did not exist in the baseline at all.
    shifted.append((0.11, ["home", "product_detail", "cart", "saved_cards",
                           "order_review", "order_confirmation"]))
    return shifted


def build_sample(tag_for_screen: dict[str, str], sessions: int, seed: int,
                 inject_pii: bool = False,
                 drift: bool = False) -> tuple[list[dict], list[dict], list[dict]]:
    rng = random.Random(seed)
    journeys = _drifted_journeys() if drift else JOURNEYS
    weights = [w for w, _ in journeys]
    paths = [p for _, p in journeys]

    events: list[dict] = []
    crashes: list[dict] = []
    sentry_errors: list[dict] = []
    now = datetime.now(timezone.utc)

    for i in range(sessions):
        session_id = f"sess-{i:06d}"
        user_id = None if rng.random() < 0.18 else f"user-{rng.randint(1, sessions // 3 + 1):05d}"
        # Spread over the trailing 30h, ending minutes ago, so the freshness
        # monitor sees a live feed rather than a stale dump.
        clock = now - timedelta(seconds=rng.randint(300, 30 * 3600))
        app_version = rng.choice(APP_VERSIONS)
        journey = rng.choices(paths, weights=weights, k=1)[0]
        session_start = clock

        def emit(name: str, tag: str, when: datetime, extra: dict) -> None:
            events.append({
                "event_name": name,
                "screen_tag": tag,
                "session_id": None if rng.random() < 0.02 else session_id,
                "user_id": user_id,
                "app_version": app_version,
                "timestamp": when.isoformat(),
                "properties": extra,
            })

        for screen_id in journey:
            tag = tag_for_screen.get(screen_id)
            if tag is None:
                continue
            clock += timedelta(seconds=rng.randint(2, 45))
            emit("screen_view", tag, clock, {"screen_tag": tag})

            for action in ACTIONS.get(screen_id, []):
                if rng.random() < 0.6:
                    clock += timedelta(seconds=rng.randint(1, 20))
                    props = _props(action, rng)
                    if inject_pii and action == "ticket_submit":
                        # A policy violation of the exact shape the scanner exists
                        # to catch: free-text support payload carrying contact data.
                        props["email"] = f"user{rng.randint(1,999)}@example.com"
                        props["message"] = (
                            f"Call me on +44 7700 900{rng.randint(100, 999)}"
                        )
                    emit(action, tag, clock, props)

        # ~3% of sessions touch a screen the taxonomy has never heard of.
        if rng.random() < 0.03:
            clock += timedelta(seconds=rng.randint(2, 30))
            drift = rng.choice(DRIFT_TAGS)
            emit("screen_view", drift, clock, {"screen_tag": drift})

        # ~1.2% of sessions crash, ~0.8% log a non-fatal error.
        if rng.random() < 0.012:
            when = session_start + (clock - session_start) * rng.uniform(0.3, 0.95)
            crashes.append({
                "event_timestamp": int(when.timestamp() * 1_000_000),
                "issue_title": rng.choice(CRASH_TITLES),
                "error_type": "ANR" if rng.random() < 0.25 else "FATAL",
                "is_fatal": True,
                "application": {"session_id": session_id},
                "user_id": user_id,
                "app_version": app_version,
                "os_version": rng.choice(["13", "14", "15"]),
            })
        if rng.random() < 0.008:
            when = session_start + (clock - session_start) * rng.uniform(0.2, 0.9)
            sentry_errors.append({
                "timestamp": when.isoformat(),
                "level": rng.choice(["error", "warning"]),
                "title": "HttpException: 503 from /v2/orders",
                "tags": {"session_id": session_id, "release": app_version},
                "user": {"id": user_id},
            })

    return events, crashes, sentry_errors


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--taxonomy", default="config/taxonomy.yaml")
    ap.add_argument("--out", default="artifacts/sample-events.jsonl")
    ap.add_argument("--crashes-out", default="artifacts/sample-crashlytics.jsonl")
    ap.add_argument("--sentry-out", default="artifacts/sample-sentry.jsonl")
    ap.add_argument("--sessions", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=20260803)
    ap.add_argument("--inject-pii", action="store_true",
                    help="Plant a deliberate policy violation for the PII scanner")
    ap.add_argument("--drift", action="store_true",
                    help="Simulate a shifted world: new payment route, failure spike")
    args = ap.parse_args()

    from goldenflow.phase0.taxonomy import Taxonomy

    taxonomy = Taxonomy.from_yaml(args.taxonomy)
    tag_for_screen = {
        s.screen_id: s.analytics_tags[0] for s in taxonomy.screens if s.analytics_tags
    }

    events, crashes, errors = build_sample(
        tag_for_screen, args.sessions, args.seed, args.inject_pii, args.drift
    )

    _write(Path(args.out), events)
    _write(Path(args.crashes_out), crashes)
    _write(Path(args.sentry_out), errors)

    reached = {e["screen_tag"] for e in events}
    print(f"Wrote {len(events):,} synthetic events across {args.sessions:,} sessions")
    print(f"  events    -> {args.out}")
    print(f"  crashes   -> {args.crashes_out}  ({len(crashes):,} signals)")
    print(f"  errors    -> {args.sentry_out}  ({len(errors):,} signals)")
    print(f"  {len(reached)} distinct screen tags emitted "
          f"({len(tag_for_screen)} screens in taxonomy)")
    if args.inject_pii:
        print("  NOTE: PII deliberately injected into ticket_submit properties")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
