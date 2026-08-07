# Making GoldenFlow Real

Everything built so far runs against **synthetic data**. This document is the honest
path from that to a system producing findings a QE team acts on.

Three sections: what runs today, what is missing, and the order to do it in.

---

## Part 1 — Run what exists (10 minutes)

```bash
cd d:/QX/goldenFlow
python -m pip install -e ".[dev,mining]"
python tools/demo.py --fast          # 54 steps, all 8 phases
python -m pytest -q                  # 512 tests
```

That works right now, on your machine, with no accounts. It proves the pipeline is
correct. It proves **nothing** about any real app — see [QUICKSTART.md](QUICKSTART.md)
for how to poke at it.

---

## Part 2 — What is missing

Six seams were deliberately left open rather than faked. Estimates are engineer-weeks
and are estimates, not quotes.

### Hard blockers — nothing works in production without these

| # | Gap | Where | Effort |
|---|---|---|---|
| 1 | **UXCam data fetcher** | nothing calls the API | 1 wk |
| 2 | **Warehouse loader** | [`store.py`](goldenflow/phase1/store.py) generates DDL, SQLite is a reference impl | 1–2 wk |
| 3 | **Device farm runner** | [`DeviceFarmRunner.run()`](goldenflow/phase5/validation.py) **raises** | 1–2 wk |

**1. UXCam fetcher.** The adapter takes records; nothing retrieves them. Write a
client for `api.uxcam.com/v2/event` with `appid`/`apikey`, paginate, and hand the
records to `load_uxcam_export()`. Prefer their bulk/streaming export over polling if
your plan has it.

**2. Warehouse loader.** `goldenflow store ddl --dialect bigquery` emits correct
partitioned DDL. What is missing is the code that writes `CanonicalEvent` rows into
it. SQLite works to roughly a few million events — genuinely fine for a pilot, and
the reason this is second not first.

**3. Device farm runner.** This one **blocks Phase 5 entirely and by design**. It
raises rather than returning a default, because a runner that silently reports
success is the most dangerous thing this codebase could contain. Implement `run()`
against BrowserStack App Automate, Firebase Test Lab, or a self-hosted Appium grid.
Contract: upload the build, run one test on one profile, return pass/fail and
duration.

### Soft blockers — the system degrades but runs

| # | Gap | Consequence if skipped | Effort |
|---|---|---|---|
| 4 | LLM client wiring | Template generator works; output reads less like your repo | 2 d |
| 5 | Crawler (Robo Test) integration | Hand-maintained UI dumps; Phase 4 resolution stalls at ~89% | 1 wk |
| 6 | Scheduler (Dagster/Airflow) | Everything stays CLI-invoked; no continuous loop | 1 wk |
| 7 | Jira / Grafana submission | Payloads generated, nobody POSTs them | 2 d |

`LlmGenerator` takes any `Callable[[str], str]` — wire it to the Claude API in about
ten lines. Deliberately no provider is embedded.

### Optional

Neo4j loading (the in-memory graph and Cypher export already work), and separate
iOS/Android suites for genuine parity analysis.

**Total engineering: roughly 6–9 engineer-weeks.** That is not the long pole.

---

## Part 3 — The long pole is not code

Two things gate everything and neither is a pull request:

```
vendor contracts ──────────────┐
                               ├──► SDK ships ──► users update ──► data exists
privacy sign-off ──► SDK work ─┘        (2-4 wk)      (4-8 wk)
```

**Vendor contracts** (UXCam, device farm) have procurement lead times measured in
months. Start them on day one, in parallel with everything else.

**Privacy sign-off blocks the SDK.** UXCam records real users' screens; occlusion is
mandatory and [fails open](docs/pii-policy.md). No SDK ships to production traffic
until Legal signs off. Retrofitting does not help — data already captured is already
captured.

**App version adoption is the slowest step nobody plans for.** After the SDK ships you
still wait weeks for enough users to update. And the population that updates fast is
*biased* — newer devices, different demographics — so early journeys over-represent
them.

---

## Part 4 — Sequence

### The important strategic point

**Phase 3 delivers value without Phase 5.** "Here are the journeys your suite does not
cover" is worth acting on before any test generation exists — and it needs no device
farm, no LLM, and no scheduler.

So do not build toward generation first. Build toward the gap report, ship it, earn
the trust, then automate.

### Weeks 0–2 — decide and unblock

