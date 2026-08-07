# UXCam Integration

**Decision:** UXCam is the analytics and session-replay vendor. A CleverTap migration
was evaluated and reversed.

---

## Why UXCam won

The deciding property is **auto-capture**. UXCam records taps, gestures and screens
with no per-interaction instrumentation, which means:

- Journeys exist from the moment the SDK ships, not after the app team writes and
  maintains hundreds of `logEvent` call sites
- Phase 0's readiness audit passes on integration rather than after an
  instrumentation project
- GoldenFlow does not become a change request against a mobile roadmap it does not
  control

That last point is the product thesis. GoldenFlow's premise is *learn from production
without asking the app team for work*. A vendor requiring manual instrumentation
inverts that.

### What was evaluated and rejected

CleverTap covers the analytics role well — `CT Session ID` on every event, an
identity model, a bulk Get Events export. It captures **no UI interactions at all**
without explicit code; its "automatic" events are lifecycle-level only (App Launched,
Session Concluded). Measured impact of the difference:

```
                          readiness   action_coverage   journeys mined
screens + taps              89.2% GO        53.6%          59 variants
screens only, no taps       81.2% GO         0.0%          59 variants
```

Journeys survive either way — GoldenFlow mines screen sequences, not taps. What
collapses is **business-value scoring**: revenue attributable to journeys drops to
zero, because `order_value` and `cart_value` ride on interaction events. Separating
*most-walked* from *most-valuable* is the product's central claim, and half its
evidence comes from interaction properties.

### The price, stated plainly

Session replay records real users' screens. Occlusion becomes mandatory, and it
**fails open** — a forgotten `occludeSensitiveScreen()` is a silent privacy incident
rather than a visible data gap. [docs/pii-policy.md](pii-policy.md) exists to
compensate, and the 1%-rollout replay review is the control that actually catches it.

This is a real cost accepted deliberately, not an oversight.

---

## What GoldenFlow reads

Verified against UXCam's own API documentation, not third-party summaries.

### Events endpoint

[`developer.uxcam.com/docs/events-endpoint`](https://developer.uxcam.com/docs/events-endpoint)

```json
{
  "success": true,
  "data": [{
    "eventId": "...", "eventName": "add_to_cart",
    "eventScreen": "Cart",
    "eventDate": "2026-08-04T15:30:45Z",
    "url": "https://app.uxcam.com/session/...",
    "sessionProperty": {"sessionId": "...", "hasVideo": true, "durationSec": 98.8},
    "userProperty": {"kUXCam_UserIdentity": "user_123"},
    "eventProperty": {"amount": 29.99},
    "device": {"appVersion": "8.2.0", "platform": "android", "deviceId": "..."}
  }]
}
```

| UXCam field | GoldenFlow | Why it matters |
|---|---|---|
| `eventScreen` | `screen_id` (via taxonomy) | **Auto-tagged on every event.** The screen sequence falls out of ordering a session — no per-screen instrumentation needed |
| `sessionProperty.sessionId` | `session_id` | Groups events into one journey |
| `eventDate` | `timestamp` | ISO 8601, orders the sequence |
| `userProperty.kUXCam_UserIdentity` | `user_id` | Identity, once logged in |
| `device.deviceId` | `anonymous_id` | Device scope, present pre-login |
| `url` | `uxcam_session_url` property | **Deep link to the replay** |

### The session URL is the sleeper feature

Every event carries a link to the recording. Carried through the pipeline, that means
a mined journey, a Phase 3 coverage gap, or a Phase 5 pull request can point a
reviewer at *a real user walking the path*.

"Here is a journey 72 sessions take that no test covers" is an argument. "…and here is
one of them, on video" ends it.

### Sessions endpoint

[`developer.uxcam.com/docs/sessions-1`](https://developer.uxcam.com/docs/sessions-1)
adds session-level signal GoldenFlow uses for risk weighting: `isCrashed`,
`rageGestureCount`, `totalGesture`, `uniqueScreensCount`, `durationSec`.

---

## Free failure signals

UXCam raises **Rage Tap** and **UI Freeze** automatically. These are the signals no
crash reporter provides: a user jabbing a dead button six times never crashes the app
and never appears in Crashlytics, but it is one of the strongest indicators of a
broken screen there is.

Routed into the risk pipeline by
[`from_uxcam`](../goldenflow/phase1/risk.py), so they feed Golden Journey risk
weighting alongside crashes:

```bash
goldenflow ingest --events uxcam-events.jsonl --source uxcam \
    --uxcam-signals uxcam-events.jsonl \
    --taxonomy config/taxonomy.yaml --store artifacts/events.db
```

Same file twice is intentional: UXCam mixes journey steps and failure signals in one
stream, and the adapter returns `None` for the former.

---

## What UXCam does not give you

**Raw individual taps are not queryable.** They live in the replay *video*, not the
Events API. What the API exposes is named `logEvent` calls plus the auto-captured
signals above, each tagged with `eventScreen`. That is sufficient for journey mining,
which operates on screen sequences — but if you expected to query "every tap at
coordinate X", you cannot.

**Screen tagging still needs one line per screen.** Auto-tagging yields class names
(`MainActivity`, `CheckoutFragment`) which are useless as a join key and churn on
every refactor. `UXCam.tagScreenName("Cart")` is required for the taxonomy contract
to hold — roughly 30 lines across an app, once.

**Session replay is sampled on lower plans.** You get a percentage of sessions, not
all of them. Confirm the sampling rate for your tier before treating journey
frequencies as population figures.

---

## Integration checklist

- [ ] Add the SDK and `UXCam.startWithConfiguration()` in `Application.onCreate` /
      `AppDelegate`
- [ ] `UXCam.optIntoSchematicRecordings()` — required for recording
- [ ] `UXCam.tagScreenName()` on every screen, matching `config/taxonomy.yaml`
- [ ] **Occlusion on every `high` and `critical` screen** — see
      [pii-policy.md](pii-policy.md)
- [ ] Disable location capture
- [ ] Verify the kill switch before the 1% rollout step
- [ ] **Watch replays at 1% before advancing** — the only control that catches an
      unmasked screen
- [ ] Sign the DPA; confirm the per-user deletion API exists
- [ ] Run `goldenflow readiness audit` against a real UXCam export

---

Sources:
[Data Access API](https://developer.uxcam.com/docs/data-access-api) ·
[Events Endpoint](https://developer.uxcam.com/docs/events-endpoint) ·
[Sessions](https://developer.uxcam.com/docs/sessions-1) ·
[Events (SDK)](https://developer.uxcam.com/docs/events)
