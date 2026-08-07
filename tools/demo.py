"""Run the whole GoldenFlow pipeline end to end, from a clean slate.

    python tools/demo.py              # full run
    python tools/demo.py --fast       # smaller sample, quicker
    python tools/demo.py --keep       # don't wipe existing artifacts first

Every step prints the command it runs, so you can copy any single line and
re-run it yourself. Steps that are *supposed* to fail are marked EXPECT-FAIL -
those demonstrate the gates actually blocking, which is the part that matters.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "artifacts"

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


class Runner:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.step = 0

    def banner(self, text: str) -> None:
        print(f"\n{BOLD}{'=' * 78}\n  {text}\n{'=' * 78}{RESET}")

    def run(self, *args: str, expect_fail: bool = False, quiet: bool = False) -> int:
        self.step += 1
        cmd = [sys.executable, "-m", "goldenflow.cli", *args]
        shown = "goldenflow " + " ".join(args)
        tag = f"{DIM}[EXPECT-FAIL]{RESET} " if expect_fail else ""
        print(f"\n{BOLD}{self.step:>2}.{RESET} {tag}{DIM}${RESET} {shown}\n")

        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        output = (proc.stdout + proc.stderr).rstrip()
        if output and not quiet:
            print("\n".join(f"    {line}" for line in output.splitlines()))
        elif quiet:
            print(f"    {DIM}({len(output.splitlines())} lines suppressed){RESET}")

        ok = (proc.returncode != 0) if expect_fail else (proc.returncode == 0)
        verdict = f"{GREEN}OK{RESET}" if ok else f"{RED}UNEXPECTED{RESET}"
        print(f"\n    -> exit {proc.returncode}  [{verdict}]")
        if not ok:
            self.failures.append(shown)
        return proc.returncode

    def script(self, *args: str) -> None:
        self.step += 1
        cmd = [sys.executable, *args]
        print(f"\n{BOLD}{self.step:>2}.{RESET} {DIM}${RESET} python {' '.join(args)}\n")
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        print("\n".join(f"    {l}" for l in (proc.stdout + proc.stderr).rstrip().splitlines()))
        if proc.returncode != 0:
            self.failures.append(" ".join(args))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fast", action="store_true", help="Smaller sample")
    ap.add_argument("--keep", action="store_true", help="Keep existing artifacts")
    args = ap.parse_args()

    sessions = "1500" if args.fast else "6000"
    r = Runner()

    if not args.keep and ART.exists():
        shutil.rmtree(ART)
        print(f"{DIM}Wiped {ART}{RESET}")
    ART.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- data
    r.banner("SETUP - generate synthetic production telemetry")
    print(f"{DIM}  Synthetic, not real. Deliberately seeded with the defects a real\n"
          f"  export contains: tag drift, missing session IDs, anonymous traffic.{RESET}")
    r.script("tools/generate_sample_events.py", "--sessions", sessions)

    # -------------------------------------------------------------- phase 0
    r.banner("PHASE 0 - Foundation & Readiness")

    print(f"\n{DIM}  The taxonomy is the join key between production and QA.{RESET}")
    r.run("taxonomy", "validate", "config/taxonomy.yaml")

    print(f"\n{DIM}  Can this app's telemetry support journey mining at all?{RESET}")
    r.run("readiness", "audit",
          "--taxonomy", "config/taxonomy.yaml",
          "--events", "artifacts/sample-events.jsonl",
          "--out", "artifacts/readiness-report.json")

    print(f"\n{DIM}  The write-once 'before'. Phase 6 has no claim without it.{RESET}")
    r.run("baseline", "capture", "--app", "acme-shop", "--release", "8.2.0",
          "--by", "qe-lead", "--out", "artifacts/baseline-8.2.0.json",
          "--coverage", "62", "--escaped-defects", "14", "--mttd", "36",
          "--suite-runtime", "180", "--flaky", "9.5", "--authoring-hours", "40",
          "--test-count", "820")

    print(f"\n{DIM}  A baseline that can be quietly rewritten is not a baseline.{RESET}")
    r.run("baseline", "capture", "--app", "acme-shop", "--release", "8.2.0",
          "--out", "artifacts/baseline-8.2.0.json", "--coverage", "99",
          expect_fail=True)

    print(f"\n{DIM}  Tests that must never be pruned by traffic signal.{RESET}")
    r.run("registry", "check", "config/protected-tests.yaml")

    print(f"\n{DIM}  GDPR erasure: near-zero traffic, non-negotiable coverage.{RESET}")
    r.run("registry", "test", "config/protected-tests.yaml",
          "tests/account/delete/test_right_to_erasure.py", expect_fail=True)

    print(f"\n{DIM}  An ordinary high-traffic test stays prunable.{RESET}")
    r.run("registry", "test", "config/protected-tests.yaml",
          "tests/browse/test_home_feed.py")

    r.banner("PHASE 0 GATE - M2 go/no-go")
    r.run("phase0", "gate",
          "--taxonomy", "config/taxonomy.yaml",
          "--events", "artifacts/sample-events.jsonl",
          "--registry", "config/protected-tests.yaml",
          "--baseline", "artifacts/baseline-8.2.0.json")

    print(f"\n{DIM}  And the same gate with an impossible bar, to prove it blocks.{RESET}")
    r.run("phase0", "gate",
          "--taxonomy", "config/taxonomy.yaml",
          "--events", "artifacts/sample-events.jsonl",
          "--registry", "config/protected-tests.yaml",
          "--threshold", "99", expect_fail=True)

    # -------------------------------------------------------------- phase 1
    r.banner("PHASE 1 - Data Foundation")

    print(f"\n{DIM}  The event schema contract, reviewed like an API.{RESET}")
    r.run("tracking", "validate", "config/tracking-plan.yaml")

    print(f"\n{DIM}  Warehouse DDL for the real deployment.{RESET}")
    r.run("store", "ddl", "--dialect", "bigquery", quiet=True)

    print(f"\n{DIM}  Normalise every vendor shape; join crashes into the timeline.{RESET}")
    r.run("ingest",
          "--events", "artifacts/sample-events.jsonl",
          "--taxonomy", "config/taxonomy.yaml",
          "--store", "artifacts/events.db",
          "--crashlytics", "artifacts/sample-crashlytics.jsonl",
          "--sentry", "artifacts/sample-sentry.jsonl")

    print(f"\n{DIM}  Garbage here becomes confident nonsense in Phase 2.{RESET}")
    r.run("quality", "check",
          "--store", "artifacts/events.db",
          "--taxonomy", "config/taxonomy.yaml",
          "--plan", "config/tracking-plan.yaml")

    print(f"\n{DIM}  Enforcement for docs/pii-policy.md.{RESET}")
    r.run("pii", "scan", "--store", "artifacts/events.db")

    r.banner("PII SCANNER - proving it actually catches something")
    print(f"{DIM}  Same generator, --inject-pii, planting contact data in support\n"
          f"  tickets. Note the report redacts values rather than echoing them.{RESET}")
    r.script("tools/generate_sample_events.py", "--sessions", "600", "--inject-pii",
             "--out", "artifacts/tainted-events.jsonl",
             "--crashes-out", "artifacts/t-crash.jsonl",
             "--sentry-out", "artifacts/t-sentry.jsonl")
    r.run("ingest", "--events", "artifacts/tainted-events.jsonl",
          "--taxonomy", "config/taxonomy.yaml",
          "--store", "artifacts/tainted.db", quiet=True)
    r.run("pii", "scan", "--store", "artifacts/tainted.db", expect_fail=True)

    r.banner("PHASE 1 GATE - M5 go/no-go")
    r.run("phase1", "gate",
          "--store", "artifacts/events.db",
          "--taxonomy", "config/taxonomy.yaml",
          "--plan", "config/tracking-plan.yaml")

    # -------------------------------------------------------------- phase 2
    r.banner("PHASE 2 - Journey Intelligence Engine (Agent 1)")

    print(f"\n{DIM}  Process mining: sessionize -> DFG -> variants -> archetypes\n"
          f"  -> score -> name. Watch the ranking: the most-walked path is NOT\n"
          f"  the top journey, which is the entire product thesis.{RESET}")
    r.run("journeys", "mine",
          "--store", "artifacts/events.db",
          "--taxonomy", "config/taxonomy.yaml",
          "--top", "5", "--out", "artifacts/journeys.json")

    print(f"\n{DIM}  Every score is interrogable. A ranking nobody can question is\n"
          f"  a ranking nobody will act on.{RESET}")
    r.run("journeys", "explain",
          "--store", "artifacts/events.db",
          "--taxonomy", "config/taxonomy.yaml", "aj_001")

    r.banner("JOURNEY DRIFT - stable traffic vs. a shifted world")
    print(f"{DIM}  First: same generator, different seed. An unchanged process must\n"
          f"  NOT read as drift, or the metric is worthless.{RESET}")
    r.script("tools/generate_sample_events.py", "--sessions", sessions,
             "--seed", "4242", "--out", "artifacts/wk3-events.jsonl",
             "--crashes-out", "artifacts/wk3-crash.jsonl",
             "--sentry-out", "artifacts/wk3-sentry.jsonl")
    r.run("ingest", "--events", "artifacts/wk3-events.jsonl",
          "--taxonomy", "config/taxonomy.yaml", "--store", "artifacts/wk3.db",
          quiet=True)
    r.run("drift", "compare", "--baseline-store", "artifacts/events.db",
          "--current-store", "artifacts/wk3.db", "--taxonomy", "config/taxonomy.yaml")

    print(f"\n{DIM}  Now a genuinely shifted world: a new wallet payment route ships,\n"
          f"  payment failures spike, the classic purchase path loses share.{RESET}")
    r.script("tools/generate_sample_events.py", "--sessions", sessions,
             "--seed", "9001", "--drift", "--out", "artifacts/q2-events.jsonl",
             "--crashes-out", "artifacts/q2-crash.jsonl",
             "--sentry-out", "artifacts/q2-sentry.jsonl")
    r.run("ingest", "--events", "artifacts/q2-events.jsonl",
          "--taxonomy", "config/taxonomy.yaml", "--store", "artifacts/q2.db",
          quiet=True)
    r.run("drift", "compare", "--baseline-store", "artifacts/events.db",
          "--current-store", "artifacts/q2.db", "--taxonomy", "config/taxonomy.yaml",
          expect_fail=True)

    r.banner("JOURNEY GRAPH - Neo4j export")
    print(f"{DIM}  Phase 3 projects every Appium test as a path over this same node\n"
          f"  set, which turns coverage analysis into a graph diff.{RESET}")
    r.run("graph", "export", "--store", "artifacts/events.db",
          "--taxonomy", "config/taxonomy.yaml", "--format", "cypher",
          "--out", "artifacts/journey-graph.cypher")
    r.run("graph", "export", "--store", "artifacts/events.db",
          "--taxonomy", "config/taxonomy.yaml", "--format", "gap-query")

    r.banner("PHASE 2 GATE - M8 go/no-go")
    r.run("phase2", "gate",
          "--store", "artifacts/events.db",
          "--taxonomy", "config/taxonomy.yaml")

    # -------------------------------------------------------------- phase 3
    r.banner("PHASE 3 - QA Asset Graph & Gap Analysis")
    print(f"{DIM}  The first phase that ships value on its own. No generation yet -\n"
          f"  just 'here is what your suite does not cover'.{RESET}")

    print(f"\n{DIM}  Parse a real Appium suite: Python via AST, Java and JS via\n"
          f"  heuristics. Comments are stripped, so a Page Object named only in a\n"
          f"  TODO never counts as coverage.{RESET}")
    r.run("suite", "parse", "--suite", "fixtures/appium-suite",
          "--taxonomy", "config/taxonomy.yaml")

    print(f"\n{DIM}  Project every test as a path over the Phase 2 journey graph.{RESET}")
    r.run("coverage", "show",
          "--store", "artifacts/events.db", "--taxonomy", "config/taxonomy.yaml",
          "--suite", "fixtures/appium-suite",
          "--registry", "config/protected-tests.yaml", "--limit", "12")

    print(f"\n{DIM}  The diff. Watch two things: payment-failure recovery is untested\n"
          f"  despite 72 sessions, and account deletion stays HIGH severity on\n"
          f"  TWO sessions because it touches a critical screen.{RESET}")
    r.run("gaps", "analyse",
          "--store", "artifacts/events.db", "--taxonomy", "config/taxonomy.yaml",
          "--suite", "fixtures/appium-suite",
          "--registry", "config/protected-tests.yaml", "--limit", "8",
          "--out", "artifacts/gap-report.json",
          "--jira-out", "artifacts/jira-tickets.json",
          "--metrics-out", "artifacts/goldenflow.prom")

    r.banner("PHASE 3 GATE - M10 go/no-go")
    r.run("phase3", "gate",
          "--store", "artifacts/events.db", "--taxonomy", "config/taxonomy.yaml",
          "--suite", "fixtures/appium-suite",
          "--registry", "config/protected-tests.yaml")

    # -------------------------------------------------------------- phase 4
    r.banner("PHASE 4 - Locator Resolution")
    print(f"{DIM}  Analytics gives screen NAMES. Appium needs LOCATORS. This is the\n"
          f"  bridge most proposals omit entirely.{RESET}")

    print(f"\n{DIM}  Three sources merged. Watch aj_007 (payment failure) and aj_015\n"
          f"  (account deletion): Phase 3 flagged both as gaps, and the crawl dump\n"
          f"  makes them executable - neither has a Page Object.{RESET}")
    r.run("locators", "resolve",
          "--store", "artifacts/events.db", "--taxonomy", "config/taxonomy.yaml",
          "--suite", "fixtures/appium-suite",
          "--crawl", "fixtures/ui-dumps/v8.2.0", "--app-version", "8.2.0",
          "--limit", "6", "--out", "artifacts/locators.json")

    print(f"\n{DIM}  Three payment rows share one accessibility ID. Ambiguity must\n"
          f"  beat strategy rank - a test tapping the wrong row of three fails in\n"
          f"  a way that looks like a product bug.{RESET}")
    r.run("locators", "show",
          "--store", "artifacts/events.db", "--taxonomy", "config/taxonomy.yaml",
          "--suite", "fixtures/appium-suite",
          "--crawl", "fixtures/ui-dumps/v8.2.0", "payment_method")

    print(f"\n{DIM}  Screen identity across a release. Every resource-id changed in\n"
          f"  v9; the fingerprint deliberately ignores them.{RESET}")
    r.run("fingerprint", "compare",
          "--baseline", "fixtures/ui-dumps/v8.2.0",
          "--current", "fixtures/ui-dumps/v9.0.0")

    print(f"\n{DIM}  Self-healing. Accessibility IDs survive the refactor untouched;\n"
          f"  resource IDs all break at once. That is the ranking's evidence.{RESET}")
    r.run("locators", "heal",
          "--baseline", "fixtures/ui-dumps/v8.2.0",
          "--current", "fixtures/ui-dumps/v9.0.0",
          "--taxonomy", "config/taxonomy.yaml", "--suite", "fixtures/appium-suite",
          "--limit", "8")

    r.banner("PHASE 4 GATE - M12 go/no-go")
    r.run("phase4", "gate",
          "--store", "artifacts/events.db", "--taxonomy", "config/taxonomy.yaml",
          "--suite", "fixtures/appium-suite",
          "--crawl", "fixtures/ui-dumps/v8.2.0",
          "--baseline", "fixtures/ui-dumps/v8.2.0",
          "--current", "fixtures/ui-dumps/v9.0.0")

    # -------------------------------------------------------------- phase 5
    r.banner("PHASE 5 - Generation & Validation")
    print(f"{DIM}  Specs are built deterministically from production data. The\n"
          f"  generator decides only how the code READS, never what it tests.{RESET}")
    r.run("generate",
          "--store", "artifacts/events.db", "--taxonomy", "config/taxonomy.yaml",
          "--suite", "fixtures/appium-suite",
          "--crawl", "fixtures/ui-dumps/v8.2.0", "--runs", "2",
          "--show", "--out", "artifacts/generated")

    print(f"\n{DIM}  No device farm exists here, so nothing validates and no PR\n"
          f"  opens. That is the gate working, not a bug. --preview renders the\n"
          f"  artifact behind a banner so it can still be reviewed.{RESET}")
    r.run("pr", "open",
          "--store", "artifacts/events.db", "--taxonomy", "config/taxonomy.yaml",
          "--suite", "fixtures/appium-suite",
          "--crawl", "fixtures/ui-dumps/v8.2.0", "--runs", "1", "--preview")

    print(f"\n{DIM}  Deletion needs TWO independent signals plus a registry check.\n"
          f"  Watch all four candidates get held back.{RESET}")
    r.run("pr", "prune",
          "--store", "artifacts/events.db", "--taxonomy", "config/taxonomy.yaml",
          "--suite", "fixtures/appium-suite",
          "--registry", "config/protected-tests.yaml",
          "--removed-screen", "legacy_promo")

    r.banner("PHASE 5 GATE - M15 go/no-go  (EXPECTED TO BLOCK)")
    print(f"{DIM}  The blocking criterion is device validation, and it blocks\n"
          f"  correctly. A syntax check is not evidence a test works.{RESET}")
    r.run("phase5", "gate",
          "--store", "artifacts/events.db", "--taxonomy", "config/taxonomy.yaml",
          "--suite", "fixtures/appium-suite",
          "--crawl", "fixtures/ui-dumps/v8.2.0", "--runs", "1",
          expect_fail=True)

    # -------------------------------------------------------------- phase 6
    r.banner("PHASE 6 - Closed Loop & Governance")
    print(f"{DIM}  The original diagram's loop read 'Better Quality -> Happier\n"
          f"  Users'. That is a narrative. This measures against real escaped\n"
          f"  defects instead.{RESET}")
    r.run("outcomes", "report",
          "--store", "artifacts/events.db", "--taxonomy", "config/taxonomy.yaml",
          "--suite", "fixtures/appium-suite",
          "--crawl", "fixtures/ui-dumps/v8.2.0",
          "--defects", "fixtures/escaped-defects.json", "--runs", "1",
          "--out", "artifacts/outcomes.json")

    print(f"\n{DIM}  Weights started as a guess. This replaces the guess with\n"
          f"  evidence - bounded, explained, and never self-applied.{RESET}")
    r.run("tune", "weights",
          "--store", "artifacts/events.db", "--taxonomy", "config/taxonomy.yaml",
          "--suite", "fixtures/appium-suite",
          "--crawl", "fixtures/ui-dumps/v8.2.0",
          "--defects", "fixtures/escaped-defects.json", "--runs", "1",
          "--out", "artifacts/weight-proposal.json")

    print(f"\n{DIM}  Lineage for every artifact, and per-unit cost. A system that\n"
          f"  quietly costs more than the QE time it saves is a failure however\n"
          f"  good its findings are.{RESET}")
    r.run("audit", "show",
          "--store", "artifacts/events.db", "--taxonomy", "config/taxonomy.yaml",
          "--suite", "fixtures/appium-suite",
          "--crawl", "fixtures/ui-dumps/v8.2.0", "--runs", "2",
          "--out", "artifacts/lineage.json")

    r.banner("PHASE 6 GATE - M18 go/no-go")
    r.run("phase6", "gate",
          "--store", "artifacts/events.db", "--taxonomy", "config/taxonomy.yaml",
          "--suite", "fixtures/appium-suite",
          "--crawl", "fixtures/ui-dumps/v8.2.0",
          "--defects", "fixtures/escaped-defects.json", "--runs", "1")

    # -------------------------------------------------------------- phase 7
    r.banner("PHASE 7 - Multi-App Scale & Productization")
    print(f"{DIM}  One app is a pilot. Three is a platform - and the difference is\n"
          f"  mostly removing the platform team from the critical path.{RESET}")
    r.run("tenant", "list", "config/tenants.yaml")

    print(f"\n{DIM}  Isolation here is about blast radius, not privacy. A shared\n"
          f"  taxonomy means one team's edit silently re-keys another's graph.{RESET}")
    r.run("tenant", "check", "config/tenants.yaml")

    print(f"\n{DIM}  RBAC guards exactly the operations that are hard to undo.{RESET}")
    r.run("tenant", "permissions", "--role", "viewer")

    print(f"\n{DIM}  The piece that takes a new app from months to weeks: draft the\n"
          f"  taxonomy and tracking plan from what the app already emits.{RESET}")
    r.run("onboard", "scaffold",
          "--events", "artifacts/sample-events.jsonl", "--app", "acme-rewards",
          "--limit", "6", "--out", "artifacts/onboard/acme-rewards")

    print(f"\n{DIM}  Adding a vendor is a registration, not an edit to the ingest\n"
          f"  path. Auto-capture is surfaced because it moves onboarding by weeks.{RESET}")
    r.run("connectors", "list")

    print(f"\n{DIM}  Portfolio view. Note acme-pay reads 'no-data', not 'healthy' -\n"
          f"  an absent measurement must never present as a good one.{RESET}")
    r.run("portfolio", "report", "--tenants", "config/tenants.yaml")

    r.banner("PHASE 7 GATE - M24 go/no-go")
    r.run("phase7", "gate",
          "--tenants", "config/tenants.yaml",
          "--events", "artifacts/sample-events.jsonl")

    # -------------------------------------------------------------- summary
    r.banner("SUMMARY")
    if r.failures:
        print(f"{RED}  {len(r.failures)} step(s) behaved unexpectedly:{RESET}")
        for f in r.failures:
            print(f"    - {f}")
        return 1
    print(f"{GREEN}  All {r.step} steps behaved as expected.{RESET}")
    print(f"\n  Gates passed:  Phase 0 (M2), Phase 1 (M5), Phase 2 (M8),")
    print(f"                 Phase 3 (M10), Phase 4 (M12), Phase 6 (M18),")
    print(f"                 Phase 7 (M24)")
    print(f"  Gates blocked: readiness at 99% threshold, baseline overwrite,")
    print(f"                 protected-test deletion, PII detection,")
    print(f"                 drift ALERT on the shifted world,")
    print(f"                 {BOLD}Phase 5 (M15) - no device farm{RESET}")
    print(f"\n  Phase 5 blocking is the honest outcome, not a defect. Tests")
    print(f"  generate and compile; none ran on a device, so none is eligible.")
    print(f"\n  All 8 phases implemented. Not proven here: the SLA over two")
    print(f"  quarters and real onboarding duration - both need calendar time.")
    print(f"\n  Artifacts in {ART}")
    print(f"  Run 'python -m pytest -q' for the 512-test suite.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
