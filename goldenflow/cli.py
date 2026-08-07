"""GoldenFlow command line interface.

Every Phase 0 deliverable is reachable from here, and every check returns a
non-zero exit code on failure so it can be wired straight into CI. The contract
is only real if a build breaks when it is violated.

    goldenflow taxonomy validate config/taxonomy.yaml
    goldenflow readiness audit --taxonomy config/taxonomy.yaml --events sample.jsonl
    goldenflow registry check config/protected-tests.yaml
    goldenflow phase0 gate --taxonomy ... --events ... --registry ...
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from types import SimpleNamespace
from pathlib import Path
from typing import Any, Iterator

from goldenflow import __version__
from goldenflow.phase0.baseline import BaselineSnapshot
from goldenflow.phase0.readiness import DEFAULT_GATE_THRESHOLD, audit_readiness
from goldenflow.phase0.registry import ProtectedTestRegistry
from goldenflow.phase0.taxonomy import Severity, Taxonomy
from goldenflow.phase1.ingest import normalise_stream
from goldenflow.phase1.pii import scan_events
from goldenflow.phase1.quality import Thresholds, run_quality_checks
from goldenflow.phase1.risk import (
    join_risk_signals,
    localisation_rate,
    normalise_risk_stream,
    risk_by_screen,
)
from goldenflow.phase1.store import Dialect, EventStore, generate_ddl
from goldenflow.phase1.tracking_plan import TrackingPlan
from goldenflow.phase2.clustering import cluster_variants
from goldenflow.phase2.drift import detect_drift
from goldenflow.phase2.graph import build_journey_graph
from goldenflow.phase2.mining import (
    build_dfg,
    entropy,
    extract_variants,
    pm4py_available,
    variants_for_coverage,
)
from goldenflow.phase2.naming import RuleBasedNamer, name_journeys
from goldenflow.phase2.scoring import ScoringWeights, score_journeys
from goldenflow.phase2.sessionize import sessionize_store
from goldenflow.phase3.coverage import build_coverage
# NOT `Severity` - phase3.gaps exports one too, and importing it here silently
# shadows goldenflow.phase0.taxonomy.Severity, breaking `taxonomy validate` and the
# Phase 0 gate with a bare AttributeError.
from goldenflow.phase3.gaps import FindingKind, analyse_gaps, deletable_candidates
from goldenflow.phase3.parser_text import parse_repository
from goldenflow.phase3.report import write_jira_payloads, write_prometheus
from goldenflow.phase4.fingerprint import match_screens
from goldenflow.phase4.healing import heal
from goldenflow.phase4.hierarchy import load_dump_directory
from goldenflow.phase4.resolver import from_page_objects, merge as merge_resolutions
from goldenflow.phase4.resolver import from_dumps, resolve_journeys, verify_against_dumps
from goldenflow.phase5.generator import RepoConventions, TemplateGenerator, generate_all
from goldenflow.phase5.pull_request import (
    build_pull_request,
    deletion_pull_request,
    propose_deletions,
)
from goldenflow.phase5.spec import build_specs
from goldenflow.phase5.validation import ValidationGate, Verdict
from goldenflow.phase6.governance import AuditLog, CostLedger, lineage_from_validation
from goldenflow.phase6.outcomes import GapOutcome, build_report, load_defects
from goldenflow.phase6.tuning import gather_evidence, propose_weights
from goldenflow.phase7.onboarding import scaffold, write_scaffold
from goldenflow.phase7.parity import compare_platforms
from goldenflow.phase7.portfolio import (
    ConnectorRegistry,
    TenantHealth,
    build_portfolio,
)
from goldenflow.phase7.tenancy import Permission, Principal, Role, TenantRegistry

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def _read_events(path: Path) -> Iterator[dict[str, Any]]:
    """Read events from JSONL, or from a JSON array."""
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("["):
        yield from json.loads(text)
        return
    for line in text.splitlines():
        line = line.strip()
        if line:
            yield json.loads(line)


# --------------------------------------------------------------------- taxonomy


def cmd_taxonomy_validate(args: argparse.Namespace) -> int:
    taxonomy = Taxonomy.from_yaml(args.path)
    issues = taxonomy.validate_contract()
    errors = [i for i in issues if i.severity is Severity.ERROR]
    warnings = [i for i in issues if i.severity is Severity.WARNING]

    print(f"Taxonomy: {taxonomy.app_id} v{taxonomy.version} "
          f"(fingerprint {taxonomy.fingerprint()})")
    print(f"{len(taxonomy.screens)} screens, {len(errors)} error(s), "
          f"{len(warnings)} warning(s)\n")

    for issue in errors + warnings:
        print(issue.format())

    if errors:
        print(f"\nFAILED - {len(errors)} contract violation(s) must be fixed.")
        return EXIT_FAILED
    print("\nPASSED - taxonomy contract satisfied.")
    return EXIT_OK


def cmd_taxonomy_summary(args: argparse.Namespace) -> int:
    taxonomy = Taxonomy.from_yaml(args.path)
    print(json.dumps(taxonomy.summary(), indent=2))
    return EXIT_OK


# -------------------------------------------------------------------- readiness


def cmd_readiness_audit(args: argparse.Namespace) -> int:
    taxonomy = Taxonomy.from_yaml(args.taxonomy)
    events = _read_events(Path(args.events))
    report = audit_readiness(events, taxonomy, threshold=args.threshold)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.format())

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(report.to_dict(), indent=2), encoding="utf-8"
        )
        print(f"\nReport written to {args.out}")

    return EXIT_OK if report.passed else EXIT_FAILED


# --------------------------------------------------------------------- baseline


def cmd_baseline_capture(args: argparse.Namespace) -> int:
    snapshot = BaselineSnapshot(
        app_id=args.app,
        release_version=args.release,
        captured_by=args.by,
        automated_coverage_pct=args.coverage,
        escaped_defects_per_release=args.escaped_defects,
        mean_time_to_detect_hours=args.mttd,
        regression_suite_runtime_minutes=args.suite_runtime,
        flaky_test_pct=args.flaky,
        manual_authoring_hours_per_sprint=args.authoring_hours,
        total_test_count=args.test_count,
        notes=args.notes,
    )
    try:
        written = snapshot.save(args.out, force=args.force)
    except FileExistsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_FAILED
    print(f"Baseline captured -> {written}  (checksum {snapshot.checksum()})")
    return EXIT_OK


def cmd_baseline_compare(args: argparse.Namespace) -> int:
    before = BaselineSnapshot.load(args.before)
    after = BaselineSnapshot.load(args.after)
    comparison = before.compare(after)
    if args.json:
        print(json.dumps(comparison.to_dict(), indent=2))
    else:
        print(comparison.format())
    return EXIT_OK


# --------------------------------------------------------------------- registry


def cmd_registry_check(args: argparse.Namespace) -> int:
    registry = ProtectedTestRegistry.from_yaml(args.path)
    stale = registry.stale_entries()
    print(json.dumps(registry.summary(), indent=2))
    if stale:
        print(f"\n{len(stale)} entry/entries past review date:")
        for entry in stale:
            print(f"  {entry.pattern}  (owner {entry.owner}, due {entry.review_by})")
        return EXIT_FAILED
    print("\nPASSED - all protections within review window.")
    return EXIT_OK


def cmd_registry_test(args: argparse.Namespace) -> int:
    registry = ProtectedTestRegistry.from_yaml(args.path)
    decision = registry.decide(args.test_id)
    print(decision.explanation)
    return EXIT_FAILED if decision.protected else EXIT_OK


# ------------------------------------------------------------------ phase0 gate


def cmd_phase0_gate(args: argparse.Namespace) -> int:
    """Run every Phase 0 exit criterion and emit a single M2 verdict."""
    results: list[tuple[str, bool, str]] = []

    taxonomy = Taxonomy.from_yaml(args.taxonomy)
    issues = taxonomy.validate_contract()
    errors = [i for i in issues if i.severity is Severity.ERROR]
    results.append(
        (
            "Taxonomy contract",
            not errors,
            f"{len(taxonomy.screens)} screens, {len(errors)} error(s)",
        )
    )

    report = audit_readiness(
        _read_events(Path(args.events)), taxonomy, threshold=args.threshold
    )
    results.append(
        (
            "Instrumentation readiness",
            report.passed,
            f"{report.score}% vs gate {report.threshold}% - {report.verdict}",
        )
    )

    registry = ProtectedTestRegistry.from_yaml(args.registry)
    stale = registry.stale_entries()
    populated = len(registry.entries) > 0
    results.append(
        (
            "Protected test registry",
            populated and not stale,
            f"{len(registry.entries)} entries, {len(stale)} stale",
        )
    )

    if args.baseline:
        exists = Path(args.baseline).exists()
        detail = "captured" if exists else "MISSING - no Phase 6 claim is possible"
        results.append(("Baseline snapshot", exists, detail))

    print("Phase 0 Exit Criteria - M2 Gate")
    print("=" * 70)
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name:<28} {detail}")
    print("=" * 70)

    passed = all(ok for _, ok, _ in results)
    if passed:
        print("VERDICT: GO - proceed to Phase 1 (Data Foundation)")
        return EXIT_OK

    print("VERDICT: NO-GO - remediate before Phase 1")
    plan = report.remediation_plan()
    if plan:
        print("\nReadiness remediation (highest impact first):")
        for i, item in enumerate(plan, 1):
            print(f"  {i}. {item}")
    return EXIT_FAILED


# =========================================================== PHASE 1 commands


def cmd_tracking_validate(args: argparse.Namespace) -> int:
    plan = TrackingPlan.from_yaml(args.path)
    errors = plan.validate_plan()
    print(f"Tracking plan: {plan.app_id} v{plan.version} - {len(plan.events)} events")
    for err in errors:
        print(f"  ERROR  {err}")
    if errors:
        print(f"\nFAILED - {len(errors)} contract defect(s).")
        return EXIT_FAILED
    print("\nPASSED - tracking plan is internally coherent.")
    return EXIT_OK


def cmd_tracking_conformance(args: argparse.Namespace) -> int:
    plan = TrackingPlan.from_yaml(args.plan)
    taxonomy = Taxonomy.from_yaml(args.taxonomy)
    report = plan.validate_stream(
        _read_events(Path(args.events)),
        screen_resolver=taxonomy.resolve_tag,
        strict_properties=args.strict,
    )
    print(report.format())
    return EXIT_OK if report.conformance_pct >= args.threshold else EXIT_FAILED


def cmd_store_ddl(args: argparse.Namespace) -> int:
    print(generate_ddl(Dialect(args.dialect), dataset=args.dataset))
    return EXIT_OK


def cmd_ingest(args: argparse.Namespace) -> int:
    taxonomy = Taxonomy.from_yaml(args.taxonomy)
    result = normalise_stream(
        _read_events(Path(args.events)),
        source=args.source,
        screen_resolver=taxonomy.resolve_tag,
        taxonomy_fingerprint=taxonomy.fingerprint(),
    )

    with EventStore(args.store) as store:
        inserted = store.insert_events(result.events)

        signals = []
        sources = (
            (args.crashlytics, "crashlytics"),
            (args.sentry, "sentry"),
            # UXCam mixes journey steps and auto-captured failure signals in one
            # stream; the adapter returns None for the former.
            (getattr(args, "uxcam_signals", None), "uxcam"),
        )
        for path, source in sources:
            if path:
                signals += normalise_risk_stream(_read_events(Path(path)), source=source)

        located = []
        if signals:
            sessions = {sid: store.session_events(sid) for sid in store.session_ids()}
            located = join_risk_signals(signals, sessions)
            store.insert_risk_signals(located)

        print(f"Ingest -> {args.store}")
        print(json.dumps(result.summary(), indent=2))
        print(f"\n  rows inserted    : {inserted:,} (store now holds "
              f"{store.count_events():,})")
        if signals:
            print(f"  risk signals     : {len(located):,}")
            print(f"  localisation rate: {localisation_rate(located)}% "
                  f"(placed on a screen in the session timeline)")
            top = list(risk_by_screen(located).items())[:5]
            if top:
                print("  failures by screen: "
                      + ", ".join(f"{s} ({n})" for s, n in top))

    if result.rejected:
        print(f"\n  {len(result.rejected)} event(s) rejected; first reasons:")
        for raw, reason in result.rejected[:3]:
            print(f"    - {reason}: {str(raw)[:90]}")
    return EXIT_OK


def cmd_quality_check(args: argparse.Namespace) -> int:
    taxonomy = Taxonomy.from_yaml(args.taxonomy)
    plan = TrackingPlan.from_yaml(args.plan) if args.plan else None
    with EventStore(args.store) as store:
        report = run_quality_checks(
            store, taxonomy, tracking_plan=plan,
            thresholds=Thresholds(min_events=args.min_events),
        )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.format())
    return EXIT_OK if report.passed else EXIT_FAILED


def cmd_pii_scan(args: argparse.Namespace) -> int:
    with EventStore(args.store) as store:
        report = scan_events(store.iter_events())
    print(report.format())
    if not report.clean:
        print(f"\nBy kind: {json.dumps(report.by_kind())}")
        print("Remediation: docs/pii-policy.md - reference by identifier and "
              "resolve server-side under access control.")
    return EXIT_OK if report.clean else EXIT_FAILED


def cmd_phase1_gate(args: argparse.Namespace) -> int:
    """Run every Phase 1 exit criterion and emit a single M5 verdict."""
    results: list[tuple[str, bool, str]] = []

    taxonomy = Taxonomy.from_yaml(args.taxonomy)
    plan = TrackingPlan.from_yaml(args.plan)

    plan_errors = plan.validate_plan()
    results.append(("Tracking plan contract", not plan_errors,
                    f"{len(plan.events)} events, {len(plan_errors)} error(s)"))

    with EventStore(args.store) as store:
        quality = run_quality_checks(store, taxonomy, tracking_plan=plan)
        pii = scan_events(store.iter_events())
        risk_count = store.count_risk_signals()

    for check in quality.checks:
        results.append((f"  {check.name}", check.passed or not check.blocking,
                        check.detail))

    results.append(("PII scan", pii.clean,
                    f"{pii.events_scanned:,} events scanned, "
                    f"{len(pii.findings)} finding(s)"))
    results.append(("Risk signals joined", risk_count > 0,
                    f"{risk_count:,} crash/error signals in store"))

    print("Phase 1 Exit Criteria - M5 Gate")
    print("=" * 78)
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name:<26} {detail}")
    print("=" * 78)

    passed = all(ok for _, ok, _ in results)
    if passed:
        print("VERDICT: GO - proceed to Phase 2 (Journey Intelligence Engine)")
        return EXIT_OK
    print("VERDICT: NO-GO - the event store is not yet trustworthy.")
    print("Mining unreliable data does not fail loudly; it produces a plausible")
    print("process model with holes in it that nobody can detect by inspection.")
    return EXIT_FAILED


# =========================================================== PHASE 2 commands


def _mine(args: argparse.Namespace):
    """Shared pipeline: store -> traces -> variants -> archetypes -> journeys."""
    taxonomy = Taxonomy.from_yaml(args.taxonomy)
    with EventStore(args.store) as store:
        session_result = sessionize_store(store)
        risk = {}
        cur = store._conn.execute(
            "SELECT screen_id, COUNT(*) FROM risk_signals "
            "WHERE screen_id IS NOT NULL GROUP BY 1"
        )
        risk = {row[0]: row[1] for row in cur.fetchall()}

    traces = session_result.usable
    variants = extract_variants(traces)
    # Critical screens keep their own archetype - otherwise account_delete and
    # data_export merge into a generic "account" journey and vanish.
    clustering = cluster_variants(
        variants,
        threshold=args.similarity,
        preserve_terminals=[s.screen_id for s in taxonomy.screens if s.critical],
    )

    monetary: dict[str, float] = {}
    by_sequence: dict[tuple, float] = {}
    for trace in traces:
        value = trace.property_sum("order_value") + trace.property_sum("cart_value")
        if value:
            by_sequence[trace.sequence] = by_sequence.get(trace.sequence, 0.0) + value
    for archetype in clustering.archetypes:
        monetary[archetype.archetype_id] = sum(
            by_sequence.get(m.sequence, 0.0) for m in archetype.members
        )

    journeys = score_journeys(
        clustering, taxonomy=taxonomy, risk_by_screen=risk,
        monetary_by_archetype=monetary, weights=ScoringWeights(),
    )
    name_journeys(journeys, RuleBasedNamer(taxonomy))
    return taxonomy, session_result, traces, variants, clustering, journeys, risk


def cmd_journeys_mine(args: argparse.Namespace) -> int:
    taxonomy, sessions, traces, variants, clustering, journeys, _ = _mine(args)
    dfg = build_dfg(traces)

    print("Journey Intelligence - mining report")
    print("=" * 78)
    print(f"  sessionization : {json.dumps(sessions.summary())}")
    print(f"  process model  : {len(dfg.node_counts)} screens, "
          f"{len(dfg.edge_counts)} transitions, {dfg.trace_count:,} traces")
    print(f"  variants       : {len(variants):,} distinct paths, "
          f"entropy {entropy(variants)} bits")
    print(f"  {variants_for_coverage(variants, 95.0):,} variants explain 95% of sessions")
    print(f"  archetypes     : {json.dumps(clustering.summary())}")
    print(f"  pm4py available: {pm4py_available()}")

    print(f"\nTop {args.top} Golden Journeys")
    print("-" * 78)
    for journey in journeys[:args.top]:
        print(f"\n#{journey.rank}  {journey.archetype.name}   [score {journey.score:.1f}]")
        print(f"    {journey.archetype.description}")
        print(f"    path: {' -> '.join(journey.sequence)}")
        c = journey.components.as_dict()
        print(f"    freq {c['frequency']:.2f} | value {c['business_value']:.2f} | "
              f"risk {c['risk']:.2f} | exposure {c['exposure']:.2f}"
              f" | {journey.archetype.variant_count} variant(s)")

    if args.out:
        payload = {
            "sessionization": sessions.summary(),
            "dfg": dfg.summary(),
            "clustering": clustering.summary(),
            "journeys": [j.to_dict() for j in journeys],
        }
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWritten to {args.out}")
    return EXIT_OK


def cmd_journeys_explain(args: argparse.Namespace) -> int:
    _, _, _, _, _, journeys, _ = _mine(args)
    target = next((j for j in journeys if j.journey_id == args.journey_id), None)
    if target is None:
        print(f"ERROR: no journey {args.journey_id!r}. Available: "
              f"{', '.join(j.journey_id for j in journeys[:10])}", file=sys.stderr)
        return EXIT_USAGE
    print(target.explain())
    print(f"\n  backbone shared by all {target.archetype.variant_count} variant(s):")
    print(f"    {' -> '.join(target.archetype.backbone()) or '(none)'}")
    print(f"\n  terminal screens: {json.dumps(target.archetype.terminal_screens)}")
    return EXIT_OK


def cmd_drift_compare(args: argparse.Namespace) -> int:
    taxonomy = Taxonomy.from_yaml(args.taxonomy)
    with EventStore(args.baseline_store) as store:
        baseline = extract_variants(sessionize_store(store).usable)
    with EventStore(args.current_store) as store:
        current = extract_variants(sessionize_store(store).usable)

    report = detect_drift(
        baseline, current,
        baseline_label=Path(args.baseline_store).stem,
        current_label=Path(args.current_store).stem,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.format())
    return EXIT_FAILED if report.severity == "ALERT" else EXIT_OK


def cmd_graph_export(args: argparse.Namespace) -> int:
    taxonomy, _, traces, _, _, journeys, _ = _mine(args)
    graph = build_journey_graph(build_dfg(traces), journeys, taxonomy=taxonomy)

    if args.format == "cypher":
        text = graph.to_cypher(limit_journeys=args.top)
    elif args.format == "gap-query":
        text = graph.gap_query(min_weight=args.min_weight)
    else:
        text = json.dumps(graph.to_dict(), indent=2)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"{len(graph.screens)} screens, {len(graph.transitions)} transitions, "
              f"{len(graph.journeys)} journeys -> {args.out}")
    else:
        print(text)
    return EXIT_OK


def cmd_phase2_gate(args: argparse.Namespace) -> int:
    """Run every Phase 2 exit criterion and emit a single M8 verdict."""
    taxonomy, sessions, traces, variants, clustering, journeys, risk = _mine(args)
    results: list[tuple[str, bool, str]] = []

    explained = clustering.explained_pct(len(clustering.archetypes))
    archetypes_95 = clustering.archetypes_for_coverage(95.0)

    results.append(("Journey catalogue built", bool(journeys),
                    f"{len(journeys)} archetypes from {len(variants):,} variants"))
    results.append(("Sessions mapped >= 95%", explained >= 95.0,
                    f"{explained:.1f}% of sessions in a named archetype"))
    results.append(("Archetypes tractable", archetypes_95 <= args.max_archetypes,
                    f"{archetypes_95} archetypes explain 95% "
                    f"(limit {args.max_archetypes})"))
    results.append(("Risk signals attributed", bool(risk),
                    f"{sum(risk.values())} failures across {len(risk)} screens"))
    results.append(("Journeys named", all(j.archetype.name for j in journeys),
                    "every archetype has a name and description"))

    graph = build_journey_graph(build_dfg(traces), journeys, taxonomy=taxonomy)
    orphaned = graph.critical_screens_without_traffic()
    results.append(("Critical screens observed", not orphaned,
                    f"{len(orphaned)} critical screen(s) with no traffic"
                    + (f": {', '.join(orphaned[:3])}" if orphaned else "")))

    print("Phase 2 Exit Criteria - M8 Gate")
    print("=" * 78)
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name:<28} {detail}")
    print("=" * 78)

    if all(ok for _, ok, _ in results):
        print("VERDICT: GO - proceed to Phase 3 (QA Asset Graph & Gap Analysis)")
        return EXIT_OK
    print("VERDICT: NO-GO - the journey catalogue is not yet trustworthy.")
    return EXIT_FAILED


def _add_mining_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--store", required=True)
    p.add_argument("--taxonomy", required=True)
    p.add_argument("--similarity", type=float, default=0.65,
                   help="Clustering similarity threshold (0-1)")


# =========================================================== PHASE 3 commands


def _analyse(args: argparse.Namespace):
    """Shared pipeline: mine journeys, parse the suite, project, diff."""
    taxonomy, _, traces, _, _, journeys, _ = _mine(args)
    graph = build_journey_graph(build_dfg(traces), journeys, taxonomy=taxonomy)
    suite = parse_repository(taxonomy, args.suite)
    coverage = build_coverage(graph, suite, journeys)
    registry = (
        ProtectedTestRegistry.from_yaml(args.registry) if args.registry else None
    )
    report = analyse_gaps(
        coverage, suite, taxonomy=taxonomy, registry=registry,
        min_gap_weight=args.min_gap_weight,
    )
    return taxonomy, suite, coverage, report, registry


def cmd_suite_parse(args: argparse.Namespace) -> int:
    taxonomy = Taxonomy.from_yaml(args.taxonomy)
    suite = parse_repository(taxonomy, args.suite)

    print(f"Suite: {args.suite}")
    print(json.dumps(suite.summary(), indent=2))

    if suite.unmapped_tests:
        print(f"\n{len(suite.unmapped_tests)} unmapped test(s):")
        for test in suite.unmapped_tests[:8]:
            hints = ", ".join(r.evidence for r in test.uncertain_screens) or "no hints"
            print(f"  {test.test_id}  ({hints})")

    unbound = [p for p in suite.page_objects if not p.is_bound]
    if unbound:
        print(f"\n{len(unbound)} unbound Page Object(s) - not in the taxonomy:")
        for page_object in unbound[:8]:
            print(f"  {page_object.class_name}  ({page_object.file_path})")

    if suite.issues:
        print(f"\n{len(suite.issues)} parse issue(s):")
        for issue in suite.issues[:8]:
            print(f"  {issue.format()}")

    if args.verbose:
        print("\nParsed tests:")
        for test in suite.tests:
            marker = "SKIP" if test.skipped else f"{test.confidence.value[:4]:>4}"
            print(f"  [{marker}] {test.test_id}")
            print(f"         {' -> '.join(test.sequence) or '(unmapped)'}")

    return EXIT_OK if suite.mapping_rate >= args.min_mapping_rate else EXIT_FAILED


def cmd_gaps_analyse(args: argparse.Namespace) -> int:
    _, suite, coverage, report, registry = _analyse(args)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.format(limit=args.limit))

        proposable, protected = deletable_candidates(report, registry)
        if protected:
            print(f"\n  {len(protected)} obsolete candidate(s) BLOCKED by the "
                  f"protected registry:")
            for test_id in protected:
                print(f"    {test_id}")
        if proposable:
            print(f"\n  {len(proposable)} candidate(s) may be PROPOSED for deletion "
                  f"(human approval still required):")
            for test_id in proposable:
                print(f"    {test_id}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report.to_dict(), indent=2),
                                  encoding="utf-8")
        print(f"\nReport written to {args.out}")
    if args.jira_out:
        count = write_jira_payloads(report, args.jira_out, project_key=args.jira_project)
        print(f"{count} Jira payload(s) -> {args.jira_out} (not submitted)")
    if args.metrics_out:
        write_prometheus(report, coverage, args.metrics_out)
        print(f"Prometheus metrics -> {args.metrics_out}")

    return EXIT_FAILED if report.critical_count else EXIT_OK


def cmd_coverage_show(args: argparse.Namespace) -> int:
    _, _, coverage, _, _ = _analyse(args)
    print("Journey coverage")
    print("=" * 78)
    print(json.dumps(coverage.summary(), indent=2))
    print(f"\n{'journey':<10}{'cov':>7}  {'sessions':>9}  name")
    print("-" * 78)
    for journey_coverage in coverage.journeys[:args.limit]:
        journey = journey_coverage.journey
        bar = "#" * int(journey_coverage.ratio * 10)
        print(f"{journey.journey_id:<10}{journey_coverage.ratio:>7.0%}  "
              f"{journey.session_count:>9,}  {bar:<10} {journey.archetype.name}")
    return EXIT_OK


def cmd_phase3_gate(args: argparse.Namespace) -> int:
    """Run every Phase 3 exit criterion and emit a single M10 verdict."""
    _, suite, coverage, report, registry = _analyse(args)
    results: list[tuple[str, bool, str]] = []

    results.append(("Suite parsed >= 85%", suite.parse_rate >= 85.0,
                    f"{suite.parse_rate}% of {suite.files_scanned} files"))
    results.append(("Tests mapped to screens", suite.mapping_rate >= 70.0,
                    f"{suite.mapping_rate}% of {len(suite.tests)} tests "
                    f"({len(suite.unmapped_tests)} unmapped)"))
    results.append(("Coverage model built", bool(coverage.transitions),
                    f"{coverage.transition_coverage_pct}% of transitions covered, "
                    f"{coverage.weighted_coverage_pct()}% traffic-weighted"))
    results.append(("Gap findings produced", bool(report.findings),
                    f"{len(report.findings)} findings: {report.by_severity()}"))

    obsolete = report.of_kind(FindingKind.OBSOLETE)
    # Protected tests are diverted before they ever become candidates, so counting
    # only the post-hoc partition would report zero and read as "nothing blocked".
    retained = report.of_kind(FindingKind.PROTECTED_RETAINED)
    registry_consulted = registry is not None
    results.append(("Protected registry enforced", registry_consulted,
                    f"{len(obsolete)} deletion candidate(s); "
                    f"{len(retained)} test(s) retained by the registry despite "
                    f"having no production traffic"
                    if registry_consulted else "NO REGISTRY SUPPLIED - unsafe"))

    print("Phase 3 Exit Criteria - M10 Gate")
    print("=" * 78)
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name:<28} {detail}")
    print("=" * 78)

    if all(ok for _, ok, _ in results):
        print("VERDICT: GO - proceed to Phase 4 (Locator Resolution)")
        print("\nPhase 3 is the first phase that ships value on its own. The gap")
        print("report above is worth acting on before any generation exists.")
        return EXIT_OK
    print("VERDICT: NO-GO - the coverage model is not yet trustworthy.")
    return EXIT_FAILED


def _add_analysis_args(p: argparse.ArgumentParser) -> None:
    _add_mining_args(p)
    p.add_argument("--suite", required=True, help="Automation repository root")
    p.add_argument("--registry", help="config/protected-tests.yaml")
    p.add_argument("--min-gap-weight", type=int, default=5)


# =========================================================== PHASE 4 commands


def _resolve(args: argparse.Namespace) -> SimpleNamespace:
    """Shared pipeline: mine journeys, then resolve their steps to locators."""
    taxonomy, _, traces, _, _, journeys, _ = _mine(args)
    suite = parse_repository(taxonomy, args.suite)

    layers = [from_page_objects(suite, taxonomy)]
    crawl = {}
    if args.crawl:
        crawl = load_dump_directory(args.crawl, app_version=args.app_version,
                                    source="crawl")
        layers.append(from_dumps(crawl, source="crawl"))
    if args.replay:
        replay = load_dump_directory(args.replay, app_version=args.app_version,
                                     source="replay")
        layers.append(from_dumps(replay, source="replay"))

    screens = merge_resolutions(*layers)
    # Page Object locators are claims until checked against a real hierarchy.
    disagreements = verify_against_dumps(screens, crawl) if crawl else []

    # A namespace, not a tuple. This function feeds five call sites and the tuple
    # had already grown to seven elements; an earlier version silently dropped
    # `traces`, which made every downstream coverage figure read 0% and sent Phase 5
    # to generate tests for journeys that were already covered.
    return SimpleNamespace(
        taxonomy=taxonomy,
        journeys=journeys,
        suite=suite,
        traces=traces,
        crawl=crawl,
        resolution=resolve_journeys(journeys, screens),
        screens=screens,
        disagreements=disagreements,
    )


def cmd_locators_resolve(args: argparse.Namespace) -> int:
    r = _resolve(args); report, screens, disagreements = r.resolution, r.screens, r.disagreements
    if args.json:
        print(json.dumps({
            "summary": report.summary(),
            "journeys": [j.to_dict() for j in report.journeys],
            "screens": {k: v.to_dict() for k, v in screens.items()},
        }, indent=2))
    else:
        print(report.format(limit=args.limit))
        if disagreements:
            print(f"\n  Cross-source disagreements ({len(disagreements)}) - the "
                  f"Page Objects and the real hierarchy do not agree:")
            for line in disagreements[:10]:
                print(f"    {line}")
            print("    These are claims the suite has never had checked. Either the "
                  "Page Object has drifted or the crawl dump is stale.")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(
            {"summary": report.summary(),
             "disagreements": disagreements,
             "screens": {k: v.to_dict() for k, v in screens.items()}}, indent=2),
            encoding="utf-8")
        print(f"\nWritten to {args.out}")
    return EXIT_OK if report.step_resolution_pct >= args.threshold else EXIT_FAILED


def cmd_locators_show(args: argparse.Namespace) -> int:
    screens = _resolve(args).screens
    resolution = screens.get(args.screen_id)
    if resolution is None:
        print(f"ERROR: no resolution for {args.screen_id!r}. Known: "
              f"{', '.join(sorted(screens)[:12])}", file=sys.stderr)
        return EXIT_USAGE

    print(f"{args.screen_id}  (sources: {', '.join(sorted(resolution.sources))})")
    print(f"  {len(resolution.resolved_elements)}/{len(resolution.elements)} elements "
          f"resolved; strategy mix {json.dumps(resolution.strategy_mix)}")
    print("=" * 76)
    for role, element in sorted(resolution.elements.items()):
        print(f"\n  {role}")
        for candidate in element.candidates[:4]:
            print(f"  {candidate.format()}")
    diagnostics = resolution.diagnostics()
    if diagnostics:
        print("\n  Diagnostics:")
        for line in diagnostics:
            print(f"    {line}")
    return EXIT_OK


def cmd_fingerprint_compare(args: argparse.Namespace) -> int:
    baseline = load_dump_directory(args.baseline, app_version=Path(args.baseline).name)
    current = load_dump_directory(args.current, app_version=Path(args.current).name)
    report = match_screens(baseline, current)
    print(report.format())
    return EXIT_OK if report.stability_pct >= args.threshold else EXIT_FAILED


def cmd_locators_heal(args: argparse.Namespace) -> int:
    baseline = load_dump_directory(args.baseline, app_version=Path(args.baseline).name)
    current = load_dump_directory(args.current, app_version=Path(args.current).name)

    taxonomy = Taxonomy.from_yaml(args.taxonomy)
    suite = parse_repository(taxonomy, args.suite)
    screens = merge_resolutions(from_page_objects(suite, taxonomy),
                                from_dumps(baseline, source="crawl"))

    locators = [
        element.resolved
        for resolution in screens.values()
        for element in resolution.elements.values()
        if element.is_resolved and resolution.screen_id in baseline
    ]
    report = heal(locators, baseline, current)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.format(limit=args.limit))
    return EXIT_OK


def cmd_phase4_gate(args: argparse.Namespace) -> int:
    """Run every Phase 4 exit criterion and emit a single M12 verdict."""
    r = _resolve(args)
    taxonomy, journeys, suite, crawl = r.taxonomy, r.journeys, r.suite, r.crawl
    report, screens, disagreements = r.resolution, r.screens, r.disagreements
    results: list[tuple[str, bool, str]] = []

    results.append(("Step resolution >= 80%", report.step_resolution_pct >= 80.0,
                    f"{report.step_resolution_pct}% of Golden Journey steps have a "
                    f"usable locator"))
    results.append(("Executable journeys", bool(report.executable_journeys),
                    f"{len(report.executable_journeys)}/{len(report.journeys)} "
                    f"journeys fully resolved ({report.executable_journey_pct}%)"))

    diagnosed = sum(1 for s in screens.values() if s.diagnostics())
    unresolved = sum(1 for s in screens.values() if not s.is_resolved)
    results.append(("Unresolved steps diagnosed", unresolved == 0 or diagnosed > 0,
                    f"{unresolved} unresolved screen(s), {diagnosed} carrying "
                    f"actionable diagnostics"))

    if args.baseline and args.current:
        baseline = load_dump_directory(args.baseline)
        current = load_dump_directory(args.current)
        fp = match_screens(baseline, current)
        results.append(("Identity holds across releases", fp.stability_pct >= 80.0,
                        f"{fp.stability_pct}% of screens kept their identity"))

    fragile = [
        element.resolved.strategy.value
        for resolution in screens.values()
        for element in resolution.elements.values()
        if element.is_resolved and element.resolved.strategy.is_fragile
    ]
    results.append(("No fragile locators proposed", not fragile,
                    f"{len(fragile)} fragile locator(s) would be handed to Phase 5"))

    results.append(("Page Object claims verified", not disagreements,
                    f"{len(disagreements)} locator(s) the Page Objects assert but the "
                    f"real hierarchy contradicts"
                    if crawl else "no crawl dumps supplied - claims unverified"))

    print("Phase 4 Exit Criteria - M12 Gate")
    print("=" * 78)
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name:<30} {detail}")
    print("=" * 78)

    if all(ok for _, ok, _ in results):
        print("VERDICT: GO - proceed to Phase 5 (Generation & Validation)")
        return EXIT_OK
    print("VERDICT: NO-GO - journeys cannot yet be turned into executable tests.")
    blocking = report.blocking_screens()
    if blocking:
        print("\nBlocking screens (most journeys first):")
        for screen, count in list(blocking.items())[:6]:
            print(f"  {screen}: blocks {count} journey(s)")
    return EXIT_FAILED


def _add_resolution_args(p: argparse.ArgumentParser) -> None:
    _add_mining_args(p)
    p.add_argument("--suite", required=True)
    p.add_argument("--crawl", help="Directory of crawl page-source dumps")
    p.add_argument("--replay", help="Directory of session-replay hierarchy dumps")
    p.add_argument("--app-version", default="")


# =========================================================== PHASE 5 commands


def _generate(args: argparse.Namespace):
    """Shared pipeline: resolve locators, spec the gaps, render, validate."""
    r = _resolve(args)
    taxonomy, journeys, suite = r.taxonomy, r.journeys, r.suite
    resolution, screens = r.resolution, r.screens

    # Only generate for journeys Phase 3 says are uncovered. The DFG must be built
    # from real traces - an empty one makes every journey read 0% covered.
    graph = build_journey_graph(build_dfg(r.traces), journeys, taxonomy=taxonomy)
    coverage = build_coverage(graph, suite, journeys)
    uncovered = {
        jc.journey.journey_id for jc in coverage.journeys if not jc.fully_covered
    }

    page_object_for = {
        p.screen_id: p.class_name for p in suite.page_objects if p.is_bound
    }
    page_object_methods = {
        p.class_name: p.methods for p in suite.page_objects if p.is_bound
    }
    targets = [
        j for j in journeys
        if j.journey_id in uncovered
        and (r := resolution.journeys) is not None
        and any(x.journey.journey_id == j.journey_id and x.executable for x in r)
    ]

    specs = build_specs(
        targets, screens, taxonomy=taxonomy,
        page_object_for=page_object_for,
        page_object_methods=page_object_methods,
        taxonomy_fingerprint=taxonomy.fingerprint(),
    )
    generator = TemplateGenerator(RepoConventions(suite))
    tests = generate_all(specs, generator)

    gate = ValidationGate(runs_per_profile=args.runs)
    report = gate.validate_all(tests)
    return taxonomy, suite, specs, tests, report


def cmd_generate(args: argparse.Namespace) -> int:
    taxonomy, suite, specs, tests, report = _generate(args)

    print(f"Generation")
    print("=" * 76)
    print(f"  journeys specced  : {len(specs)} "
          f"({sum(1 for s in specs if s.executable)} executable)")
    print(f"  tests rendered    : {len(tests)}")
    skipped = [s for s in specs if not s.executable]
    if skipped:
        print(f"  skipped           : {len(skipped)} spec(s) with unresolvable steps")
        for spec in skipped[:5]:
            print(f"      {spec.test_name}: {', '.join(spec.unresolvable_steps)}")
    print()
    print(report.format())

    if args.show and tests:
        print(f"\n{'=' * 76}\nExample generated test: {tests[0].test_name}\n{'=' * 76}")
        print(tests[0].code)

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for test in tests:
            (out / f"{test.test_name}.py").write_text(test.code, encoding="utf-8")
        (out / "validation.json").write_text(
            json.dumps({"summary": report.summary(),
                        "results": [r.to_dict() for r in report.results]}, indent=2),
            encoding="utf-8")
        print(f"\n{len(tests)} test(s) + validation.json -> {out}")
    return EXIT_OK


def cmd_pr_open(args: argparse.Namespace) -> int:
    _, _, _, _, report = _generate(args)
    pr = build_pull_request(report.results)

    if pr.is_empty:
        print("No pull request opened.")
        print(f"{len(report.results)} generated test(s), 0 validated.")
        print("\nNothing reaches a reviewer until it has run green on a real device.")
        print("Wire up DeviceFarmRunner to change that - see "
              "goldenflow/phase5/validation.py.")

        if args.preview:
            # Renders the artifact so it can be inspected, without pretending the
            # validation happened. The banner is the point.
            from goldenflow.phase5.validation import ValidationResult
            preview = build_pull_request([
                ValidationResult(test=r.test, runs=r.runs, verdict=Verdict.VALIDATED,
                                 reason="PREVIEW ONLY - not actually validated")
                for r in report.results
            ])
            print("\n" + "!" * 76)
            print("PREVIEW ONLY. These tests are NOT validated. This PR would NOT")
            print("be opened. Shown so the artifact can be reviewed before a device")
            print("farm exists.")
            print("!" * 76 + "\n")
            print(preview.body())
        return EXIT_OK

    print(f"Pull request: {pr.title}")
    print(f"  branch : {pr.branch} -> {pr.base}")
    print(f"  changes: {len(pr.changes)}")
    print(f"  labels : {', '.join(pr.labels)}")
    print("\n" + pr.body())

    if args.out:
        pr.write(args.out, repo=args.repo)
        print(f"\nPayload -> {args.out} (NOT submitted)")
    return EXIT_OK


def cmd_pr_prune(args: argparse.Namespace) -> int:
    """Dual-signal deletion proposals."""
    _, suite, coverage, report, registry = _analyse(args)
    candidates, _ = deletable_candidates(report, registry)

    taxonomy = Taxonomy.from_yaml(args.taxonomy)
    known_screens = {s.screen_id for s in taxonomy.screens}
    proposals = propose_deletions(
        candidates + [f.test_ids[0] for f in report.of_kind(FindingKind.PROTECTED_RETAINED)
                      if f.test_ids],
        registry=registry,
        zero_traffic=candidates,
        removed_screens=[s for s in args.removed_screen or []
                         if s not in known_screens],
        retired_flags=args.retired_flag or [],
    )

    print("Deletion proposals - dual-signal rule")
    print("=" * 76)
    for proposal in proposals:
        mark = "PROPOSE" if proposal.proposable else "HOLD   "
        print(f"  [{mark}] {proposal.test_id}")
        print(f"           {proposal.rationale()}")
    print("=" * 76)

    pr = deletion_pull_request(proposals)
    print(f"{len(pr.changes)} of {len(proposals)} candidate(s) cleared both the "
          f"dual-signal rule and the protected registry.")
    if args.out and pr.changes:
        pr.write(args.out, repo=args.repo)
        print(f"Draft payload -> {args.out} (NOT submitted)")
    return EXIT_OK


def cmd_phase5_gate(args: argparse.Namespace) -> int:
    """Run every Phase 5 exit criterion and emit a single M15 verdict."""
    taxonomy, suite, specs, tests, report = _generate(args)
    results: list[tuple[str, bool, str]] = []

    results.append(("Specs built from journeys", bool(specs),
                    f"{len(specs)} spec(s), "
                    f"{sum(1 for s in specs if s.executable)} executable"))
    results.append(("Tests rendered", bool(tests),
                    f"{len(tests)} test(s) generated"))

    compiled = sum(1 for t in tests if t.compiles()[0])
    results.append(("All generated code compiles", compiled == len(tests),
                    f"{compiled}/{len(tests)} parse"))

    has_provenance = all(t.spec.provenance is not None for t in tests)
    results.append(("Provenance on every test", has_provenance,
                    "journey, traffic, score and locator sources in each docstring"))

    validated = report.validated
    results.append((
        "Validated on a real device",
        bool(validated),
        f"{len(validated)}/{len(report.results)} validated "
        f"({report.first_attempt_pass_rate}% first attempt)"
        if validated else
        f"NO DEVICE FARM WIRED UP - {len(report.of_verdict(Verdict.UNVALIDATED))} "
        f"test(s) UNVALIDATED"))

    pr = build_pull_request(report.results)
    results.append(("Only validated tests in the PR",
                    len(pr.changes) == len(validated),
                    f"{len(pr.changes)} change(s) proposed from "
                    f"{len(validated)} validated test(s)"))

    print("Phase 5 Exit Criteria - M15 Gate")
    print("=" * 78)
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name:<30} {detail}")
    print("=" * 78)

    if all(ok for _, ok, _ in results):
        print("VERDICT: GO - proceed to Phase 6 (Closed Loop & Governance)")
        return EXIT_OK
    print("VERDICT: NO-GO")
    if not validated:
        print("\nThe blocking criterion is device validation, and it is blocking")
        print("correctly. CompileOnlyRunner reports UNVALIDATED because a syntax")
        print("check is not evidence a test works. Implement DeviceFarmRunner")
        print("against BrowserStack or Firebase Test Lab to clear it.")
    return EXIT_FAILED


def _add_generation_args(p: argparse.ArgumentParser) -> None:
    _add_resolution_args(p)
    p.add_argument("--registry", help="config/protected-tests.yaml")
    p.add_argument("--min-gap-weight", type=int, default=5)
    p.add_argument("--runs", type=int, default=10,
                   help="Runs per device profile in the validation gate")


# =========================================================== PHASE 6 commands


def _outcomes(args: argparse.Namespace):
    """Attribute escaped defects against the current journey catalogue."""
    r = _resolve(args)
    taxonomy, journeys, suite = r.taxonomy, r.journeys, r.suite

    graph = build_journey_graph(build_dfg(r.traces), journeys, taxonomy=taxonomy)
    coverage = build_coverage(graph, suite, journeys)
    coverage_by_journey = coverage.coverage_by_archetype()
    tests_by_journey = {
        jc.journey.journey_id: sorted(jc.covering_tests) for jc in coverage.journeys
    }

    raw = json.loads(Path(args.defects).read_text(encoding="utf-8"))
    defects = load_defects(args.defects)
    gaps = [
        GapOutcome(
            journey_id=g["journey_id"],
            reported_at=datetime.fromisoformat(
                g["reported_at"].replace("Z", "+00:00")),
            severity=g.get("severity", "medium"),
            closed_by_test=g.get("closed_by_test"),
            caused_defect=g.get("caused_defect"),
        )
        for g in raw.get("gaps_reported", [])
    ]

    report = build_report(
        defects, gaps,
        golden_journey_ids=[j.journey_id for j in journeys],
        coverage_by_journey=coverage_by_journey,
        tests_by_journey=tests_by_journey,
    )
    return taxonomy, journeys, report, coverage


def cmd_outcomes_report(args: argparse.Namespace) -> int:
    _, _, report, _ = _outcomes(args)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.format())
        blind = report.assertion_blind_spot
        if blind and blind > 0.2:
            print(f"\n  {blind:.0%} of in-scope defects slipped past a PASSING test.")
            print("  That is the strongest available argument for Phase 3's")
            print("  assertion-gap analysis, measured rather than asserted.")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report.to_dict(), indent=2),
                                  encoding="utf-8")
        print(f"\nWritten to {args.out}")
    return EXIT_OK


def cmd_tune_weights(args: argparse.Namespace) -> int:
    _, journeys, report, _ = _outcomes(args)

    component_scores = {
        j.journey_id: j.components.as_dict() for j in journeys
    }
    evidence = gather_evidence(report, component_scores)
    proposal = propose_weights(ScoringWeights(), report, evidence)

    print(proposal.format())
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(proposal.to_dict(), indent=2),
                                  encoding="utf-8")
        print(f"\nProposal -> {args.out} (NOT applied)")
    return EXIT_OK


def cmd_audit_show(args: argparse.Namespace) -> int:
    taxonomy, suite, specs, tests, validation = _generate(args)
    log = lineage_from_validation(validation.results,
                                  taxonomy_fingerprint=taxonomy.fingerprint())

    ledger = CostLedger()
    for test in tests:
        # Token counts are estimates from rendered size; a real integration reads
        # them from the model response.
        ledger.record_generation(test.test_name,
                                 input_tokens=len(test.code) // 3,
                                 output_tokens=len(test.code) // 4)
    for result in validation.results:
        ledger.record_validation(result.test.test_name,
                                 device_minutes=len(result.executed_runs) * 0.75)
    ledger.record_mining(terabytes_scanned=args.tb_scanned)

    print("Audit lineage")
    print("=" * 74)
    print(json.dumps(log.summary(), indent=2))
    if log.incomplete:
        print(f"\n{len(log.incomplete)} record(s) with incomplete lineage:")
        for record in log.incomplete[:5]:
            print(f"  {record.artifact_id}")
    print()
    print(ledger.format())
    merged = len(validation.validated)
    print()
    print(json.dumps(ledger.unit_economics(merged_tests=merged), indent=2))

    if args.out:
        log.write(args.out)
        print(f"\nLineage -> {args.out}")
    return EXIT_OK


def cmd_phase6_gate(args: argparse.Namespace) -> int:
    """Run every Phase 6 exit criterion and emit a single M18 verdict."""
    taxonomy, journeys, report, coverage = _outcomes(args)
    results: list[tuple[str, bool, str]] = []

    attributed = [d for d in report.defects if d.attributed]
    results.append(("Defects attributed", len(attributed) == len(report.defects),
                    f"{len(attributed)}/{len(report.defects)} given an origin"))

    results.append(("Precision/recall measured",
                    report.precision is not None or report.recall is not None,
                    f"precision {report.precision}, recall {report.recall}, "
                    f"{report.unresolved_gaps} gap(s) unresolved"))

    results.append(("Assertion blind spot measured",
                    report.assertion_blind_spot is not None,
                    f"{report.assertion_blind_spot:.0%} of in-scope defects passed a "
                    f"green test" if report.assertion_blind_spot is not None
                    else "no in-scope defects"))

    component_scores = {j.journey_id: j.components.as_dict() for j in journeys}
    evidence = gather_evidence(report, component_scores)
    proposal = propose_weights(ScoringWeights(), report, evidence)
    results.append(("Weight proposal produced", bool(proposal.reason),
                    "no-op: " + proposal.reason[:60] if proposal.is_noop
                    else f"deltas {proposal.deltas}"))
    results.append(("Nothing tuned automatically", not proposal.applied,
                    "weights change only on explicit human acceptance"))

    print("Phase 6 Exit Criteria - M18 Gate")
    print("=" * 78)
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name:<30} {detail}")
    print("=" * 78)

    if all(ok for _, ok, _ in results):
        print("VERDICT: GO - proceed to Phase 7 (Multi-App Scale & Productization)")
        print("\nThe loop is closed and measured. Note what it measures against:")
        print("real escaped defects, not opinion about whether the gaps looked right.")
        return EXIT_OK
    print("VERDICT: NO-GO - the loop is not yet measurable.")
    return EXIT_FAILED


def _add_outcome_args(p: argparse.ArgumentParser) -> None:
    _add_generation_args(p)
    p.add_argument("--defects", required=True,
                   help="Escaped defects JSON (Jira export shape)")


# =========================================================== PHASE 7 commands


def cmd_tenant_list(args: argparse.Namespace) -> int:
    registry = TenantRegistry.from_yaml(args.path)
    principal = Principal(name=args.principal, role=Role(args.role))
    visible = registry.visible_to(principal)

    print(json.dumps(registry.summary(), indent=2))
    print(f"\n{'tenant':<18}{'platform':<10}{'status':<12}{'source':<12}owner")
    print("-" * 70)
    for tenant in visible:
        print(f"{tenant.tenant_id:<18}{tenant.platform:<10}{tenant.status:<12}"
              f"{tenant.source:<12}{tenant.owner_team}")
    return EXIT_OK


def cmd_tenant_check(args: argparse.Namespace) -> int:
    registry = TenantRegistry.from_yaml(args.path)
    problems = registry.isolation_check()
    print(f"Tenant isolation: {len(registry.tenants)} tenant(s)")
    if not problems:
        print("PASSED - no shared configuration between tenants.")
        return EXIT_OK
    print(f"\nFAILED - {len(problems)} isolation violation(s):")
    for problem in problems:
        print(f"  {problem}")
    print("\nShared config means one team's change silently re-keys another team's")
    print("journey graph, which presents as a coverage collapse, not an error.")
    return EXIT_FAILED


def cmd_tenant_permissions(args: argparse.Namespace) -> int:
    principal = Principal(name=args.principal, role=Role(args.role),
                          tenants=tuple(args.tenant or ()))
    print(f"{principal.name} ({principal.role.value})")
    print(f"  scoped to: {', '.join(principal.tenants) or 'all tenants'}")
    print()
    for permission in Permission:
        allowed = principal.may(permission, args.on)
        print(f"  [{'ALLOW' if allowed else 'DENY ':5}] {permission.value}")
    return EXIT_OK


def cmd_onboard_scaffold(args: argparse.Namespace) -> int:
    events = list(_read_events(Path(args.events)))
    result = scaffold(events, app_id=args.app)
    print(result.format(limit=args.limit))

    if args.out:
        written = write_scaffold(result, args.out)
        print(f"\nDrafts written:")
        for kind, path in written.items():
            print(f"  {kind:<14} {path}")
        print("\nNext: assign domains, bind Page Objects, and confirm every")
        print("sensitivity value before any SDK ships.")
    return EXIT_OK


def cmd_connectors_list(args: argparse.Namespace) -> int:
    registry = ConnectorRegistry()
    print(f"{'connector':<16}{'kind':<10}{'auto-capture':<15}notes")
    print("-" * 84)
    for entry in registry.summary():
        auto = "yes" if entry["auto_capture"] else "no"
        print(f"{entry['name']:<16}{entry['kind']:<10}{auto:<15}{entry['notes']}")
    print(f"\nAuto-capture sources: {', '.join(registry.auto_capture_sources()) or 'none'}")
    print("Auto-capture changes the onboarding estimate by weeks: a manual vendor")
    print("means the app team ships event code before any journey exists.")
    return EXIT_OK


def cmd_parity_compare(args: argparse.Namespace) -> int:
    taxonomy = Taxonomy.from_yaml(args.taxonomy)
    with EventStore(args.store) as store:
        traces = sessionize_store(store).usable

    covered: dict[str, set[tuple[str, ...]]] = {}
    if args.suite:
        suite = parse_repository(taxonomy, args.suite)
        sequences = {t.sequence for t in suite.tests if t.sequence}
        # Without per-platform suites, the same suite is assumed for both. Stated
        # rather than hidden: real asymmetry needs separate iOS and Android suites.
        for platform in ("android", "ios"):
            covered[platform] = sequences

    report = compare_platforms(traces, covered_sequences=covered)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.format())
        if len(report.platforms) < 2:
            print("\n  Only one platform present in the store - nothing to compare.")
            print("  Parity analysis needs traces carrying `platform` from both.")
    return EXIT_OK


def cmd_portfolio_report(args: argparse.Namespace) -> int:
    registry = TenantRegistry.from_yaml(args.tenants)
    healths: list[TenantHealth] = []

    for tenant in registry.tenants:
        health = TenantHealth(tenant_id=tenant.tenant_id, status=tenant.status)
        store_path = Path(tenant.store_path) if tenant.store_path else None
        taxonomy_path = Path(tenant.taxonomy_path) if tenant.taxonomy_path else None

        # Tenants whose stores do not exist yet report as unknown rather than zero.
        # A zero would read as "no journeys" when the truth is "not onboarded".
        if (store_path and store_path.exists()
                and taxonomy_path and taxonomy_path.exists()):
            sub = argparse.Namespace(
                store=str(store_path), taxonomy=str(taxonomy_path),
                similarity=0.65, suite=tenant.suite_path,
                crawl=None, replay=None, app_version="",
                registry=tenant.registry_path, min_gap_weight=5,
            )
            try:
                r = _resolve(sub)
                graph = build_journey_graph(build_dfg(r.traces), r.journeys,
                                            taxonomy=r.taxonomy)
                coverage = build_coverage(graph, r.suite, r.journeys)
                gaps = analyse_gaps(coverage, r.suite, taxonomy=r.taxonomy)
                health.journeys = len(r.journeys)
                health.coverage_pct = coverage.transition_coverage_pct
                health.traffic_weighted_coverage_pct = coverage.weighted_coverage_pct()
                health.open_gaps = len(gaps.of_kind(FindingKind.COVERAGE_GAP))
                health.critical_gaps = gaps.critical_count
            except Exception as exc:      # a broken tenant must not hide the others
                print(f"  WARN {tenant.tenant_id}: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
        healths.append(health)

    report = build_portfolio(healths, onboarding_target_days=args.target_days)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.format())
    return EXIT_OK


def cmd_phase7_gate(args: argparse.Namespace) -> int:
    """Run every Phase 7 exit criterion and emit a single M24 verdict."""
    registry = TenantRegistry.from_yaml(args.tenants)
    results: list[tuple[str, bool, str]] = []

    results.append(("3+ tenants onboarded", len(registry.tenants) >= 3,
                    f"{len(registry.tenants)} tenant(s), "
                    f"{len(registry.active)} active"))

    problems = registry.isolation_check()
    results.append(("Tenant isolation clean", not problems,
                    f"{len(problems)} shared-config violation(s)"))

    custom = [t for t in registry.tenants if t.weights]
    results.append(("Self-service configuration", bool(custom),
                    f"{len(custom)} tenant(s) with their own scoring weights"))

    connectors = ConnectorRegistry()
    results.append(("Connector framework", len(connectors.names) >= 4,
                    f"{len(connectors.names)} source(s): "
                    f"{', '.join(connectors.names)}"))

    events = list(_read_events(Path(args.events)))
    result = scaffold(events, app_id="scaffold-check")
    results.append(("Onboarding automation", result.screens_found > 0,
                    f"drafts {result.screens_found} screen(s) and "
                    f"{len(result.tracking_plan.events)} event(s) from a sample; "
                    f"~{result.estimated_review_hours}h review"))

    principal = Principal(name="viewer", role=Role.VIEWER)
    rbac_holds = not principal.may(Permission.APPROVE_DELETION)
    results.append(("RBAC guards destructive ops", rbac_holds,
                    "a viewer cannot approve deletions"))

    print("Phase 7 Exit Criteria - M24 Gate")
    print("=" * 78)
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name:<30} {detail}")
    print("=" * 78)

    if all(ok for _, ok, _ in results):
        print("VERDICT: GO - the platform is adoptable across the organisation")
        print("\nThis is the last phase. What is NOT proven here: the SLA over two")
        print("quarters and the real onboarding duration, both of which need")
        print("calendar time and real tenants rather than a fixture.")
        return EXIT_OK
    print("VERDICT: NO-GO - not yet a platform.")
    return EXIT_FAILED


# ------------------------------------------------------------------- arg parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="goldenflow",
        description="GoldenFlow AI - A Self-Evolving Mobile QE Agent",
    )
    parser.add_argument("--version", action="version", version=f"goldenflow {__version__}")
    sub = parser.add_subparsers(dest="group", required=True)

    # taxonomy
    tax = sub.add_parser("taxonomy", help="Screen taxonomy contract")
    tax_sub = tax.add_subparsers(dest="cmd", required=True)
    p = tax_sub.add_parser("validate", help="Validate the taxonomy contract (CI gate)")
    p.add_argument("path")
    p.set_defaults(func=cmd_taxonomy_validate)
    p = tax_sub.add_parser("summary", help="Print taxonomy summary as JSON")
    p.add_argument("path")
    p.set_defaults(func=cmd_taxonomy_summary)

    # readiness
    rdy = sub.add_parser("readiness", help="Instrumentation readiness audit")
    rdy_sub = rdy.add_subparsers(dest="cmd", required=True)
    p = rdy_sub.add_parser("audit", help="Score an event sample against the rubric")
    p.add_argument("--taxonomy", required=True)
    p.add_argument("--events", required=True, help="JSONL or JSON array of events")
    p.add_argument("--threshold", type=float, default=DEFAULT_GATE_THRESHOLD)
    p.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    p.add_argument("--out", help="Also write the JSON report to this path")
    p.set_defaults(func=cmd_readiness_audit)

    # baseline
    bl = sub.add_parser("baseline", help="Pre-GoldenFlow baseline metrics")
    bl_sub = bl.add_subparsers(dest="cmd", required=True)
    p = bl_sub.add_parser("capture", help="Capture a write-once baseline snapshot")
    p.add_argument("--app", required=True)
    p.add_argument("--release", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--by", default="")
    p.add_argument("--coverage", type=float, default=0.0)
    p.add_argument("--escaped-defects", type=float, default=0.0)
    p.add_argument("--mttd", type=float, default=0.0, help="Mean time to detect, hours")
    p.add_argument("--suite-runtime", type=float, default=0.0, help="Minutes")
    p.add_argument("--flaky", type=float, default=0.0, help="Flaky test %%")
    p.add_argument("--authoring-hours", type=float, default=0.0)
    p.add_argument("--test-count", type=int, default=0)
    p.add_argument("--notes", default="")
    p.add_argument("--force", action="store_true", help="Overwrite (record why)")
    p.set_defaults(func=cmd_baseline_capture)
    p = bl_sub.add_parser("compare", help="Compare two snapshots")
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_baseline_compare)

    # registry
    reg = sub.add_parser("registry", help="Protected test registry")
    reg_sub = reg.add_subparsers(dest="cmd", required=True)
    p = reg_sub.add_parser("check", help="Validate the registry and flag stale entries")
    p.add_argument("path")
    p.set_defaults(func=cmd_registry_check)
    p = reg_sub.add_parser("test", help="Check whether a test id is protected")
    p.add_argument("path")
    p.add_argument("test_id")
    p.set_defaults(func=cmd_registry_test)

    # phase0
    ph0 = sub.add_parser("phase0", help="Phase 0 gates")
    ph0_sub = ph0.add_subparsers(dest="cmd", required=True)
    p = ph0_sub.add_parser("gate", help="Run all Phase 0 exit criteria (M2 go/no-go)")
    p.add_argument("--taxonomy", required=True)
    p.add_argument("--events", required=True)
    p.add_argument("--registry", required=True)
    p.add_argument("--baseline", help="Path to the captured baseline snapshot")
    p.add_argument("--threshold", type=float, default=DEFAULT_GATE_THRESHOLD)
    p.set_defaults(func=cmd_phase0_gate)

    # ------------------------------------------------------------ phase 1
    trk = sub.add_parser("tracking", help="Event schema contract")
    trk_sub = trk.add_subparsers(dest="cmd", required=True)
    p = trk_sub.add_parser("validate", help="Check the plan is internally coherent")
    p.add_argument("path")
    p.set_defaults(func=cmd_tracking_validate)
    p = trk_sub.add_parser("conformance", help="Check production traffic against the plan")
    p.add_argument("--plan", required=True)
    p.add_argument("--taxonomy", required=True)
    p.add_argument("--events", required=True)
    p.add_argument("--threshold", type=float, default=95.0)
    p.add_argument("--strict", action="store_true",
                   help="Also report properties not declared in the plan")
    p.set_defaults(func=cmd_tracking_conformance)

    st = sub.add_parser("store", help="Event store")
    st_sub = st.add_subparsers(dest="cmd", required=True)
    p = st_sub.add_parser("ddl", help="Emit warehouse DDL")
    p.add_argument("--dialect", choices=[d.value for d in Dialect], default="bigquery")
    p.add_argument("--dataset", default="goldenflow")
    p.set_defaults(func=cmd_store_ddl)

    p = sub.add_parser("ingest", help="Normalise vendor events into the event store")
    p.add_argument("--events", required=True)
    p.add_argument("--taxonomy", required=True)
    p.add_argument("--store", required=True)
    p.add_argument("--source", default="raw",
                   choices=["raw", "rudderstack", "firebase", "mixpanel", "uxcam"])
    p.add_argument("--crashlytics", help="Crashlytics export to join on session_id")
    p.add_argument("--sentry", help="Sentry export to join on session_id")
    p.add_argument("--uxcam-signals",
                   help="UXCam export to mine for rage taps and UI freezes")
    p.set_defaults(func=cmd_ingest)

    q = sub.add_parser("quality", help="Data quality monitors")
    q_sub = q.add_subparsers(dest="cmd", required=True)
    p = q_sub.add_parser("check", help="Run all monitors against the store")
    p.add_argument("--store", required=True)
    p.add_argument("--taxonomy", required=True)
    p.add_argument("--plan", help="Tracking plan for the conformance check")
    p.add_argument("--min-events", type=int, default=1000)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_quality_check)

    pii = sub.add_parser("pii", help="PII policy enforcement")
    pii_sub = pii.add_subparsers(dest="cmd", required=True)
    p = pii_sub.add_parser("scan", help="Scan stored event properties for personal data")
    p.add_argument("--store", required=True)
    p.set_defaults(func=cmd_pii_scan)

    ph1 = sub.add_parser("phase1", help="Phase 1 gates")
    ph1_sub = ph1.add_subparsers(dest="cmd", required=True)
    p = ph1_sub.add_parser("gate", help="Run all Phase 1 exit criteria (M5 go/no-go)")
    p.add_argument("--store", required=True)
    p.add_argument("--taxonomy", required=True)
    p.add_argument("--plan", required=True)
    p.set_defaults(func=cmd_phase1_gate)

    # ------------------------------------------------------------ phase 2
    jn = sub.add_parser("journeys", help="Journey Intelligence (Agent 1)")
    jn_sub = jn.add_subparsers(dest="cmd", required=True)
    p = jn_sub.add_parser("mine", help="Mine Golden Journeys from the event store")
    _add_mining_args(p)
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--out", help="Write the full catalogue as JSON")
    p.set_defaults(func=cmd_journeys_mine)
    p = jn_sub.add_parser("explain", help="Explain one journey's score")
    _add_mining_args(p)
    p.add_argument("journey_id")
    p.set_defaults(func=cmd_journeys_explain)

    dr = sub.add_parser("drift", help="Journey Drift detection")
    dr_sub = dr.add_subparsers(dest="cmd", required=True)
    p = dr_sub.add_parser("compare", help="Compare two event stores for drift")
    p.add_argument("--baseline-store", required=True)
    p.add_argument("--current-store", required=True)
    p.add_argument("--taxonomy", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_drift_compare)

    gr = sub.add_parser("graph", help="Journey graph (Neo4j)")
    gr_sub = gr.add_subparsers(dest="cmd", required=True)
    p = gr_sub.add_parser("export", help="Export the journey graph")
    _add_mining_args(p)
    p.add_argument("--format", choices=["cypher", "json", "gap-query"],
                   default="cypher")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--min-weight", type=int, default=50)
    p.add_argument("--out")
    p.set_defaults(func=cmd_graph_export)

    ph2 = sub.add_parser("phase2", help="Phase 2 gates")
    ph2_sub = ph2.add_subparsers(dest="cmd", required=True)
    p = ph2_sub.add_parser("gate", help="Run all Phase 2 exit criteria (M8 go/no-go)")
    _add_mining_args(p)
    p.add_argument("--max-archetypes", type=int, default=50)
    p.set_defaults(func=cmd_phase2_gate)

    # ------------------------------------------------------------ phase 3
    su = sub.add_parser("suite", help="QA asset parsing")
    su_sub = su.add_subparsers(dest="cmd", required=True)
    p = su_sub.add_parser("parse", help="Parse an automation repository")
    p.add_argument("--suite", required=True)
    p.add_argument("--taxonomy", required=True)
    p.add_argument("--min-mapping-rate", type=float, default=70.0)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_suite_parse)

    cv = sub.add_parser("coverage", help="Journey coverage")
    cv_sub = cv.add_subparsers(dest="cmd", required=True)
    p = cv_sub.add_parser("show", help="Per-journey coverage table")
    _add_analysis_args(p)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_coverage_show)

    gp = sub.add_parser("gaps", help="Gap analysis (Quality Strategy, part 1)")
    gp_sub = gp.add_subparsers(dest="cmd", required=True)
    p = gp_sub.add_parser("analyse", help="Diff the suite against production reality")
    _add_analysis_args(p)
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--json", action="store_true")
    p.add_argument("--out", help="Write the full report as JSON")
    p.add_argument("--jira-out", help="Write Jira payloads (not submitted)")
    p.add_argument("--jira-project", default="QA")
    p.add_argument("--metrics-out", help="Write Prometheus metrics for Grafana")
    p.set_defaults(func=cmd_gaps_analyse)

    ph3 = sub.add_parser("phase3", help="Phase 3 gates")
    ph3_sub = ph3.add_subparsers(dest="cmd", required=True)
    p = ph3_sub.add_parser("gate", help="Run all Phase 3 exit criteria (M10 go/no-go)")
    _add_analysis_args(p)
    p.set_defaults(func=cmd_phase3_gate)

    # ------------------------------------------------------------ phase 4
    lo = sub.add_parser("locators", help="Locator resolution")
    lo_sub = lo.add_subparsers(dest="cmd", required=True)
    p = lo_sub.add_parser("resolve", help="Resolve journey steps to locators")
    _add_resolution_args(p)
    p.add_argument("--threshold", type=float, default=80.0)
    p.add_argument("--limit", type=int, default=12)
    p.add_argument("--json", action="store_true")
    p.add_argument("--out")
    p.set_defaults(func=cmd_locators_resolve)
    p = lo_sub.add_parser("show", help="Every candidate locator for one screen")
    _add_resolution_args(p)
    p.add_argument("screen_id")
    p.set_defaults(func=cmd_locators_show)
    p = lo_sub.add_parser("heal", help="Check locators against a newer build")
    p.add_argument("--baseline", required=True, help="Baseline dump directory")
    p.add_argument("--current", required=True, help="New build dump directory")
    p.add_argument("--taxonomy", required=True)
    p.add_argument("--suite", required=True)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_locators_heal)

    fp = sub.add_parser("fingerprint", help="Screen identity across releases")
    fp_sub = fp.add_subparsers(dest="cmd", required=True)
    p = fp_sub.add_parser("compare", help="Match screens between two builds")
    p.add_argument("--baseline", required=True)
    p.add_argument("--current", required=True)
    p.add_argument("--threshold", type=float, default=80.0)
    p.set_defaults(func=cmd_fingerprint_compare)

    ph4 = sub.add_parser("phase4", help="Phase 4 gates")
    ph4_sub = ph4.add_subparsers(dest="cmd", required=True)
    p = ph4_sub.add_parser("gate", help="Run all Phase 4 exit criteria (M12 go/no-go)")
    _add_resolution_args(p)
    p.add_argument("--baseline", help="Baseline dump dir for the fingerprint check")
    p.add_argument("--current", help="New build dump dir for the fingerprint check")
    p.set_defaults(func=cmd_phase4_gate)

    # ------------------------------------------------------------ phase 5
    p = sub.add_parser("generate", help="Generate and validate tests for gaps")
    _add_generation_args(p)
    p.add_argument("--show", action="store_true", help="Print one generated test")
    p.add_argument("--out", help="Directory to write generated tests into")
    p.set_defaults(func=cmd_generate)

    pr = sub.add_parser("pr", help="Pull request proposals (never commits)")
    pr_sub = pr.add_subparsers(dest="cmd", required=True)
    p = pr_sub.add_parser("open", help="Build a PR from validated tests only")
    _add_generation_args(p)
    p.add_argument("--repo", default="")
    p.add_argument("--out", help="Write the PR payload (not submitted)")
    p.add_argument("--preview", action="store_true",
                   help="Render the PR body even when nothing validated, clearly "
                        "banner-marked. For inspecting the artifact, never for merging.")
    p.set_defaults(func=cmd_pr_open)
    p = pr_sub.add_parser("prune", help="Dual-signal deletion proposals")
    _add_analysis_args(p)
    p.add_argument("--removed-screen", action="append",
                   help="Screen removed from the app (second signal)")
    p.add_argument("--retired-flag", action="append",
                   help="Retired feature flag (second signal)")
    p.add_argument("--repo", default="")
    p.add_argument("--out")
    p.set_defaults(func=cmd_pr_prune)

    ph5 = sub.add_parser("phase5", help="Phase 5 gates")
    ph5_sub = ph5.add_subparsers(dest="cmd", required=True)
    p = ph5_sub.add_parser("gate", help="Run all Phase 5 exit criteria (M15 go/no-go)")
    _add_generation_args(p)
    p.set_defaults(func=cmd_phase5_gate)

    # ------------------------------------------------------------ phase 6
    oc = sub.add_parser("outcomes", help="Escaped-defect attribution (the closed loop)")
    oc_sub = oc.add_subparsers(dest="cmd", required=True)
    p = oc_sub.add_parser("report", help="Why did defects escape, and did we call it?")
    _add_outcome_args(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--out")
    p.set_defaults(func=cmd_outcomes_report)

    tu = sub.add_parser("tune", help="Scoring weight proposals from outcomes")
    tu_sub = tu.add_subparsers(dest="cmd", required=True)
    p = tu_sub.add_parser("weights", help="Propose weight changes (never applies them)")
    _add_outcome_args(p)
    p.add_argument("--out")
    p.set_defaults(func=cmd_tune_weights)

    au = sub.add_parser("audit", help="Lineage and cost governance")
    au_sub = au.add_subparsers(dest="cmd", required=True)
    p = au_sub.add_parser("show", help="Artifact lineage and unit economics")
    _add_generation_args(p)
    p.add_argument("--tb-scanned", type=float, default=0.4,
                   help="Warehouse TB scanned by the last mining run")
    p.add_argument("--out")
    p.set_defaults(func=cmd_audit_show)

    ph6 = sub.add_parser("phase6", help="Phase 6 gates")
    ph6_sub = ph6.add_subparsers(dest="cmd", required=True)
    p = ph6_sub.add_parser("gate", help="Run all Phase 6 exit criteria (M18 go/no-go)")
    _add_outcome_args(p)
    p.set_defaults(func=cmd_phase6_gate)

    # ------------------------------------------------------------ phase 7
    tn = sub.add_parser("tenant", help="Multi-tenancy")
    tn_sub = tn.add_subparsers(dest="cmd", required=True)
    p = tn_sub.add_parser("list", help="List tenants visible to a principal")
    p.add_argument("path")
    p.add_argument("--principal", default="cli")
    p.add_argument("--role", default="platform", choices=[r.value for r in Role])
    p.set_defaults(func=cmd_tenant_list)
    p = tn_sub.add_parser("check", help="Check tenant config isolation")
    p.add_argument("path")
    p.set_defaults(func=cmd_tenant_check)
    p = tn_sub.add_parser("permissions", help="Show what a role may do")
    p.add_argument("--principal", default="cli")
    p.add_argument("--role", required=True, choices=[r.value for r in Role])
    p.add_argument("--tenant", action="append", help="Restrict to these tenants")
    p.add_argument("--on", help="Check against this tenant")
    p.set_defaults(func=cmd_tenant_permissions)

    ob = sub.add_parser("onboard", help="New-app onboarding automation")
    ob_sub = ob.add_subparsers(dest="cmd", required=True)
    p = ob_sub.add_parser("scaffold",
                          help="Draft a taxonomy and tracking plan from a sample")
    p.add_argument("--events", required=True)
    p.add_argument("--app", required=True)
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--out", help="Directory for the draft YAML files")
    p.set_defaults(func=cmd_onboard_scaffold)

    cn = sub.add_parser("connectors", help="Telemetry source registry")
    cn_sub = cn.add_subparsers(dest="cmd", required=True)
    p = cn_sub.add_parser("list", help="Registered connectors")
    p.set_defaults(func=cmd_connectors_list)

    pa = sub.add_parser("parity", help="Cross-platform parity")
    pa_sub = pa.add_subparsers(dest="cmd", required=True)
    p = pa_sub.add_parser("compare", help="iOS vs Android journeys and coverage")
    p.add_argument("--store", required=True)
    p.add_argument("--taxonomy", required=True)
    p.add_argument("--suite")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_parity_compare)

    pf = sub.add_parser("portfolio", help="Cross-tenant rollup")
    pf_sub = pf.add_subparsers(dest="cmd", required=True)
    p = pf_sub.add_parser("report", help="Health across every tenant")
    p.add_argument("--tenants", required=True)
    p.add_argument("--target-days", type=int, default=14)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_portfolio_report)

    ph7 = sub.add_parser("phase7", help="Phase 7 gates")
    ph7_sub = ph7.add_subparsers(dest="cmd", required=True)
    p = ph7_sub.add_parser("gate", help="Run all Phase 7 exit criteria (M24 go/no-go)")
    p.add_argument("--tenants", required=True)
    p.add_argument("--events", required=True,
                   help="Event sample, for the onboarding-automation check")
    p.set_defaults(func=cmd_phase7_gate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        print(f"ERROR: file not found - {exc.filename}", file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:  # surfaced to CI with a non-zero code
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
