# The Naming Contract

**Status:** Phase 0 deliverable 0.2 · CI-enforced · owned by QE Architecture

---

## Why this document exists

GoldenFlow compares two things:

- what users **did** in production — a sequence of screens, reconstructed from analytics
- what automation **tests** — a sequence of screens, extracted from the Appium suite

Those can only be compared if both sides agree on what a screen *is*. Nothing else in
the product works without that agreement, and no amount of downstream cleverness
recovers from its absence.

The contract binds three vocabularies owned by three different teams:

```
analytics screen tag   ->   canonical screen ID   ->   Page Object class
(mobile engineering)        (GoldenFlow)               (QE automation)
        "PDP"          ->     product_detail      ->   ProductDetailScreen
```

The canonical `screen_id` is the join key. It is the only one of the three that
must never change casually.

---

## Why a violation is worse than a crash

Every rule in [config/taxonomy.yaml](../config/taxonomy.yaml) exists because breaking it
**corrupts data silently instead of failing loudly.**

The clearest example: if the analytics tag `Checkout` is mapped to two different
screens, nothing errors. Two genuinely distinct user journeys merge into one. The
mined process model looks plausible. Coverage analysis reports gaps that do not
exist and misses gaps that do. Nobody notices for months, and when they do, every
conclusion drawn in the interim is suspect.

That is why this is enforced in CI from Phase 0 rather than validated at analysis
time in Phase 2. By Phase 2 the corrupt data already exists.

---

## The rules

Run `goldenflow taxonomy validate config/taxonomy.yaml`. It exits non-zero on any ERROR.

| Rule | Severity | Rationale |
|---|---|---|
| `screen_id` is snake_case | ERROR | It is a join key; inconsistent casing produces silent mismatches |
| `screen_id` is unique | ERROR | Primary key |
| An analytics tag maps to exactly one screen | ERROR | Ambiguity merges distinct journeys without failing |
| A Page Object maps to exactly one screen | ERROR | Coverage attribution would be ambiguous |
| `domain` is in the declared list | ERROR | Uncontrolled domains break reporting rollups |
| Sensitive screens declare occlusion | ERROR | Session replay would record PII — see [pii-policy.md](pii-policy.md) |
| Critical screens have a Page Object | ERROR | Phase 4 cannot resolve their journey steps |
| Every screen has ≥1 analytics tag | WARNING | Without one it can never appear in a mined journey |
| Non-critical screens have a Page Object | WARNING | A backlog item, not a blocker |

The severity split is deliberate: **strict about anything that corrupts a join,
lenient about anything that merely represents work not yet done.** A taxonomy that
errors on incomplete automation coverage would be unadoptable on day one.

---

## Governance

### Adding a screen

1. Mobile engineering adds the screen tag to the app and opens a taxonomy PR in the
   same change. A screen instrumented but not declared shows up as a
   `naming_consistency` deduction on the next readiness audit.
2. QE automation adds the `page_objects` binding when the Page Object exists. Until
   then the screen carries an `automatable` warning — visible, tracked, not blocking.
3. Set `sensitivity` honestly. If in doubt, escalate. Over-masking costs replay
   fidelity; under-masking is a privacy incident.

### Changing a screen

| Change | Impact | Process |
|---|---|---|
| `display_name`, `notes` | None — excluded from the fingerprint | Normal PR |
| Add an `analytics_tag` | Additive; old journeys still resolve | Normal PR |
| Remove/rename an `analytics_tag` | **Breaking** — historical events stop resolving | Architecture review; keep the old tag as an alias for ≥2 releases |
| Rename a `screen_id` | **Breaking** — invalidates every stored journey and coverage mapping | Architecture review + Phase 2 re-mine + Phase 3 re-map |
| Raise `sensitivity` | Requires an app change to add occlusion | Ship occlusion first, then the taxonomy |

Aliasing is why `analytics_tags` is a list. Migrating `PDP` → `ProductDetail` means
carrying both for two releases so journeys spanning the migration stay intact.

### Drift between repos

The taxonomy fingerprint (`goldenflow taxonomy summary`) is a content hash over the
join keys only. Both the app repo and the automation repo must pin the same
fingerprint. If they diverge, every join degrades quietly — so CI in both repos
compares its pinned fingerprint against the canonical taxonomy and fails on
mismatch.

Cosmetic fields are excluded from the hash so that documentation improvements do
not trigger a false drift alarm.

---

## Naming conventions

**Canonical screen IDs** — `snake_case`, functional not visual:

- good: `product_detail`, `otp_verify`, `payment_failure`
- bad: `ProductDetail` (casing), `screen_3` (meaningless), `red_checkout_button_page` (visual and not a screen)

Name the *function*, not the layout. A redesign should not force a rename, because
a rename invalidates history.

**Domains** — coarse functional areas, ~5–8 total. Used for reporting rollups and
Phase 2 journey grouping. Too many and rollups become useless.

**Page Objects** — whatever the automation repo already uses. The contract adapts to
the existing suite rather than demanding a refactor; the taxonomy exists to describe
reality, not to relitigate it.

---

## Enforcement

```yaml
# .github/workflows/taxonomy.yml — required in BOTH repos
- run: goldenflow taxonomy validate config/taxonomy.yaml
```

Unenforced, this document is a suggestion, and the join it protects degrades one
well-intentioned PR at a time.
