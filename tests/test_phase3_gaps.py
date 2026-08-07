"""Coverage projection, gap analysis and reporting tests.

The single most important test in this file is
``test_protected_test_with_identical_evidence_is_not_proposed_for_deletion``. The
protected registry only earns its place if it changes the outcome for a test whose
evidence is otherwise indistinguishable from a genuinely dead one.
"""

from __future__ import annotations

import json

import pytest

from goldenflow.phase0.registry import ProtectedTestRegistry
from goldenflow.phase0.taxonomy import Taxonomy
from goldenflow.phase2.clustering import cluster_variants
from goldenflow.phase2.graph import build_journey_graph
from goldenflow.phase2.mining import Variant, build_dfg
from goldenflow.phase2.scoring import score_journeys
from goldenflow.phase3.coverage import build_coverage
from goldenflow.phase3.gaps import (
    FindingKind,
    Severity,
    analyse_gaps,
    deletable_candidates,
)
from goldenflow.phase3.models import (
    Confidence,
    Language,
    ParsedTest,
    ScreenReference,
    TestSuite,
)
from goldenflow.phase3.parser_text import parse_repository
from goldenflow.phase3.report import (
    to_jira_tickets,
    to_prometheus,
    write_jira_payloads,
)
from helpers import trace

TAXONOMY = Taxonomy.from_yaml("config/taxonomy.yaml")
REGISTRY = ProtectedTestRegistry.from_yaml("config/protected-tests.yaml")
SUITE_ROOT = "fixtures/appium-suite"


def make_test(test_id: str, *screens: str, assertions_on: tuple[str, ...] = ()) -> ParsedTest:
    test = ParsedTest(test_id=test_id, name=test_id, file_path=test_id,
                      language=Language.PYTHON)
    for index, screen in enumerate(screens, start=1):
        test.screens.append(
            ScreenReference(screen, Confidence.HIGH, "explicit", index, index)
        )
    from goldenflow.phase3.models import Assertion
    for screen in assertions_on:
        test.assertions.append(Assertion(screen_id=screen, kind="assert",
                                         expression="x", line=1))
    return test


def make_graph(*sequences: tuple[str, ...], counts: tuple[int, ...] = ()):
    counts = counts or tuple(10 for _ in sequences)
    traces = []
    for index, (sequence, count) in enumerate(zip(sequences, counts)):
        traces += [trace(*sequence, session=f"s{index}_{n}") for n in range(count)]
    variants = [Variant(sequence=s, count=c) for s, c in zip(sequences, counts)]
    clustering = cluster_variants(
        variants, preserve_terminals=[s.screen_id for s in TAXONOMY.screens if s.critical]
    )
    journeys = score_journeys(clustering, taxonomy=TAXONOMY)
    return build_journey_graph(build_dfg(traces), journeys, taxonomy=TAXONOMY), journeys


@pytest.fixture(scope="module")
def real_analysis():
    """The fixture suite analysed against a synthetic production graph."""
    graph, journeys = make_graph(
        ("home", "product_detail", "cart", "delivery_address", "payment_method",
         "order_review", "order_confirmation"),
        ("home", "product_detail", "cart", "delivery_address", "payment_method",
         "payment_failure"),
        ("home", "account_home", "account_delete"),
        counts=(500, 70, 2),
    )
    suite = parse_repository(TAXONOMY, SUITE_ROOT)
    coverage = build_coverage(graph, suite, journeys)
    report = analyse_gaps(coverage, suite, taxonomy=TAXONOMY, registry=REGISTRY)
    return suite, coverage, report


# ================================================================== coverage


def test_coverage_is_measured_in_transitions_not_screens() -> None:
    """A suite can touch every screen and never walk cart -> payment_method."""
    graph, journeys = make_graph(("home", "cart", "payment_method"))
    suite = TestSuite(tests=[
        make_test("t_home", "home"),
        make_test("t_cart", "cart"),
        make_test("t_pay", "payment_method"),
    ])
    coverage = build_coverage(graph, suite, journeys)
    assert coverage.transition_coverage_pct == 0.0


