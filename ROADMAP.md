# GoldenFlow AI â€” Product Roadmap

**A Self-Evolving Mobile QE Agent**
*Learning from Production. Evolving Quality.*

Full product build-out. 8 phases, ~18 months to GA, ~24 months to full org adoption.
This is not a POC plan â€” every phase ends in something running in production with owners and SLAs.

---

## Product Thesis

Test suites are authored once from requirements and assumed behaviour. Production diverges.
Nothing carries that divergence back into QA, so suites decay silently while dashboards stay green.

GoldenFlow closes the loop: production telemetry â†’ Golden Journeys â†’ diff against QA assets â†’
generated and validated automation â†’ measured back against escaped defects.

**Core technical framing:** this is *process mining + conformance checking* applied to mobile QE.
Deterministic algorithms do the analysis. LLMs do semantic interpretation and code generation.
That split is what makes the system auditable.

---

## Phase Map

| Phase | Name | Window | Ships |
|-------|------|--------|-------|
| 0 | Foundation & Readiness | M1â€“M2 | Go/no-go decision, governance, baselines |
| 1 | Data Foundation | M2â€“M5 | Single queryable event store |
| 2 | Journey Intelligence Engine | M4â€“M8 | Auto-refreshing Golden Journey catalogue |
| 3 | QA Asset Graph & Gap Analysis | M7â€“M10 | **First customer value: the gap report** |
| 4 | Locator Resolution | M9â€“M12 | Journey steps become executable |
| 5 | Generation & Validation | M11â€“M15 | First auto-generated test merged |
| 6 | Closed Loop & Governance | M14â€“M18 | Self-measuring, self-tuning system |
| 7 | Multi-App Scale & Productization | M17â€“M24 | Org-wide adoption |

Phases overlap deliberately. Bars are staffing windows, not hard gates.

---

## Phase 0 â€” Foundation & Readiness
**M1â€“M2 Â· 8 weeks**

The phase most similar products skip, and the reason most of them fail. GoldenFlow's entire
value depends on instrumentation quality it does not control. Find that out now, not in Phase 3.

### Workstreams

**0.1 Instrumentation readiness audit**
Score the target app against a rubric: % of screens emitting a screen-view event, % of user
actions instrumented, session ID integrity, user ID stability across reinstall, event naming
consistency. Produce a readiness score per app.

**0.2 Screen taxonomy & naming contract**
The single most important artifact in the entire product. A versioned, governed mapping between:
- analytics screen tag â†’ canonical screen ID â†’ Page Object class

Without this contract, journeys and tests can never be joined. Owned by QE, enforced in CI via
lint rules on both the app repo and the automation repo.

**0.3 Baseline measurement**
You cannot claim improvement without a before. Capture: current automated coverage %, escaped
defect rate per release, mean time to detect, regression suite runtime, % of suite that is flaky,
and manual test-authoring hours per sprint.

**0.4 Privacy, legal & security review**
Session replay records screens. Analytics carries user identifiers. This needs sign-off before a
single SDK ships, not after. Deliverables: PII masking policy, data retention schedule, DPA review
for each vendor, regional data residency plan (GDPR/DPDP), and an occlusion checklist enforced at
PR review for any new screen touching payment, auth, PII, or health data.

**0.5 Protected test registry (governance, defined early)**
A registry of tests that are **never** eligible for obsolescence pruning regardless of production
traffic â€” account deletion, payment failure recovery, refunds, data export, accessibility flows,
regulatory journeys. Low usage is not low importance, and this registry is the guardrail that
encodes it. Defined in Phase 0 so it exists before anything can propose a deletion.

**0.6 Tooling decisions & procurement**
Lock the stack. Commercial agreements for analytics, session replay, and device farm have lead
times measured in months â€” start now.

### Exit criteria
- [ ] Readiness score â‰¥ 70% for at least one pilot app, with a remediation plan for gaps
- [ ] Screen taxonomy v1 published and CI-enforced in both repos
- [ ] Baseline metrics captured and signed off by QE leadership
- [ ] Legal/privacy sign-off obtained for all vendors
- [ ] Protected test registry populated with an initial set
- [ ] Contracts executed

### Team
1 Product Manager Â· 1 QE Architect Â· 1 Data Engineer Â· 0.5 Legal/Privacy Â· 0.5 Security

