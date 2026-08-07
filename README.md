# GoldenFlow AI

**A Self-Evolving Mobile QE Agent** â€” Learning from Production. Evolving Quality.

Test suites are authored once from requirements and assumed behaviour. Production
diverges. Nothing carries that divergence back into QA, so suites decay silently
while dashboards stay green.

GoldenFlow closes the loop: production telemetry â†’ Golden Journeys â†’ diff against QA
assets â†’ generated and validated automation â†’ measured back against escaped defects.

Technically this is **process mining + conformance checking** applied to mobile QE.
Deterministic algorithms do the analysis; LLMs do semantic interpretation and code
generation. That split is what makes the system auditable.

---

## Status

| Phase | Name | Status |
|---|---|---|
| **0** | **Foundation & Readiness** | **Implemented** |
| **1** | **Data Foundation** | **Implemented** |
| **2** | **Journey Intelligence Engine** | **Implemented** |
| **3** | **QA Asset Graph & Gap Analysis** | **Implemented** |
| **4** | **Locator Resolution** | **Implemented** |
| **5** | **Generation & Validation** | **Implemented** (gate blocks â€” no device farm) |
| **6** | **Closed Loop & Governance** | **Implemented** |
| **7** | **Multi-App Scale & Productization** | **Implemented** |

All eight phases implemented. **512 tests.** `python tools/demo.py --fast` runs the
whole thing in 54 steps.

Full plan: [ROADMAP.md](ROADMAP.md) Â· Architecture: [goldenflow-architecture.svg](goldenflow-architecture.svg) Â· Timeline: [goldenflow-roadmap.svg](goldenflow-roadmap.svg)

---

## Install

```bash
pip install -e ".[dev]"
```

Requires Python 3.11+. Phase 0 depends only on `pydantic` and `pyyaml`.

---

## Phase 0 â€” Foundation & Readiness

The phase most similar products skip, and the reason most of them fail. GoldenFlow's
value depends entirely on instrumentation quality it does not control. Phase 0 finds
that out in month 2 for the cost of a sample export, rather than in month 9 after the
data platform is already built.

### Run the M2 gate

```bash
goldenflow phase0 gate \
  --taxonomy config/taxonomy.yaml \
  --events   artifacts/sample-events.jsonl \
  --registry config/protected-tests.yaml \
  --baseline artifacts/baseline-8.2.0.json
```

```
Phase 0 Exit Criteria - M2 Gate
======================================================================
  [PASS]  Taxonomy contract            28 screens, 0 error(s)
  [PASS]  Instrumentation readiness    85.1% vs gate 70.0% - GO
  [PASS]  Protected test registry      11 entries, 0 stale
  [PASS]  Baseline snapshot            captured
======================================================================
VERDICT: GO - proceed to Phase 1 (Data Foundation)
```

Exits non-zero on NO-GO, so it drops straight into CI.

### The four deliverables

**1. Screen taxonomy** â€” [`config/taxonomy.yaml`](config/taxonomy.yaml) Â· [`goldenflow/phase0/taxonomy.py`](goldenflow/phase0/taxonomy.py)

The join key between production journeys and QA assets, binding three vocabularies:

```
analytics screen tag  ->  canonical screen ID  ->  Page Object class
      "PDP"           ->    product_detail     ->  ProductDetailScreen
```

Every rule is CI-enforced because violations **corrupt the join silently rather than
failing loudly**. A tag mapped to two screens doesn't error â€” it quietly merges two
journeys into one and nobody notices for months.

```bash
goldenflow taxonomy validate config/taxonomy.yaml
```

Severity is deliberately split: strict about anything that corrupts a join, lenient
about anything that merely represents work not yet done. Governance:
[docs/naming-contract.md](docs/naming-contract.md).

**2. Instrumentation readiness audit** â€” [`goldenflow/phase0/readiness.py`](goldenflow/phase0/readiness.py)

Scores a production event sample across six weighted dimensions and issues the M2
go/no-go.

| Dimension | Weight | Measures |
|---|---|---|
| `screen_coverage` | 0.25 | Declared screens actually emitting events |
| `session_integrity` | 0.20 | Session ID presence and event ordering |
| `action_coverage` | 0.15 | Screens emitting interaction events, not just navigation |
| `identity_stability` | 0.15 | User ID presence and consistency |
| `naming_consistency` | 0.15 | Observed tags resolving to the taxonomy |
| `automation_binding` | 0.10 | Screens with a Page Object binding |

A **blocking defect fails the gate regardless of the aggregate**. An app can score 78%
overall while having session IDs so broken no journey can ever be reconstructed â€”
averaging hides that, and this audit exists precisely to not hide it.

