# PII & Session Replay Occlusion Policy

**Status:** Phase 0 deliverable 0.4 · blocks SDK rollout · owned by Privacy + QE Architecture

**Analytics vendor: UXCam.** Session replay is in scope, so occlusion is mandatory
and this policy is load-bearing rather than advisory. A CleverTap variant of this
document existed briefly; the decision to keep UXCam reinstates everything below.

---

## The exposure

Session replay records what is on the user's screen in production. Not a mock, not a
staging fixture — a real person's actual card number, one-time code, home address,
and order history.

UXCam session objects also carry `location.latitude` / `location.longitude`, and user
objects carry whatever custom properties the app attaches. Together with the video
that is a re-identifiable record of individual behaviour.

**No SDK ships to production traffic until this policy is signed off.** Retrofitting
occlusion after a rollout does not help: the data already captured is already
captured, and deleting it is a breach-response exercise, not a fix.

### Why this is the price of auto-capture

UXCam requires almost no instrumentation — taps, gestures and screens are captured
without the app team writing event code, which is precisely why it was chosen. The
same mechanism that makes journeys free makes occlusion mandatory. You do not get one
without the other.

The failure mode is the inverse of a manual-instrumentation vendor's:

| | Forgotten call | Consequence |
|---|---|---|
| Manual instrumentation | `logEvent(...)` | Missing data. Loud, measurable in the readiness audit. |
| **UXCam** | **`occludeSensitiveScreen(...)`** | **Silent privacy incident.** A vendor now stores video of someone typing a card number. |

Occlusion **fails open**. Everything in this document exists to compensate for that.

---

## Sensitivity levels

Declared per screen in [config/taxonomy.yaml](../config/taxonomy.yaml) and enforced by
`goldenflow taxonomy validate`, which errors if a sensitive screen does not declare
occlusion.

| Level | Meaning | Requirement |
|---|---|---|
| `none` | No personal data | Record normally |
| `low` | Behavioural only, not identifying | Record normally; review if joined to identity |
| `high` | Personal data present | **Field-level occlusion** of every PII element |
| `critical` | Credentials, payment, health, government ID | **Full-screen occlusion** |

### Why `critical` means the whole screen

Field-level masking **fails open**. Someone adds a field, forgets the occlusion
annotation, and it silently starts recording. On a payment or credential screen the
first evidence of that mistake is the data already sitting in a vendor's storage.

Full-screen occlusion fails closed. A new field on an already-masked screen is masked
by default. On screens where the downside is a reportable breach, default-safe beats
fidelity — and it is not close.

---

## Screens currently classified `critical`

| Screen | Why |
|---|---|
| `login`, `signup` | Credential entry |
| `otp_verify` | One-time codes must never reach replay storage |
| `payment_method` | Card entry |
| `saved_cards` | Stored instruments |

Screens classified `high` — delivery address, order review/confirmation, order
history, profile edit, address book, account deletion, data export, refund request —
carry field-level occlusion of names, addresses, contact details, and order values.

---

## Implementation

**Android**
```java
UXCam.occludeSensitiveScreen(true);            // critical: whole screen
UXCam.occludeSensitiveView(cardNumberField);   // high: specific fields
```

**iOS**
```swift
UXCam.occludeSensitiveScreen(true)
UXCam.occludeSensitiveView(cardNumberField)
```

Prefer declaring occlusion in the **screen's own lifecycle** (`onResume` /
`viewWillAppear`) rather than centrally. Central configuration drifts out of sync with
navigation changes; screen-local configuration travels with the screen when it moves.

### Screen tagging is still required

Auto-tagging gives class names — `MainActivity`, `CheckoutFragment` — which are
useless as a join key and change on every refactor. Tag explicitly so the taxonomy
contract holds:

```java
UXCam.tagScreenName("Cart");   // must match config/taxonomy.yaml
```

### Never log as event properties

Instrumenting an interaction is not permission to attach its payload. Event properties
are the most common accidental PII channel because they feel like telemetry, not data.

```java
// WRONG - PII in an event property
UXCam.logEvent("checkout", Map.of("email", user.email, "address", addr));

// RIGHT - reference by identifier, resolve server-side under access control
UXCam.logEvent("checkout", Map.of("order_id", orderId, "item_count", 3));
```

Same rule for network instrumentation: log the **path**, never the full URL — query
strings carry tokens and identifiers.

```java
"endpoint", request.url().encodedPath()   // "/v2/orders"   yes
"endpoint", request.url().toString()      // "?token=..."   no
```

### Location

UXCam attaches `location.latitude` / `location.longitude` to session records. Disable
it unless the product genuinely needs it — GoldenFlow does not, since journeys are
screen sequences. GoldenFlow's ingestion does not carry location into the event store,
but that is defence in depth, **not** a substitute: the data still exists in UXCam.

---

## PR review checklist

Required on any PR that adds or modifies a screen:

- [ ] Screen declared in `config/taxonomy.yaml` with an honest `sensitivity`
- [ ] `UXCam.tagScreenName()` matches the canonical screen ID
- [ ] `critical` → `occludeSensitiveScreen(true)` in the screen's lifecycle
- [ ] `high` → every PII view individually occluded
- [ ] No PII in any `logEvent` property
- [ ] Network instrumentation logs paths, not full URLs
- [ ] **Manually verified in a replay session, not assumed from the code**

The last item is not optional. Occlusion is configuration; configuration is wrong
until observed working.

Verified automatically for the event stream:

```bash
goldenflow pii scan --store artifacts/events.db
```

The scanner Luhn-validates card numbers so a 16-digit order ID does not cry wolf, and
**findings never quote the matched value** — a PII report that contains PII gets
pasted into tickets and CI logs. It cannot inspect the video; only the replay review
above can.

---

## Ongoing controls

**Sampled replay audits.** Every release, a human watches 20 sampled replays covering
each `critical` and `high` screen and confirms masking held. This is the only control
that catches an occlusion regression, because no automated scan can see the video.

**Sampled event audits.** Every release, scan 1,000 sampled sessions for PII in event
properties. Phase 1's exit criterion is zero detections.

**Retention.** Replay retention set to the shortest window that supports the QE use
case. GoldenFlow mines *journeys* (screen sequences), which are derived and retained
separately from the replay video. Once a journey is mined, the underlying replay has
no further value to this product and should age out on the vendor's schedule.

**Residency.** EU/UK and India traffic pinned to in-region processing where the vendor
supports it. Confirm per-vendor during Phase 0 procurement, not after.

**DPA.** Signed with UXCam and the crash-reporting vendor before rollout.

**Deletion.** A user's erasure request must propagate to UXCam. Verify the vendor
exposes a per-user deletion API before selecting them — the `account_delete` journey
is in the protected test registry precisely because this path must keep working.

---

## Rollout gating

Staged rollout with a remote-config kill switch: **1% → 10% → 50% → 100%**, holding at
each step for battery, app size, ANR and crash-rate regressions.

The kill switch must be verified working **before** the 1% step. A kill switch that
has never been exercised is a hypothesis, not a control.

At the 1% step, **watch replays before advancing.** That is the cheapest moment to
discover an unmasked screen, and the only one where the exposure is bounded.