### Risk
> **The audit may reveal the pilot app is not instrumentable within budget.** That is a valid and
> valuable outcome. Phase 0 exists to surface it for Â£X of effort rather than Â£XXX. Have a second
> candidate app ready.

---

## Phase 1 â€” Data Foundation
**M2â€“M5 Â· 14 weeks**

Build the single source of truth. Every downstream phase reads from here and nowhere else.

### Workstreams

**1.1 Tracking plan**
A versioned schema contract â€” event names, required properties, types, screen tags. Managed as
code, reviewed like an API. This is the interface between the app team and GoldenFlow.

**1.2 CDP deployment â€” RudderStack**
Self-hosted, open source. One instrumentation point in the app; fan out to product analytics for
humans and to the warehouse for GoldenFlow. This removes "Mixpanel or Firebase?" as an
architectural decision permanently â€” it becomes a downstream destination toggle.

**1.3 SDK integration**
Analytics + session replay SDK (UXCam), crash reporting (Crashlytics), error/performance (Sentry).
Ship behind a remote-config kill switch and a staged rollout â€” 1% â†’ 10% â†’ 50% â†’ 100%
with battery, size, ANR, and crash-rate gates at each step.

**UXCam auto-captures** taps, gestures and screens with no per-interaction code, which is why it was chosen: journeys exist the moment the SDK ships. The price is that occlusion is mandatory and fails open - see [docs/pii-policy.md](docs/pii-policy.md) and [docs/uxcam-integration.md](docs/uxcam-integration.md).

**1.4 Event store**
BigQuery where the org is Firebase-native â€” the Firebaseâ†’BigQuery export gives raw event-level
rows for free and is the highest-leverage integration available. ClickHouse where vendor neutrality
is required; it is purpose-built for the sequence queries Phase 2 will run.

**1.5 Risk signal join**
Crashlytics and Sentry into the same store, keyed on session ID so a crash can be located at a
precise position in a journey. Backend latency from the APM, joined on trace/request ID where the
app propagates one.

**1.6 Data quality monitoring**
Freshness, volume anomaly, schema drift, session-ID null rate, orphan-event rate. Alerting to the
data team. Garbage in Phase 1 becomes confident nonsense in Phase 2 â€” this is the firewall.

### Exit criteria
- [ ] â‰¥ 90% of screens emitting conformant screen-view events in production
- [ ] Session reconstruction validated against manual replay review on a 100-session sample
- [ ] Store queryable with < 4h data latency
- [ ] Zero PII detected in a sampled audit of 1,000 sessions
- [ ] App size increase < 3%, crash-rate delta statistically insignificant
- [ ] Data quality alerting live

### Team
2 Data Engineers Â· 2 Mobile Engineers (iOS/Android) Â· 1 QE Architect Â· 0.5 SRE

---

## Phase 2 â€” Journey Intelligence Engine
**M4â€“M8 Â· 18 weeks**

Agent â‘ . The research core of the product, and the section to lead with in any technical review.

### Workstreams

**2.1 Sessionization**
Group events into ordered traces. Native session ID where available; 30-minute inactivity window
as fallback. Handle backgrounding, cold vs. warm start, and cross-device continuation.

**2.2 Process mining â€” PM4Py**
The problem is a solved academic one with mature tooling; use it rather than inventing.

| GoldenFlow concept | Process mining equivalent |
|---|---|
| User journey | Trace / event log |
| Golden Journey | Discovered process model (Inductive Miner) |
| Journey Drift | Concept drift |
| Coverage gap | Conformance checking |

Pipeline: event log â†’ Directly-Follows Graph â†’ Inductive Miner â†’ petri net / process tree.

**2.3 Variant clustering**
A real app produces tens of thousands of distinct paths. Collapse to archetypes using PrefixSpan
sequential pattern mining and Markov-chain transition models. Target: 95% of sessions explained by
< 50 archetypes.

**2.4 Golden Journey scoring**
```
score = frequency_weight   Ã— log(session_count)
      Ã— business_weight    Ã— revenue_or_conversion_attribution
      Ã— risk_weight        Ã— (crash_rate + anr_rate + error_rate + rage_tap_rate)
      Ã— exposure_weight    Ã— (1 âˆ’ current_coverage)
```
Weights are configurable per app and auto-tuned in Phase 6. Business value comes from a
manually-maintained flow-to-value mapping â€” this is deliberately human-owned, not inferred.