```bash
goldenflow readiness audit --taxonomy config/taxonomy.yaml --events events.jsonl
```

Output includes a remediation plan ordered by weighted impact.

**3. Baseline metrics** â€” [`goldenflow/phase0/baseline.py`](goldenflow/phase0/baseline.py)

Phase 6 claims a measured reduction in escaped defects. That claim is only defensible
against a baseline captured *before* any GoldenFlow work began. Snapshots are
**write-once and checksummed**, so tampering after the fact is detectable by someone
who was not in the room.

```bash
goldenflow baseline capture --app acme-shop --release 8.2.0 --out artifacts/baseline.json \
  --coverage 62 --escaped-defects 14 --mttd 36 --suite-runtime 180
goldenflow baseline compare artifacts/baseline-8.2.0.json artifacts/baseline-9.4.0.json
```

Comparison is direction-aware â€” a shrinking suite is a win in Phase 5 and a loss in
Phase 1, and the tool knows which.

**4. Protected test registry** â€” [`config/protected-tests.yaml`](config/protected-tests.yaml) Â· [`goldenflow/phase0/registry.py`](goldenflow/phase0/registry.py)

The guardrail against the failure mode most likely to cause real harm.

Phase 5 ranks deletion candidates by production traffic. That signal is **actively
inverted** for the tests that matter most: account deletion, refunds, payment failure
recovery, GDPR export, accessibility, regulatory flows. All low-volume by design,
all high-consequence by nature. A traffic-ranked pruner deletes the safety-critical
coverage first, and the result looks like a success metric â€” suite size down, runtime
down, pass rate up.

This registry exists in Phase 0, months before anything can propose a deletion, so
the guardrail is never retrofitted onto a system that has already caused harm.

```bash
goldenflow registry check config/protected-tests.yaml
goldenflow registry test  config/protected-tests.yaml tests/account/delete/test_erasure.py
```

Phase 5's deletion path calls `assert_deletable()`, which **raises** rather than
returning a boolean â€” omitting the check should be loud, not silent.

---

---

## Phase 1 â€” Data Foundation

Builds the single source of truth. Everything downstream reads from here and nowhere
else â€” four vendor APIs queried at analysis time is what kills these systems in
production.

### Run the pipeline end to end

```bash
python tools/generate_sample_events.py --sessions 6000

goldenflow ingest --events artifacts/sample-events.jsonl \
  --taxonomy config/taxonomy.yaml --store artifacts/events.db \
  --crashlytics artifacts/sample-crashlytics.jsonl \
  --sentry artifacts/sample-sentry.jsonl

goldenflow phase1 gate --store artifacts/events.db \
  --taxonomy config/taxonomy.yaml --plan config/tracking-plan.yaml
```

```
Phase 1 Exit Criteria - M5 Gate
==============================================================================
  [PASS]  Tracking plan contract     22 events, 0 error(s)
  [PASS]    freshness                newest event is 0.1h old (limit 4.0h)
  [PASS]    session_id_presence      1.92% of events have no session_id
  [PASS]    orphan_screen_tags       0.33% carry a tag absent from the taxonomy
  [PASS]    screen_coverage          28/28 declared screens seen
  [PASS]    plan_conformance         98.1% of events honour the tracking plan
  [PASS]  PII scan                   51,547 events scanned, 0 finding(s)
  [PASS]  Risk signals joined        121 crash/error signals in store
==============================================================================
VERDICT: GO - proceed to Phase 2 (Journey Intelligence Engine)
```

### The six components

**Tracking plan** â€” [`config/tracking-plan.yaml`](config/tracking-plan.yaml) Â· [`tracking_plan.py`](goldenflow/phase1/tracking_plan.py)

An event schema contract reviewed like an API. Two separate validations: is the
*contract* coherent, and does *observed traffic* honour it? The second runs
continuously â€” a contract nobody checks against reality is documentation.

```bash
goldenflow tracking validate config/tracking-plan.yaml
goldenflow tracking conformance --plan config/tracking-plan.yaml \
  --taxonomy config/taxonomy.yaml --events artifacts/sample-events.jsonl
```

**Ingestion** â€” [`ingest.py`](goldenflow/phase1/ingest.py)

Adapters for RudderStack, the Firebase BigQuery export, Mixpanel and flat JSON, all
converging on one canonical event. Phase 2 never learns that three vendors exist.

Two deliberate choices: unresolvable screen tags are **ingested as orphans, not
dropped** (dropping hides the drift the monitors exist to measure), and events with
no usable timestamp are **rejected** (defaulting to now corrupts every sequence they
touch).

**Event store** â€” [`store.py`](goldenflow/phase1/store.py)

