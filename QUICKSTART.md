# Testing GoldenFlow

Everything below runs locally with no cloud accounts, no vendor keys, and no
mobile app. Python 3.11+ is the only requirement.

---

## Fastest path — one command

```bash
cd d:/QX/goldenFlow
python -m pip install -e .
python tools/demo.py --fast
```

19 steps, about 30 seconds. It generates synthetic telemetry, runs both phases end
to end, and — importantly — includes steps that are **supposed to fail**, marked
`[EXPECT-FAIL]`. A gate that only ever passes is not a gate, so the demo proves the
blocking paths work too:

| Step | Should block because |
|---|---|
| Readiness at 99% threshold | The bar is set impossibly high |
| Baseline overwrite | Baselines are write-once |
| Deleting a GDPR erasure test | Protected registry refuses |
| PII scan on tainted data | Contact data planted in event properties |

Drop `--fast` for a 6,000-session run. Add `--keep` to leave existing artifacts alone.

---

## The test suite

```bash
python -m pytest -q          # 188 tests, ~4 seconds
python -m pytest -v          # see every test name
python -m pytest tests/test_registry.py -v
```

The test names are written to be read as documentation. A few worth looking at:

- `test_blocker_overrides_a_passing_aggregate_score` — an app scoring 78% overall
  still fails if session IDs are broken, because averaging would hide it
- `test_findings_never_contain_the_matched_value` — a PII report that quotes PII is
  itself a leak
- `test_canonical_low_traffic_high_consequence_tests_are_protected` — the concrete
  list a traffic-ranked pruner would delete first
- `test_signal_before_the_session_starts_is_left_unlocated` — a mislocated crash is
  worse than an unlocated one

---

## Running it yourself, step by step

After `pip install -e .` the `goldenflow` command is on your PATH. Without
installing, substitute `python -m goldenflow.cli` for `goldenflow` everywhere.

### 1. Generate synthetic telemetry

```bash
python tools/generate_sample_events.py --sessions 6000
```

Writes events, Crashlytics crashes and Sentry errors to `artifacts/`. Deliberately
seeded with the defects a real export contains: tag drift, ~2% missing session IDs,
~18% anonymous traffic, and rare flows at sub-1% weights.

### 2. Phase 0 — is this app even minable?

```bash
goldenflow taxonomy validate config/taxonomy.yaml
goldenflow readiness audit --taxonomy config/taxonomy.yaml \
    --events artifacts/sample-events.jsonl
goldenflow registry check config/protected-tests.yaml
```

### 3. Phase 1 — build the source of truth

```bash
goldenflow ingest --events artifacts/sample-events.jsonl \
    --taxonomy config/taxonomy.yaml --store artifacts/events.db \
    --crashlytics artifacts/sample-crashlytics.jsonl \
    --sentry artifacts/sample-sentry.jsonl

goldenflow quality check --store artifacts/events.db \
    --taxonomy config/taxonomy.yaml --plan config/tracking-plan.yaml

goldenflow pii scan --store artifacts/events.db
```

### 4. The gates

```bash
goldenflow phase0 gate --taxonomy config/taxonomy.yaml \
    --events artifacts/sample-events.jsonl \
    --registry config/protected-tests.yaml \
    --baseline artifacts/baseline-8.2.0.json

goldenflow phase1 gate --store artifacts/events.db \
    --taxonomy config/taxonomy.yaml --plan config/tracking-plan.yaml
```

Both exit non-zero on NO-GO, so they drop straight into CI.

---

## Break it on purpose

This is the useful part. The system is only worth anything if it fails loudly.

**Ambiguous analytics tag** — the defect that silently merges two journeys into one:

```bash
# In config/taxonomy.yaml, add "Cart" to the analytics_tags of `home`
goldenflow taxonomy validate config/taxonomy.yaml
# ERROR unique_analytics_tags: tag 'Cart' maps to 2 screens
```

**Unmasked payment screen** — set `occlusion_required: false` on `payment_method`:

```bash
goldenflow taxonomy validate config/taxonomy.yaml
# ERROR occlusion_policy [payment_method]: session replay would record this screen
```

**PII in event properties:**

```bash
python tools/generate_sample_events.py --sessions 600 --inject-pii \
    --out artifacts/tainted.jsonl --crashes-out artifacts/c.jsonl \
    --sentry-out artifacts/s.jsonl
goldenflow ingest --events artifacts/tainted.jsonl \
    --taxonomy config/taxonomy.yaml --store artifacts/tainted.db
goldenflow pii scan --store artifacts/tainted.db
```

**Deleting a protected test:**

```bash
goldenflow registry test config/protected-tests.yaml \
    tests/account/delete/test_right_to_erasure.py
# PROTECTED under data_rights ... exit 1
```

**Stale data** — edit `tools/generate_sample_events.py` and change the clock offset
to `rng.randint(86400, 172800)`, regenerate, re-ingest, then run `quality check`.
Freshness fails at >4h.

---

## Inspect the store directly

It is plain SQLite, so nothing is hidden:

```bash
python -c "import sqlite3;c=sqlite3.connect('artifacts/events.db');print(c.execute('SELECT screen_id, COUNT(*) FROM events GROUP BY 1 ORDER BY 2 DESC LIMIT 10').fetchall())"
```

Or from Python:

```python
from goldenflow.phase1.store import EventStore
with EventStore("artifacts/events.db") as s:
    print(s.count_events(), "events across", len(s.session_ids()), "sessions")
    for e in s.session_events(s.session_ids()[0]):
        print(f"  {e.timestamp:%H:%M:%S}  {e.event_name:16} {e.screen_id}")
```

That last loop prints one real user journey, which is what Phase 2 will mine.

---

## Point it at your own data

Nothing here is bound to the sample. To try a real export:

1. Replace `config/taxonomy.yaml` with your screens (the join key is
   `analytics_tag -> screen_id -> page_object`)
2. Replace `config/tracking-plan.yaml` with your events
3. Run with `--source rudderstack`, `firebase`, or `mixpanel`:

```bash
goldenflow ingest --events your-export.jsonl --source firebase \
    --taxonomy config/taxonomy.yaml --store artifacts/real.db
```

Input may be JSONL or a JSON array. The readiness audit is the honest first step —
it tells you whether the data can support journey mining before you invest in it.

---

## What "passing" does and does not mean

The gates pass against **synthetic data**. That proves the pipeline is correct and
the checks fire; it does not prove anything about a real app.

The vendor adapters are written against documented export shapes and unit-tested,
but have never seen a live Firebase or RudderStack payload. First contact with real
data will surface field-shape surprises — that is expected, and the rejection and
orphan counters exist to make those surprises visible rather than silent.