**2.5 Journey Drift detection**
Defined, measurable, no hand-waving:
- **Distribution shift** â€” Jensenâ€“Shannon divergence between path distributions across time windows
- **Population Stability Index** on journey archetype membership
- **Novel variant emergence rate** â€” % of sessions on paths unseen in the trailing baseline
- **Drop-off position shift** â€” movement in the modal exit point of a funnel

Each with a configurable alerting threshold.

**2.6 Semantic layer (LLM)**
Claude converts mined structures into human-legible journeys: names them, writes the business
description, groups them by domain, flags anomalies in plain language. **The LLM never performs the
statistical analysis** â€” it translates results a reviewer can independently verify.

**2.7 Journey graph in Neo4j**
Screens as nodes, observed transitions as weighted edges, journeys as annotated paths.

### Exit criteria
- [ ] Journey catalogue auto-refreshing on schedule
- [ ] â‰¥ 95% of sessions mapped to a named archetype
- [ ] Drift metrics computed with alerting live
- [ ] Blind validation: QE leads shown the top 10 Golden Journeys agree â‰¥ 80% they are the
      journeys that matter â€” the honesty test for the whole engine
- [ ] Journey graph queryable in Neo4j

### Team
1 Data Scientist (process mining) Â· 2 Data Engineers Â· 1 ML/LLM Engineer Â· 1 QE Architect

---

## Phase 3 â€” QA Asset Graph & Gap Analysis
**M7â€“M10 Â· 14 weeks**

**This is the first phase that delivers standalone customer value.** Before any code generation
exists, "here are the journeys your suite does not cover" is independently worth the build.
Ship insight before automation â€” it earns the trust that Phase 5 will need.

### Workstreams

**3.1 Suite parsers**
AST-level parsing of the existing Appium suite. Multi-language: Java/TestNG, Python/pytest,
JS/WebdriverIO, Kotlin. Extract per test: screens touched, ordered steps, assertions, tags,
Page Objects referenced.

**3.2 Page Object extraction**
Parse the POM repository into canonical screen IDs and their locator inventory. Joined to
production screens via the Phase 0 naming contract.

**3.3 Coverage graph**
Project every test as a path over the same node set as the journey graph. Both graphs, one
database, one vocabulary.

**3.4 Gap analysis â€” as a graph diff**
The elegance of the design, and the demo that sells the product:

| Finding | Query |
|---|---|
| **Coverage gap** | High-traffic edges with no covering test path |
| **Obsolete** | Test paths with near-zero production traffic |
| **Stale** | Test path exists but observed transitions have changed |
| **Over-tested** | Many tests on low-value paths â€” reallocation opportunity |
| **Assertion gap** | Path covered but the failure mode seen in production is not asserted |

**3.5 Confidence scoring**
Every mapping carries a confidence value. Low-confidence joins are surfaced for human confirmation
rather than acted on silently. Unmappable tests are reported, never guessed at.

**3.6 Reporting surface**
Grafana dashboards â€” coverage %, gap count by severity, drift score over time, test-to-value
alignment. Jira integration creating gap tickets with journey evidence attached.

### Exit criteria
- [ ] â‰¥ 85% of the existing suite successfully parsed and mapped
- [ ] Gap report reviewed by QE leads; â‰¥ 75% of reported gaps confirmed genuine
- [ ] Obsolete candidates cross-checked against the protected registry with zero false deletions proposed
- [ ] Dashboards live, Jira integration operating
- [ ] **First real defect traced to a gap GoldenFlow identified**

### Team
2 Backend Engineers (parsers) Â· 1 Data Engineer Â· 1 QE Architect Â· 1 Frontend (dashboards)

---

## Phase 4 â€” Locator Resolution
**M9â€“M12 Â· 14 weeks**

The layer that turns a journey description into something executable. Analytics gives you screen
*names*; Appium needs *locators*. This bridge is the single most under-estimated component in the
product, and the one most similar proposals omit entirely.

### Workstreams

**4.1 Page Object resolver (primary path)**
Canonical screen ID â†’ Page Object â†’ locator inventory. Highest confidence, zero extra
infrastructure, works wherever POM discipline exists.