Canonical model plus warehouse DDL, partitioned and clustered for the access pattern
Phase 2 actually has â€” every event for a session, time-ordered, over a bounded date
range. Event IDs are **content-addressed**, so at-least-once delivery doesn't inflate
journey frequencies.

```bash
goldenflow store ddl --dialect bigquery     # or clickhouse, sqlite
```

SQLite is the runnable reference implementation so every downstream phase has
something real to read on day one; BigQuery/ClickHouse are the deployment targets.

**Risk signals** â€” [`risk.py`](goldenflow/phase1/risk.py)

Crashlytics and Sentry joined on session ID and **positioned in the journey
timeline** â€” the difference between a stack trace and a testable scenario. A signal
is attributed to the *preceding* screen, because a crash has no next event: the app
died. Signals that can't be placed are left unlocated rather than guessed at; a
mislocated crash corrupts the risk weighting of an unrelated journey.

`localisation_rate` measures session-ID propagation from the analytics SDK into the
crash reporter â€” the most common Phase 1 integration defect, and invisible unless
measured.

**PII scanner** â€” [`pii.py`](goldenflow/phase1/pii.py)

Enforcement for [docs/pii-policy.md](docs/pii-policy.md). Policy that isn't scanned
for is policy that isn't followed.

```bash
goldenflow pii scan --store artifacts/events.db
```

Card numbers are Luhn-validated so a 16-digit order ID doesn't trigger a false alarm
â€” a scanner people learn to ignore protects nobody. **Findings never contain the
matched value**; a PII report that quotes the PII it found is itself a leak, because
it gets pasted into tickets and CI logs.

**Quality monitors** â€” [`quality.py`](goldenflow/phase1/quality.py)

The firewall before Phase 2. Bad input doesn't make a mining pipeline fail â€” it makes
it produce a plausible-looking process model with holes nobody can see.

| Monitor | Guards against |
|---|---|
| `freshness` | Silent pipeline stall |
| `session_id_presence` | Events that can't join any journey |
| `orphan_screen_tags` | Tag drift dropping events from journeys |
| `screen_coverage` | Screens invisible to mining |
| `session_ordering` | Out-of-order sequences (warn) |
| `duplicate_events` | Inflated journey frequency (warn) |
| `plan_conformance` | Schema drift from the contract |

---

## Phase 2 â€” Journey Intelligence Engine (Agent 1)

The research core. Production telemetry in, Golden Journeys out.

```bash
goldenflow journeys mine --store artifacts/events.db \
    --taxonomy config/taxonomy.yaml --top 5
```

```
  variants       : 45 distinct paths, entropy 3.475 bits
  14 variants explain 95% of sessions
  archetypes     : 18 archetypes from 45 variants (compression 2.5x)

#1  Splash to Purchase                      [score 95.2]
    path: splash -> home -> search -> product_detail -> cart -> ... -> order_confirmation
    freq 0.89 | value 1.00 | risk 0.94 | exposure 1.00

#4  Splash to Product Detail                [score 70.0]
    381 sessions (25.4% of traffic)
    freq 1.00 | value 0.00 | risk 1.00 | exposure 1.00
```

**Read #4 carefully.** It is the most-walked path in the app â€” 25.4% of all traffic,
frequency 1.00 â€” and it ranks *fourth*. Browse-and-leave carries the volume; checkout
carries the value. A frequency-ranked suite aims at the wrong target, confidently.
That inversion is the entire product thesis, and it is visible in the output.

### The pipeline

```
events -> sessionize -> mine (DFG + variants) -> cluster -> score -> name
                                                    |
                                                    +-> drift (vs. baseline)
                                                    +-> graph (Neo4j)
```

**Sessionization** â€” [`sessionize.py`](goldenflow/phase2/sessionize.py). Consecutive
duplicate screens collapse, because SDKs emit `screen_view` on every re-render and
`Home â†’ Home â†’ Home â†’ Cart` would otherwise be a distinct variant from `Home â†’ Cart`.
That single decision is the difference between 50 archetypes and 5,000.

**Mining** â€” [`mining.py`](goldenflow/phase2/mining.py). Directly-follows graph,
variants, coverage curve, and distribution entropy. The DFG is implemented directly
because Phase 3 reasons about its edges; PM4Py is used for what it is genuinely good
at â€” Inductive Miner discovery of a *sound* model â€” and that bridge is optional.

**Clustering** â€” [`clustering.py`](goldenflow/phase2/clustering.py). Normalised LCS
similarity, because an extra screen en route to checkout is still the checkout
journey. Critical screens keep their own archetype (see below).

**Scoring** â€” [`scoring.py`](goldenflow/phase2/scoring.py). Weighted **sum**, not
product: multiplying would zero a high-value journey that happens to have no
recorded crashes. Every score carries its component breakdown â€”
`goldenflow journeys explain aj_001`.