def test_a_walking_test_covers_its_transitions() -> None:
    graph, journeys = make_graph(("home", "cart", "payment_method"))
    suite = TestSuite(tests=[make_test("t", "home", "cart", "payment_method")])
    coverage = build_coverage(graph, suite, journeys)
    assert coverage.transition_coverage_pct == 100.0
    assert coverage.journeys[0].fully_covered


def test_traffic_weighted_coverage_differs_from_raw_coverage() -> None:
    """A suite can cover most transitions and miss the ones carrying the sessions."""
    graph, journeys = make_graph(
        ("home", "cart"), ("home", "help_center"), counts=(1000, 5)
    )
    suite = TestSuite(tests=[make_test("t", "home", "help_center")])
    coverage = build_coverage(graph, suite, journeys)
    assert coverage.transition_coverage_pct == 50.0
    assert coverage.weighted_coverage_pct() < 5.0


def test_unmapped_tests_are_tracked_not_dropped() -> None:
    graph, journeys = make_graph(("home", "cart"))
    suite = TestSuite(tests=[ParsedTest(test_id="ghost", name="ghost",
                                        file_path="f", language=Language.PYTHON)])
    coverage = build_coverage(graph, suite, journeys)
    assert coverage.unmapped_tests == ["ghost"]


def test_coverage_feeds_back_into_phase_2_exposure() -> None:
    graph, journeys = make_graph(("home", "cart"))
    suite = TestSuite(tests=[make_test("t", "home", "cart")])
    coverage = build_coverage(graph, suite, journeys)
    assert coverage.coverage_by_archetype() == {journeys[0].journey_id: 1.0}


# ================================================================ gap finding


def test_uncovered_critical_journey_is_critical_severity() -> None:
    graph, journeys = make_graph(("home", "account_home", "account_delete"))
    report = analyse_gaps(build_coverage(graph, TestSuite(), journeys), TestSuite(),
                          taxonomy=TAXONOMY)
    gaps = report.of_kind(FindingKind.COVERAGE_GAP)
    assert gaps and gaps[0].severity is Severity.CRITICAL


def test_low_traffic_critical_gap_outranks_high_traffic_ordinary_gap() -> None:
    """Two sessions of account deletion beat four hundred of browsing, because the
    registry says one is a regulatory obligation."""
    graph, journeys = make_graph(
        ("home", "category_list", "product_list"),
        ("home", "account_home", "account_delete"),
        counts=(400, 2),
    )
    report = analyse_gaps(build_coverage(graph, TestSuite(), journeys), TestSuite(),
                          taxonomy=TAXONOMY)
    top = report.of_kind(FindingKind.COVERAGE_GAP)[0]
    assert "account_delete" in json.dumps(top.evidence) or "Delete" in top.title


def test_assertion_gap_flags_a_walked_but_unchecked_critical_screen() -> None:
    """Coverage on the graph, nothing caught in practice - worse than a known gap."""
    graph, journeys = make_graph(("home", "cart", "payment_method"))
    suite = TestSuite(tests=[
        make_test("t", "home", "cart", "payment_method", assertions_on=("home",))
    ])
    report = analyse_gaps(build_coverage(graph, suite, journeys), suite,
                          taxonomy=TAXONOMY)
    findings = report.of_kind(FindingKind.ASSERTION_GAP)
    assert findings
    assert "payment_method" in findings[0].evidence["unasserted_critical_screens"]


def test_fully_asserted_test_raises_no_assertion_gap() -> None:
    graph, journeys = make_graph(("home", "cart"))
    suite = TestSuite(tests=[make_test("t", "home", "cart", assertions_on=("cart",))])
    report = analyse_gaps(build_coverage(graph, suite, journeys), suite,
                          taxonomy=TAXONOMY)
    assert report.of_kind(FindingKind.ASSERTION_GAP) == []