- [ ] **Pick the pilot app.** Highest-traffic, actively developed, has *some* Appium suite. Not the one being rewritten.
- [ ] Start UXCam and device-farm procurement — the long pole
- [ ] Book the privacy review; hand Legal [docs/pii-policy.md](docs/pii-policy.md)
- [ ] **Capture the Phase 0 baseline before anything changes.** Write-once, and every later claim is measured against it
- [ ] Draft the taxonomy: `goldenflow onboard scaffold` if you have any existing analytics, otherwise by hand

### Weeks 2–6 — instrument (mobile team)

- [ ] UXCam SDK + `optIntoSchematicRecordings()` in `Application.onCreate`/`AppDelegate`
- [ ] `UXCam.tagScreenName()` on every screen, matching `config/taxonomy.yaml` — auto-tagging gives class names that churn on refactor
- [ ] **Occlusion on every `high` and `critical` screen**
- [ ] Disable location capture
- [ ] Verify the kill switch *before* the 1% rollout
- [ ] Ship. Roll 1% → 10% → 50% → 100%, **watching replays at 1%** — the only control that catches an unmasked screen

Parallel, platform team: build gaps **#1 and #2** (fetcher, loader).

### Weeks 6–10 — first real data

- [ ] `goldenflow readiness audit` against a real export. **This is a genuine go/no-go** — a low score means the app is not minable yet, and finding that here is the point
- [ ] Ingest, run `phase1 gate`, fix data quality
- [ ] Mine journeys; show the top 10 to QE leads. If they do not recognise them, the mining is wrong — stop and fix it
- [ ] Parse the Appium suite, run `gaps analyse`

### Weeks 10–14 — ship the gap report

- [ ] Wire Jira (gap #7); dashboards in Grafana
- [ ] **Have QE act on 3–5 gaps by hand.** Do not generate anything yet
- [ ] Measure: were the gaps real? That number decides whether Phase 5 is worth building

### Weeks 14–20 — generation, only if the gap report earned it

- [ ] Crawler integration (#5) → Phase 4 resolution
- [ ] **Device farm runner (#3)** → Phase 5 unblocks
- [ ] LLM wiring (#4), scheduler (#6)
- [ ] First generated PR. Human approves or rejects; either is data

### Month 6+ — the loop

- [ ] Feed escaped defects into `goldenflow outcomes report`
- [ ] Weight proposals from `goldenflow tune weights` — reviewed, never auto-applied
- [ ] Compare against the Phase 0 baseline. **That comparison is the whole product**

---

## Part 5 — People

| Role | When | Doing |
|---|---|---|
| QE Architect | throughout | Owns the taxonomy. The single most important role |
| Mobile engineer | wk 2–6 | SDK, screen tags, occlusion |
| Data engineer | wk 2–10 | Fetcher, loader, quality monitors |
| Backend engineer | wk 10–20 | Device runner, scheduler, integrations |
| Legal / Privacy | wk 0–2 | Sign-off. Blocking |
| QE leads | wk 6+ | Validate journeys, act on gaps, review PRs |

Peak ~4 FTE for a pilot. The 13-FTE figure in [ROADMAP.md](ROADMAP.md) is the
full-platform build, not this.

---

## Part 6 — Decisions to make before starting

**Warehouse: SQLite, BigQuery or ClickHouse?** SQLite is genuinely fine for a pilot
and defers gap #2. Choose a warehouse when you outgrow it, not before.

**Device farm: BrowserStack or Firebase Test Lab?** Test Lab is cheaper and pairs
with Robo Test for the crawler (#5 and #3 in one). BrowserStack has broader device
coverage. Test Lab if you are already Firebase.

**Do you have per-platform Appium suites?** If iOS and Android share one suite, parity
analysis cannot tell you anything. Worth knowing before you expect it to.

**Who owns the taxonomy?** Not a shared responsibility. One named person, or it rots.

---

## The honest summary

- **Code missing: ~6–9 engineer-weeks.** Real, but bounded and specified
- **Calendar to first real gap report: ~10 weeks**, most of it waiting for a mobile release and user adoption
- **The biggest risk is not technical.** It is that the pilot app's instrumentation cannot support journey mining — which is exactly what Phase 0's readiness audit exists to find, cheaply, before you build on it
- **The gap report is the product.** Generation is the follow-on, and it should have to earn its place