**Drift** â€” [`drift.py`](goldenflow/phase2/drift.py). Four independent measures, each
naming its biggest movers:

```
  [WARN ] js_divergence        0.1023  overall shift in journey distribution
           - home -> product_detail -> cart -> saved_cards: 0.0% -> 8.8% (up 8.8pp)
           - splash -> home -> search -> product_detail: 12.1% -> 3.5% (down 8.7pp)
  [ALERT] population_stability 2.4451
  [WARN ] novel_variants       9.9200  9.9% of traffic on 24 unseen path(s)
```

"drift = 0.31" tells a QE lead nothing. "A new wallet route took 8.8% of checkout
traffic" tells them everything.

**Graph** â€” [`graph.py`](goldenflow/phase2/graph.py). Idempotent Cypher for Neo4j,
plus the same gap query in both Cypher and Python so the two cannot drift apart.

```bash
goldenflow graph export --store artifacts/events.db \
    --taxonomy config/taxonomy.yaml --format cypher --out graph.cypher
```

**Naming** â€” [`naming.py`](goldenflow/phase2/naming.py). Rule-based by default, no
API key needed. `LlmJourneyNamer` wraps any caller-supplied completion function and
falls back on error â€” naming is presentation and must never take down the pipeline
that produced the numbers. **The LLM never touches the analysis**; every figure comes
from the deterministic pipeline.

### The bug worth knowing about

Default clustering merged `home â†’ account_home â†’ order_history` with
`home â†’ account_home â†’ account_delete`. They share two of three screens, so they
score 0.667 similarity and collapse into one generic "account" archetype.

The GDPR erasure journey disappeared from the catalogue â€” and an invisible journey
produces no coverage gap in Phase 3, so Phase 5 never generates its test. The whole
chain fails silently for exactly the low-traffic, high-consequence flows the
protected registry exists to defend.

Fixed by `preserve_terminals`: when either journey ends on a screen the taxonomy
marks `critical`, terminals must match exactly. For those journeys the destination
*is* the journey. `account_delete` now ranks 13th of 18 on 2 sessions â€” where pure
frequency would bury it last.

---

## Phase 3 â€” QA Asset Graph & Gap Analysis

**The first phase that ships value on its own.** No generation yet â€” just "here are
the journeys your suite doesn't cover." Shipping insight before automation earns the
trust Phase 5 will need; reverse the order and QE teams meet the product as a
code-spam machine.

```bash
goldenflow gaps analyse --store artifacts/events.db \
    --taxonomy config/taxonomy.yaml --suite fixtures/appium-suite \
    --registry config/protected-tests.yaml
```

```
  transition coverage : 38.3% (18/47)
  traffic-weighted    : 89.9%
  journeys            : 9 fully covered, 2 untouched, of 18

[HIGH    ] coverage_gap  Home to Payment Failure: 4/5 transitions covered
           72 sessions (4.8%), score 72.5. Missing: payment_method -> payment_failure
[HIGH    ] coverage_gap  Home to Delete Account: 1/2 transitions covered
           2 sessions (0.1%), score 46.7. Missing: account_home -> account_delete
```

Two numbers worth contrasting: **38.3% of transitions** are covered but **89.9% of
traffic** is. The suite covers the busy paths and misses the long tail â€” including
payment-failure recovery, which production hits on ~5% of checkouts.

And note the second finding: **2 sessions, 0.1% of traffic, still HIGH severity**,
because the journey touches a screen the taxonomy marks critical. A traffic-ranked
system would never surface it.

### Coverage is measured in transitions, not screens

A suite can touch every screen in the app and never walk `cart â†’ payment_method`.
Navigation regressions live in the transitions.

### Parsing

[`parser_python.py`](goldenflow/phase3/parser_python.py) uses the real `ast` module â€”
`ast` is in the standard library, so guessing at Python would be inexcusable.
[`parser_text.py`](goldenflow/phase3/parser_text.py) handles Java, Kotlin and
JavaScript by regex, and is honest about it: those parsers can *miss* things, so
comments and annotation arguments are stripped first to ensure they never *invent*
things.

Every attribution carries [`Confidence`](goldenflow/phase3/models.py):

| Level | Basis | Counts as coverage |
|---|---|---|
| HIGH | Page Object the taxonomy binds to a screen | Yes |
| MEDIUM | Screen-ID literal or navigation helper | Yes |
| LOW | Guessed from the test name | **No** |

A guess must never be able to make a path look tested.

### The five findings

| Kind | Meaning |
|---|---|
| `coverage_gap` | Users walk it; no test does |
| `obsolete` | A test walks it; production no longer does |
| `stale` | Covered, but the journey changed shape |
| `over_tested` | Effort concentrated on a below-median journey |
| `assertion_gap` | Path walked but the failure mode never asserted |