**4.2 Automated crawler (gap-filling path)**
Firebase Test Lab Robo Test crawls the app and dumps the UI hierarchy per screen, building a
screenâ†’element map without human effort. Supplemented by a custom Appium crawler where deeper or
authenticated-state traversal is needed.

**4.3 Session replay hierarchy (third path)**
UXCam replay data carries view hierarchy for real production screens, covering states the
crawler cannot reach: post-payment, and error conditions triggered only by a real backend
response. The `--replay` input takes Appium page-source XML from any source, so a manual
dump or an instrumented run works identically.

Worth knowing how much this path actually buys: Phase 4 reached **89% step resolution
and 12/18 executable journeys using paths 1 and 2 alone**, and both highest-consequence
journeys (`payment_failure`, `account_delete`) resolved fully from the crawl. Path 3 is
insurance for the screens a crawler cannot reach, not the primary source.
See [docs/uxcam-integration.md](docs/uxcam-integration.md).

**4.4 Screen fingerprinting**
Identify the same logical screen across app versions when IDs shift, via structural similarity of
the element tree. This is what keeps resolution stable across releases.

**4.5 Locator quality & self-healing**
Rank candidate locators by stability: accessibility ID > resource ID > semantic XPath > positional.
Detect broken locators against new builds and propose repairs. Never silently swap to a fragile
strategy â€” downgrade proposals go to human review.

### Exit criteria
- [ ] â‰¥ 80% of Golden Journey steps resolved to at least one high-confidence locator
- [ ] Fingerprinting holds screen identity across three consecutive app releases
- [ ] Unresolvable steps reported with actionable diagnostics, never silently dropped
- [ ] Self-healing proposals achieve â‰¥ 90% precision on a held-out set

### Team
2 Mobile Automation Engineers Â· 1 Backend Engineer Â· 1 QE Architect

---

## Phase 5 â€” Generation & Validation
**M11â€“M15 Â· 18 weeks**

Agent â‘¡'s output stage. The credibility of the entire product rests on one rule:
**nothing reaches a human reviewer until it has already run green on a real device.**

### Workstreams

**5.1 Test generation**
Claude generates from three inputs: journey spec, resolved locators, and **existing test files as
few-shot examples** â€” so output matches the repo's conventions, helpers, and fixtures rather than
generic Appium. Generated tests carry provenance metadata: source journey, session count, score,
generation timestamp, model version.

**5.2 Validation harness â€” the non-negotiable gate**
```
generate â†’ compile â†’ run on device farm â†’ repeat NÃ— for flake detection
        â†’ assert deterministic â†’ only then open PR
```
BrowserStack / Sauce Labs / Firebase Test Lab. A test that fails, or that passes inconsistently
across N runs, never becomes a PR. This is what separates the product from a code-spam machine.

**5.3 Flakiness gate**
Minimum 10 consecutive runs across at least 3 device profiles. Any non-determinism disqualifies.
Suspected flaky tests are quarantined, not merged.

**5.4 PR automation & human review**
GitHub Actions opens a PR containing: the test, the journey evidence, production traffic
statistics, validation run history, and the specific gap it closes. **Human approval is required to
merge. The agent never commits to a protected branch.**

**5.5 Obsolescence workflow â€” with safeguards**
Deletion proposals require *two independent signals* â€” low production traffic **plus** a
corroborating signal such as a removed screen or retired feature flag. Protected registry members
are excluded unconditionally. Every deletion is a reviewed PR with a documented rationale.

**5.6 Update path**
Prefer amending an existing test over authoring a new one when a journey has drifted. Suite growth
is a cost, not a success metric.

### Exit criteria
- [ ] Generated tests pass the validation gate at â‰¥ 70% first-attempt rate
- [ ] â‰¥ 60% of opened PRs merged by human reviewers without modification
- [ ] Zero unreviewed commits to any protected branch
- [ ] Flake rate of merged generated tests â‰¤ the existing suite's baseline
- [ ] **First auto-generated test catches a real regression before release**

### Team
1 ML/LLM Engineer Â· 2 Mobile Automation Engineers Â· 1 DevOps Â· 1 QE Architect

---

## Phase 6 â€” Closed Loop & Governance
**M14â€“M18 Â· 18 weeks**

The loop in the original diagram was narrative â€” "better quality â†’ happier users". This phase makes
it a mechanism the system can measure itself against.

### Workstreams