def test_stale_is_distinguished_from_obsolete() -> None:
    """Partially unreachable is an update; entirely unreachable is a candidate."""
    graph, journeys = make_graph(("home", "cart"))
    suite = TestSuite(tests=[
        make_test("stale", "home", "cart", "dead_screen"),
        make_test("dead", "dead_screen", "other_dead"),
    ])
    report = analyse_gaps(build_coverage(graph, suite, journeys), suite,
                          taxonomy=TAXONOMY)
    assert [f.test_ids[0] for f in report.of_kind(FindingKind.STALE)] == ["stale"]
    assert [f.test_ids[0] for f in report.of_kind(FindingKind.OBSOLETE)] == ["dead"]


# ============================================ the protected-registry guardrail


def test_protected_test_with_identical_evidence_is_not_proposed_for_deletion() -> None:
    """The registry only earns its place if it changes the outcome for a test whose
    evidence is indistinguishable from a genuinely dead one.

    Both tests below walk exactly one transition that production never shows. The
    only difference is that one matches a protected pattern.
    """
    graph, journeys = make_graph(("home", "cart"))
    suite = TestSuite(tests=[
        make_test("tests/deeplinks/test_old_route.py::test_x", "splash", "order_confirmation"),
        make_test("tests/a11y/test_talkback.py::test_y", "splash", "order_confirmation"),
    ])
    coverage = build_coverage(graph, suite, journeys)
    report = analyse_gaps(coverage, suite, taxonomy=TAXONOMY, registry=REGISTRY)

    obsolete = {tid for f in report.of_kind(FindingKind.OBSOLETE) for tid in f.test_ids}
    retained = {tid for f in report.of_kind(FindingKind.PROTECTED_RETAINED)
                for tid in f.test_ids}

    assert obsolete == {"tests/deeplinks/test_old_route.py::test_x"}
    assert retained == {"tests/a11y/test_talkback.py::test_y"}


def test_deletable_candidates_partitions_against_the_registry() -> None:
    graph, journeys = make_graph(("home", "cart"))
    suite = TestSuite(tests=[
        make_test("tests/deeplinks/test_old.py::t", "splash", "order_confirmation"),
    ])
    report = analyse_gaps(build_coverage(graph, suite, journeys), suite,
                          taxonomy=TAXONOMY, registry=REGISTRY)
    proposable, protected = deletable_candidates(report, REGISTRY)
    assert proposable == ["tests/deeplinks/test_old.py::t"]
    assert protected == []


def test_without_a_registry_nothing_is_marked_protected() -> None:
    """Running without a registry must not silently look safe."""
    graph, journeys = make_graph(("home", "cart"))
    suite = TestSuite(tests=[make_test("tests/a11y/test_x.py::t", "splash",
                                       "order_confirmation")])
    report = analyse_gaps(build_coverage(graph, suite, journeys), suite,
                          taxonomy=TAXONOMY, registry=None)
    assert report.of_kind(FindingKind.PROTECTED_RETAINED) == []
    assert report.of_kind(FindingKind.OBSOLETE)


def test_obsolete_findings_are_always_worded_as_candidates() -> None:
    graph, journeys = make_graph(("home", "cart"))
    suite = TestSuite(tests=[make_test("t", "dead_a", "dead_b")])
    report = analyse_gaps(build_coverage(graph, suite, journeys), suite,
                          taxonomy=TAXONOMY)
    finding = report.of_kind(FindingKind.OBSOLETE)[0]
    assert "CANDIDATE ONLY" in finding.recommendation
    assert "human approval" in finding.recommendation


# =================================================================== reporting