### The guardrail, demonstrated

The fixture contains two tests with **identical evidence** â€” each walks exactly one
transition production never shows:

```
[LOW ] obsolete            tests/test_deeplinks.py::test_deeplink_straight_to_payment
       -> CANDIDATE ONLY - requires a second corroborating signal and human approval

[INFO] protected_retained  tests/a11y/test_talkback_checkout.py::test_talkback_...
       Protected under accessibility. Usage share is not a proxy for obligation.
       -> Retain. Low traffic is the expected state here.
```

Same evidence, opposite outcome. That is the protected registry from Phase 0 earning
its place three phases later â€” and it is why the registry was built before anything
could propose a deletion.

### Reporting

```bash
goldenflow gaps analyse ... --jira-out artifacts/jira.json \
                            --metrics-out artifacts/goldenflow.prom
```

Both are **payload generators, not integrations** â€” they emit what would be POSTed
and stop. Creating tickets in someone's project is an outward-facing side effect
that belongs behind an explicit human action. Obsolete findings are deliberately
excluded from Jira: a ticket reading "delete this test" is an instruction, and
obsolescence here is only ever a hypothesis.

---

## Phase 4 â€” Locator Resolution

Analytics gives you screen **names**. Appium needs **locators**. Without this bridge,
"automatically generates Appium scripts" is not an achievable claim â€” and it's the
layer most similar proposals omit entirely.

```bash
goldenflow locators resolve --store artifacts/events.db \
    --taxonomy config/taxonomy.yaml --suite fixtures/appium-suite \
    --crawl fixtures/ui-dumps/v8.2.0
```

```
  step resolution   : 89.0% of journey steps
  executable journeys: 12/18 (66.7%)

  [OK  ] aj_007   100%  Home to Payment Failure
  [OK  ] aj_015   100%  Home to Delete Account
  [GAP ] aj_008    33%  Home to Contact Support
          blocked by: help_center, contact_support
```

**`aj_007` and `aj_015` are the payoff.** Phase 2 found them, Phase 3 flagged both as
coverage gaps, and neither has a Page Object â€” the suite literally cannot drive those
screens. The crawl dump makes them generatable. That's the chain closing.

### Three sources, merged not chosen

| Source | Role |
|---|---|
| Page Objects | Highest confidence; reflects what the team decided locators *should* be |
| Crawl dumps | Reaches screens with no Page Object |
| Replay hierarchy | Reaches states a crawler can't â€” post-payment, real backend errors |

Merged, because two sources agreeing is stronger evidence than either alone â€” and
where they *disagree*, that's worth knowing before generation rather than after.

### Stability ranking

| Strategy | Score | Why |
|---|---|---|
| `accessibility_id` | 1.00 | Semantic, maintained for screen readers |
| `resource_id` | 0.80 | Stable until a module refactor |
| `semantic_xpath` | 0.55 | Survives layout, breaks on copy changes |
| `positional_xpath` | 0.15 | Breaks when anything is inserted above it |
| `coordinates` | 0.00 | Never proposed |

Anything below 0.50 is **never handed to Phase 5**. A generated test that passes
today and breaks next build costs more trust than the missing test would have.

**Ambiguity beats strategy rank.** Three payment rows sharing one accessibility ID:

```
  payment_option_card
    resource_id         0.80  com.acme.shop:id/payment_option_card
   !accessibility_id    0.25  payment_option_row [3 matches]
```

The a11y ID outranks resource ID on strategy alone, and still loses. A test tapping
the wrong one of three fails in a way that looks like a product bug â€” a harder
failure than breaking loudly.

### The Page Objects were wrong

Page Object locators arrive as *assertions nobody has ever checked*. Verified against
a real hierarchy, the fixture suite had four latent defects:

```
payment_method.card_option: Page Object claims accessibility_id='payment_option_card'
  but no such element exists. The strategy is misdeclared.
cart.quantity_stepper: locator matches 2 elements. The test has been relying on
  document order.
```

Three misdeclared strategies (resource IDs written as accessibility IDs) and one
genuinely ambiguous locator â€” `increase_quantity()` silently always incremented the
first cart row. All four are now fixed in
[checkout_pages.py](fixtures/appium-suite/pages/checkout_pages.py), and a regression
test guards them.

### Fingerprinting and healing

Screen identity must survive a release, or the graph re-keys itself and coverage
history resets to zero â€” which looks like a catastrophic regression and is in fact a
rename. Fingerprints are built from accessibility IDs, element composition and text;
**resource IDs are deliberately excluded** as the thing most likely to have changed.