**6.1 Outcome tracking**
Instrument the loop's own effectiveness: did generated tests catch real defects? Did identified
gaps correlate with subsequent production incidents? What is the precision and recall of gap
prediction against escaped defects?

**6.2 Scoring auto-tune**
Feed outcome data back into the Phase 2.4 weights. If risk-weighted journeys are over-predicting
incidents relative to frequency-weighted ones, the model rebalances. This is the actual
"self-evolving" claim, made concrete and measurable.

**6.3 Escaped-defect attribution**
For every production defect, answer automatically: was this journey in the Golden set? Was it
covered? If covered, why did the test not catch it? The answers become the highest-quality
training signal the system has.

**6.4 Human-in-the-loop feedback capture**
Reviewer rejections are labelled data. Capture *why* a PR was rejected and route it back into
generation prompts and locator ranking.

**6.5 Audit & compliance**
Full lineage for every generated artifact: which journey, which sessions, which model, which
prompt, which reviewer approved. Required for regulated environments and for any post-incident
review.

**6.6 Cost governance**
LLM spend per generated test, device-farm minutes per validation, warehouse query cost. Budget
alerting and per-app quotas.

### Exit criteria
- [ ] Precision/recall of gap prediction measured against a full release cycle of escaped defects
- [ ] Weight auto-tuning demonstrably improves prediction over a fixed baseline
- [ ] Complete audit lineage for every generated artifact
- [ ] Unit economics per generated test established and within target
- [ ] **Measured reduction in escaped defects vs. the Phase 0 baseline**

### Team
1 Data Scientist Â· 1 ML/LLM Engineer Â· 1 Backend Engineer Â· 1 QE Architect Â· 0.5 Compliance

---

## Phase 7 â€” Multi-App Scale & Productization
**M17â€“M24 Â· 30 weeks**

Turns a system that works for one app into a platform the organisation adopts â€” the direct answer
to *"Can this idea be adopted across organization/multiple projects?"*

### Workstreams

**7.1 Multi-tenancy**
Per-app isolation of data, config, scoring weights, and taxonomy. Shared infrastructure, separated
blast radius.

**7.2 Onboarding automation**
Reduce new-app onboarding from a Phase 0â€“1 repeat to a guided workflow: automated readiness scoring,
taxonomy scaffolding, connector configuration, baseline capture. Target: 2 weeks, not 5 months.

**7.3 Self-service configuration**
Teams tune their own scoring weights, protected registry, drift thresholds, and generation policy
without platform-team involvement.

**7.4 Connector framework**
Pluggable sources â€” Amplitude, Adjust, AppsFlyer, Datadog, New Relic, Instabug â€” so adoption is not
gated on one analytics vendor.

**7.5 Cross-platform intelligence**
iOS/Android journey parity analysis: where do the same users behave differently per platform, and
where does coverage diverge between the two suites?

**7.6 RBAC, SSO, audit, SLAs**
Enterprise readiness. Uptime targets, support model, incident response, documentation, runbooks.

**7.7 Advanced capabilities**
- **Anomaly-triggered generation** â€” a crash spike auto-generates a reproduction test
- **Predictive drift** â€” forecast which journeys are trending toward divergence before they do
- **Backend correlation** â€” join client journeys to API traces for full-stack root cause
- **Pre-release synthetic journeys** â€” model expected journeys for unreleased features from designs
  and specs, so new features ship with coverage from day one

### Exit criteria
- [ ] 3+ apps onboarded and operating independently
- [ ] New-app onboarding â‰¤ 2 weeks
- [ ] Platform SLA met for two consecutive quarters
- [ ] Self-service adoption â€” teams configuring without platform-team tickets
- [ ] Documented ROI across the portfolio vs. per-app Phase 0 baselines

### Team
2 Platform Engineers Â· 1 Data Engineer Â· 1 Frontend Â· 1 Product Manager Â· 1 Technical Writer Â· 0.5 SRE

---

## Success Metrics

Measured against the Phase 0 baseline, not against nothing.

| Metric | Target |
|---|---|
| Escaped defects per release | âˆ’40% |
| Automated coverage of top-20 Golden Journeys | â‰¥ 95% |
| Test authoring effort | âˆ’50% |
| Journey drift detection latency | < 7 days |
| Obsolete tests removed | âˆ’25% suite size, zero critical loss |
| Generated-test PR acceptance | â‰¥ 60% unmodified |
| New-app onboarding | â‰¤ 2 weeks |
| Regression suite runtime | âˆ’30% via reallocation |