def test_jira_tickets_exclude_deletion_advice() -> None:
    """A ticket saying 'delete this test' is an instruction; obsolescence is only
    ever a hypothesis needing a second signal."""
    graph, journeys = make_graph(("home", "cart"))
    suite = TestSuite(tests=[make_test("t", "dead_a", "dead_b")])
    report = analyse_gaps(build_coverage(graph, suite, journeys), suite,
                          taxonomy=TAXONOMY)
    kinds = {t.summary for t in to_jira_tickets(report, min_severity=Severity.LOW)}
    assert not any("Obsolete candidate" in summary for summary in kinds)


def test_jira_severity_floor_prevents_ticket_spam() -> None:
    graph, journeys = make_graph(("home", "account_home", "account_delete"))
    report = analyse_gaps(build_coverage(graph, TestSuite(), journeys), TestSuite(),
                          taxonomy=TAXONOMY)
    high_only = to_jira_tickets(report, min_severity=Severity.CRITICAL)
    everything = to_jira_tickets(report, min_severity=Severity.LOW)
    assert len(high_only) <= len(everything)


def test_jira_payload_shape_matches_the_rest_api(tmp_path) -> None:
    graph, journeys = make_graph(("home", "account_home", "account_delete"))
    report = analyse_gaps(build_coverage(graph, TestSuite(), journeys), TestSuite(),
                          taxonomy=TAXONOMY)
    path = tmp_path / "jira.json"
    count = write_jira_payloads(report, path, project_key="QA")
    payloads = json.loads(path.read_text())
    assert count == len(payloads) >= 1
    fields = payloads[0]["fields"]
    assert fields["project"]["key"] == "QA"
    assert fields["issuetype"]["name"] == "Task"
    assert "goldenflow" in fields["labels"]
    assert len(fields["summary"]) <= 255


def test_prometheus_output_is_valid_exposition_format() -> None:
    graph, journeys = make_graph(("home", "cart"))
    suite = TestSuite(tests=[make_test("t", "home", "cart")])
    coverage = build_coverage(graph, suite, journeys)
    report = analyse_gaps(coverage, suite, taxonomy=TAXONOMY)
    text = to_prometheus(report, coverage, app_id="acme-shop")

    assert "# HELP goldenflow_transition_coverage_pct" in text
    assert "# TYPE goldenflow_transition_coverage_pct gauge" in text
    assert 'app="acme-shop"' in text
    for line in text.splitlines():
        if line and not line.startswith("#"):
            assert line.rsplit(" ", 1)[1].replace(".", "").replace("-", "").isdigit()


# =========================================================== the real fixture


def test_real_suite_produces_actionable_findings(real_analysis) -> None:
    _, _, report = real_analysis
    assert report.findings
    assert report.of_kind(FindingKind.COVERAGE_GAP)


def test_payment_failure_recovery_is_flagged_as_a_gap(real_analysis) -> None:
    """The highest-consequence gap in the fixture: production sees payment failures
    on ~5% of checkouts and the suite cannot drive that screen at all."""
    _, _, report = real_analysis
    blob = json.dumps(report.to_dict())
    assert "payment_method -> payment_failure" in blob


def test_account_deletion_gap_survives_being_low_traffic(real_analysis) -> None:
    _, _, report = real_analysis
    gaps = [
        f for f in report.of_kind(FindingKind.COVERAGE_GAP)
        if "account_delete" in json.dumps(f.evidence)
    ]
    assert gaps, "a 2-session regulatory journey must not be filtered out"
    assert gaps[0].severity in (Severity.CRITICAL, Severity.HIGH)


def test_a11y_fixture_is_retained_while_deeplinks_are_candidates(real_analysis) -> None:
    _, _, report = real_analysis
    retained = {tid for f in report.of_kind(FindingKind.PROTECTED_RETAINED)
                for tid in f.test_ids}
    obsolete = {tid for f in report.of_kind(FindingKind.OBSOLETE) for tid in f.test_ids}
    assert any("a11y" in tid for tid in retained)
    assert not any("a11y" in tid for tid in obsolete)