`not_captured` is distinguished from `removed`: a crawler that didn't reach a screen
proves nothing about whether it still exists.

Healing across the v9 build, where every payment resource ID was renamed:

```
  survived intact  : 46.7%
  broken by strategy: {'resource_id': '4 broken'}

  [REPAIR ] payment_option_card  (confidence 0.90)
            resource_id=com.acme.shop:id/payment_option_card
         -> resource_id=com.acme.shop:id/checkout_pay_card
```

Accessibility-ID locators survived untouched; resource-ID locators all broke at once.
That's the stability ranking's evidence, measured rather than asserted. **Downgrades
never auto-apply** â€” silent downgrades are how a suite rots while its pass rate stays
green.

---

## Phase 5 â€” Generation & Validation

One rule the whole phase rests on:

> **Nothing reaches a human reviewer until it has already run green on a real device.**

A QE team that merges two broken generated tests will never trust the third.

```bash
goldenflow generate --store artifacts/events.db --taxonomy config/taxonomy.yaml \
    --suite fixtures/appium-suite --crawl fixtures/ui-dumps/v8.2.0 --show
```

```
  journeys specced  : 12 (12 executable)
  tests rendered    : 12

  runner            : compile-only
  validated         : 0 (0.0% first attempt)
  unvalidated       : 12

  UNVALIDATED is not a pass. No device farm executed these, so
  nothing here is eligible for a pull request.
```

**The Phase 5 gate returns NO-GO, and that is the correct result here.** Twelve tests
generate and compile; none has run on a device, so none is eligible for review. The
gate refuses to certify on a syntax check.

### The spec/generator split

Specs are assembled **deterministically** from production data â€” every screen,
locator and assertion fixed before a model is involved. The generator decides only
how the code *reads*.

Ask a model what to test and it invents plausible journeys. Hand it a fixed spec and
its failure mode becomes bad syntax, which the gate catches.

### Three seams left open, not faked

| Seam | Behaviour |
|---|---|
| [`DeviceFarmRunner`](goldenflow/phase5/validation.py) | **Raises `NotImplementedError`** |
| [`LlmGenerator`](goldenflow/phase5/generator.py) | Takes a caller-supplied completion function |
| [`PullRequest`](goldenflow/phase5/pull_request.py) | Emits payloads; never commits |

`DeviceFarmRunner` raises rather than returning a default because a runner that
silently reports success is the most dangerous thing this codebase could contain.

### Validation policy

10 runs Ã— 3 device profiles, all green, or no PR.

| Verdict | Meaning |
|---|---|
| `VALIDATED` | Green every run, every profile. Eligible for a PR. |
| `FAILED` | Red at least once. Never a PR. |
| `FLAKY` | Passed *sometimes*. **Quarantined** â€” worse than none, it teaches the team to ignore failures. |
| `UNVALIDATED` | Nothing actually ran. Explicitly not a pass. |

Code that doesn't compile never reaches a device â€” pinned by a test asserting the
runner was called zero times.

### Deletion needs two signals

```
[HOLD] tests/test_deeplinks.py::test_deeplink_straight_to_payment
       Held back: only 1 signal (no production traffic). Deletion requires two
       independent signals - low traffic alone is not evidence of death.

[HOLD] tests/a11y/test_talkback_checkout.py::test_talkback_...
       BLOCKED: protected under accessibility by platform-qe.
```

**Zero of four candidates cleared.** Additions need one signal; the downside of a
wrong addition is a redundant test. Deletions need two plus a registry check, because
the downside is silently losing coverage that was protecting a regulatory obligation.

### A defect worth naming

The first generator emitted `assert cart is not None` â€” which cannot fail, since
instantiating a Page Object always returns an object. That is exactly the vacuous
coverage **Phase 3's own `assertion_gap` analysis exists to catch**, being produced by
Phase 5.

It now uses the real Page Object methods Phase 3 parsed (`cart.item_count()`,
`order_review.order_total()`), falls back to `find_element(...).is_displayed()`, and
where neither exists emits `# TODO no assertable state exposed` rather than a
comforting lie. A test pins that `assert <var> is not None` never appears again.

---

## Phase 6 â€” Closed Loop & Governance

The original diagram's loop read *"Production Insights â†’ Better Quality â†’ Happier
Users"*. That's a narrative. This phase makes it something the system measures itself
against â€” **real escaped defects, not opinion about whether the gaps looked right.**

```bash
goldenflow outcomes report --store artifacts/events.db \
    --taxonomy config/taxonomy.yaml --suite fixtures/appium-suite \
    --crawl fixtures/ui-dumps/v8.2.0 --defects fixtures/escaped-defects.json
```