---

## Risk Register

| Risk | Impact | Mitigation | Phase |
|---|---|---|---|
| **Instrumentation too sparse to mine journeys** | Fatal | Phase 0 readiness gate before any build | 0 |
| **Taxonomy drifts between app and automation repos** | High | CI-enforced naming contract, versioned | 0,1 |
| **Agent prunes low-traffic critical tests** | Severe | Protected registry + dual-signal rule + human PR | 0,5 |
| **Generated tests are flaky, suite trust collapses** | Severe | Validation gate before PR; N-run flake detection | 5 |
| **Session replay captures PII** | Legal | Occlusion policy, PR checklist, sampled audits | 0,1 |
| **Locator resolution below usable threshold** | High | Three independent resolution paths | 4 |
| **Analytics vendor lock-in** | Medium | CDP abstraction + connector framework | 1,7 |
| **LLM cost scales past value** | Medium | Cost governance, quotas, cheaper models for classification | 6 |
| **QE teams distrust generated tests** | High | Ship gap insight (Phase 3) before generation (Phase 5) | 3 |
| **App rewrite invalidates the graph** | Medium | Screen fingerprinting; taxonomy survives refactors | 4 |

---

## Team Shape at Peak (Phase 5â€“6)

| Role | FTE |
|---|---|
| Product Manager | 1 |
| QE Architect | 1 |
| Data Engineer | 2 |
| Data Scientist (process mining) | 1 |
| ML/LLM Engineer | 1 |
| Backend Engineer | 2 |
| Mobile Automation Engineer | 2 |
| Mobile Engineer (iOS/Android) | 1 |
| DevOps / SRE | 1 |
| Frontend | 1 |
| **Total** | **13** |

Phase 0 starts at ~3.5 FTE and ramps.

---

## Reference Stack

| Layer | Selection | Rationale |
|---|---|---|
| Instrumentation | RudderStack | One tap point, open source, self-hosted |
| Analytics + replay | **UXCam** | Auto-captured taps/screens, session replay, rage-tap signals |
| Crash / errors | Crashlytics + Sentry | Crash, ANR, error, release health |
| Event store | BigQuery / ClickHouse | Preserves per-session sequence and identity |
| Process mining | Python + PM4Py | Mature, citable algorithms |
| Graph | Neo4j | Journey âŠ• coverage in one vocabulary |
| Orchestration | Dagster | Retries, lineage, backfill, observability |
| LLM | Claude | Semantic interpretation + code generation |
| Automation target | Appium + POM | Existing asset base |
| Device farm | BrowserStack / Firebase Test Lab | Pre-PR validation |
| CI/CD | GitHub Actions | PR-based human approval |
| Dashboards | Grafana | **Output** surface |
| Tickets | Jira | Gap workflow |

### Vendor decision: UXCam, with CleverTap evaluated and rejected

The deciding property is **auto-capture**. UXCam records taps, gestures and screens
with no per-interaction instrumentation, so journeys exist the moment the SDK ships
rather than after an instrumentation project the mobile roadmap has to absorb. That
matters because GoldenFlow's premise is *learn from production without asking the app
team for work*, and a manual-instrumentation vendor inverts it.

CleverTap was evaluated in full and reversed. It covers analytics well but captures no
UI interactions without explicit code. Measured: journeys survive either way (both
mine 59 variants), but `action_coverage` falls to 0% and revenue attributable to
journeys drops to zero â€” which removes half the evidence behind separating
*most-walked* from *most-valuable*, the product's central claim.

The price of auto-capture is that occlusion is mandatory and **fails open**. That cost
is accepted deliberately; [docs/pii-policy.md](docs/pii-policy.md) is the compensating
control and the 1%-rollout replay review is what actually catches a miss. Full
assessment in [docs/uxcam-integration.md](docs/uxcam-integration.md).

### One deliberate correction from the original concept

Grafana is a visualisation layer, not a journey source. Prometheus aggregates away the per-session
identity and ordering that journey reconstruction requires â€” putting `user_id` in a Prometheus
label is a cardinality anti-pattern, not a configuration choice. Grafana's correct role is
displaying GoldenFlow's output and supplying health signals that *risk-rank* journeys. Journey
reconstruction reads from the event store.