```
  escaped defects       : 12 (10 in scope)

  Why they escaped:
    uncovered gap       : 5   (GoldenFlow flagged it; nobody closed it)
    covered but missed  : 4   (a test walked the path and passed anyway)
    unknown journey     : 1   (mining or instrumentation missed it)
    out of scope        : 2   (no journey to attribute it to)

  precision             : 75.0%
  recall                : 54.5%
  assertion blind spot  : 40.0%
```

**40% of in-scope defects slipped past a passing test.** That is the strongest
available argument for Phase 3's assertion-gap analysis â€” measured, not asserted.

### Two failure modes this defends against

**Flattering itself.** An open gap that hasn't yet caused an incident is
`unresolved`, not a win. Only gaps that *produced a defect* count as true positives.

**Panicking.** `precision` returns `None`, never `0.0`, when nothing has resolved
yet. A hard zero reads as "the system is broken" when the truth is "too early to
say" â€” and that distinction decides whether a programme survives its first quarter.

### Weight tuning, bounded and explained

```
  frequency        0.300 -> 0.272  (-0.028)
  business_value   0.300 -> 0.339  (+0.039)
  risk             0.250 -> 0.200  (-0.050)
  exposure         0.150 -> 0.200  (+0.050)

  Evidence:
    exposure         hit rate 100.0%  (9 seen, 0 missed)
    business_value   hit rate  88.9%  (8 seen, 1 missed)
    risk             hit rate  33.3%  (3 seen, 6 missed)

  Nothing is applied automatically. Review and accept.
```

Three rules stop this becoming an unaccountable feedback loop:

| Rule | Why |
|---|---|
| Movement capped at **0.05/round** | A model that swings its own priorities produces a ranking nobody can plan against |
| Floor of **0.05** per component | A term driven to zero can never earn its way back â€” the evidence that would correct it is what it can no longer see |
| **Refuses below 10 attributed defects** | Tuning on a handful produces confident nonsense â€” the same failure the Phase 2 PSI calibration exposed |

### Governance

Lineage for every artifact: journey, sessions, locator sources, generator, model,
prompt **hash** (not the prompt â€” it carries screen names), validation runs, reviewer.
Rejections are captured as labelled data: a reviewer saying no is information, not
just a blocked merge.

Cost is tracked per unit â€” LLM tokens, device minutes, warehouse TB â€” because a
system that quietly costs more than the QE time it saves is a failure however good
its findings are. `unit_economics()` flags its own hours-saved assumption in the
output so nobody mistakes it for a measurement.

### The bug this phase surfaced

`covered but missed` read **0** on the first run, despite three defects explicitly on
covered paths. The cause was upstream: I'd passed `build_dfg([])` â€” an *empty* trace
list â€” so the graph had no transitions and **every journey read 0% covered**. Phase 5
had been generating tests for journeys that were already tested (12 instead of 3).

Root cause was a 7-element tuple returned from a shared helper feeding five call
sites, which had already caused one arity bug. It now returns a namespace.

---

## Phase 7 — Multi-App Scale & Productization

One app is a pilot. Three is a platform — and the difference is mostly **removing the
platform team from the critical path**.

### Onboarding: months to weeks

The single highest-value piece. Phase 0 took eight weeks for the pilot, most of it
hand-writing a taxonomy by reading the app. Almost all of it is derivable:

```bash
goldenflow onboard scaffold --events sample.jsonl --app acme-rewards --out drafts/
```

```
  events analysed   : 12,699
  screens drafted   : 32
  events drafted    : 22
  estimated review  : ~6.9 hours

  EXPECTED: this draft fails `taxonomy validate` with 13 error(s).
  All of them are critical screens with no Page Object bound, which
  the scaffolder cannot know. Binding them is the work; the errors
  are the to-do list, not a defect.
```

That last block matters. Without it a team runs `taxonomy validate` on a fresh draft,
sees a wall of errors, and concludes the generator is broken.

**Sensitivity is inferred, and biased toward over-protection.** It decides whether a
screen gets occluded in session replay, so an unmatched screen defaults to `low`,
never `none` — wrong-high costs replay fidelity, wrong-low costs a breach
notification. Every value carries its reason into the YAML, and the file opens with a
`# DRAFT — NOT signed off` banner.

### Tenancy: blast radius, not privacy

These teams work for the same company. What must not happen is a taxonomy edit for
the payments app re-keying the shopping app's journey graph — which presents as a
mysterious coverage collapse, not an error.

```bash
goldenflow tenant check config/tenants.yaml
```

Tenant paths **default to tenant-scoped locations**, so a new tenant is isolated by
construction and sharing becomes a choice someone had to write down. A test caught
this: my first version defaulted `taxonomy_path` to a shared `config/taxonomy.yaml`,
handing out the exact failure the module exists to prevent.

Per-tenant scoring weights are a first-class concept — a payments app weights risk at
0.40 where a shopping app runs 0.25, and Phase 6 tunes each separately.

### RBAC, on exactly what's hard to undo

| Role | Read | Generate | Approve deletion | Onboard tenant |
|---|---|---|---|---|
| viewer | ✓ | | | |
| engineer | ✓ | ✓ | | |
| owner | ✓ | ✓ | ✓ | |
| platform | ✓ | ✓ | ✓ | ✓ |

`require()` **raises** rather than returning a boolean — the same reasoning as Phase
0's `assert_deletable`. Denials name the role that would suffice.

### Connectors, parity, portfolio

Adding a vendor is a registration, not an edit to the ingest path. Auto-capture is
surfaced per connector because it moves the onboarding estimate by *weeks*, not hours.

Parity compares iOS against Android. The finding that pays for it is **coverage
asymmetry**: a team writes the checkout test on Android, ships iOS, and nobody
notices the iOS path is untested because the aggregate number looks fine.

```
  tenant          health      journeys   cov%  gaps
  acme-pay        no-data            0      -     0
  acme-rewards    onboarding         0      -     0
  acme-shop       at-risk           18     38    12
```

`acme-pay` reads **`no-data`, not `healthy`** — an active tenant with no measurement
is not healthy, and reporting it as such is how a tenant whose pipeline silently
stopped sits green on a director's dashboard for a quarter. ROI is measured against
each tenant's *own* Phase 0 baseline, because a portfolio average lets a strong app
hide a weak one.

### What Phase 7 does not prove

The SLA over two consecutive quarters, and the real onboarding duration. Both need
calendar time and real tenants rather than a fixture. The gate says so.

---

## Analytics: UXCam

```bash
goldenflow ingest --events uxcam-events.jsonl --source uxcam \
    --uxcam-signals uxcam-events.jsonl \
    --taxonomy config/taxonomy.yaml --store artifacts/events.db
```

**Why UXCam:** auto-capture. Taps, gestures and screens are recorded with no
per-interaction instrumentation, so journeys exist the moment the SDK ships â€” not
after an instrumentation project the mobile roadmap has to absorb. That matters
because the whole premise is *learn from production without asking the app team for
work*.

`eventScreen` is auto-tagged on **every** event, so the screen sequence falls out of
ordering a session. And every event carries `url` â€” a deep link to the replay â€”
carried through as `uxcam_session_url`. "Here is a journey 72 sessions take that no
test covers" is an argument; *"â€¦and here is one of them, on video"* ends it.

**Free failure signals no crash reporter provides.** Rage Tap and UI Freeze are
auto-captured and routed into risk weighting. A user jabbing a dead button six times
never crashes the app and never reaches Crashlytics, but it's one of the strongest
indicators of a broken screen there is.

**The price, stated plainly.** Session replay records real users' screens, so
occlusion is mandatory and **fails open** â€” a forgotten `occludeSensitiveScreen()` is
a silent privacy incident, not a visible data gap. That cost is accepted deliberately;
[docs/pii-policy.md](docs/pii-policy.md) is the compensating control, and the
1%-rollout replay review is what actually catches a miss.

**CleverTap was evaluated in full and reversed.** It covers analytics well but
captures no UI interactions without explicit code. Measured: journeys survive either
way (both mine 59 variants), but `action_coverage` falls to 0% and revenue
attributable to journeys drops to **zero** â€” removing half the evidence behind
separating *most-walked* from *most-valuable*. Full assessment:
[docs/uxcam-integration.md](docs/uxcam-integration.md).

**What UXCam won't give you:** raw individual taps aren't queryable â€” they live in the
replay video, not the Events API. Screen tagging still needs one `tagScreenName()` line
per screen, because auto-tagging yields class names that churn on every refactor. And
replay is sampled on lower plans.

---

## Development

```bash
python -m pytest -q                      # 512 tests
python tools/generate_sample_events.py   # synthetic events, crashes, errors
python tools/generate_sample_events.py --inject-pii   # plant a policy violation
```

`tools/generate_sample_events.py` produces clearly-labelled synthetic data that
deliberately reproduces the defects a real export contains â€” rare flows absent from a
bounded sample, tag drift, missing session IDs, anonymous traffic. An audit that only
ever sees clean input is not an audit.

---

## Repository layout

```
goldenflow/phase0/     taxonomy, readiness, baseline, registry
goldenflow/cli.py      CI-wirable commands, non-zero exit on failure
config/                the taxonomy and registry contracts
docs/                  naming contract, PII & occlusion policy
tools/                 synthetic sample generation
tests/                 91 tests
artifacts/             audit reports and baselines
```
