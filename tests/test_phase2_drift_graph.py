"""Journey drift and journey graph tests."""

from __future__ import annotations

import pytest

from goldenflow.phase0.taxonomy import Taxonomy
from goldenflow.phase2.clustering import cluster_variants
from goldenflow.phase2.drift import (
    DriftThresholds,
    biggest_movers,
    detect_drift,
    jensen_shannon_divergence,
    novel_variant_rate,
    population_stability_index,
    vanished_variants,
)
from goldenflow.phase2.graph import build_journey_graph
from goldenflow.phase2.mining import Variant, build_dfg
from goldenflow.phase2.scoring import score_journeys
from goldenflow.phase2.sessionize import sessionize
from helpers import ev, trace, variant

TAXONOMY = Taxonomy.from_yaml("config/taxonomy.yaml")


# ================================================================== drift


def test_identical_distributions_have_zero_divergence() -> None:
    d = {"a": 50.0, "b": 50.0}
    assert jensen_shannon_divergence(d, dict(d)) == 0.0
    assert population_stability_index(d, dict(d)) == 0.0


def test_disjoint_distributions_saturate_divergence() -> None:
    assert jensen_shannon_divergence({"a": 10.0}, {"b": 10.0}) == 1.0


def test_divergence_is_symmetric() -> None:
    """The reason JS is used rather than KL - and KL is undefined when a path
    exists in one window and not the other, which is the common case here."""
    a, b = {"x": 70.0, "y": 30.0}, {"x": 40.0, "y": 60.0}
    assert jensen_shannon_divergence(a, b) == jensen_shannon_divergence(b, a)


def test_divergence_scales_with_the_size_of_the_shift() -> None:
    base = {"x": 50.0, "y": 50.0}
    small = jensen_shannon_divergence(base, {"x": 55.0, "y": 45.0})
    large = jensen_shannon_divergence(base, {"x": 95.0, "y": 5.0})
    assert 0 < small < large


def test_empty_distributions_do_not_raise() -> None:
    assert jensen_shannon_divergence({}, {}) == 0.0
    assert jensen_shannon_divergence({"a": 1.0}, {}) == 1.0
    assert population_stability_index({}, {"a": 1.0}) == 0.0


def test_psi_crosses_its_conventional_thresholds() -> None:
    stable = population_stability_index({"a": 50.0, "b": 50.0},
                                        {"a": 52.0, "b": 48.0})
    significant = population_stability_index({"a": 50.0, "b": 50.0},
                                             {"a": 90.0, "b": 10.0})
    assert stable < 0.1 < significant
    assert significant > 0.25


def test_zero_bins_do_not_blow_up_psi() -> None:
    assert population_stability_index({"a": 100.0, "b": 0.0},
                                      {"a": 50.0, "b": 50.0}) > 0


def test_psi_noise_floor_stays_well_below_the_warn_threshold() -> None:
    """The calibration behind PSI_TOP_BINS=10.

    A resampled-but-unchanged process must not read as drift. This pins the noise
    floor so a future change to the bin count cannot silently reintroduce false
    positives - which is exactly what top_k=20 did.
    """
    import random
    from goldenflow.phase2.drift import DriftThresholds

    rng = random.Random(20260803)
    shape = {f"path_{i}": 500.0 / (i + 1) for i in range(150)}
    baseline = {k: v * rng.uniform(0.9, 1.1) for k, v in shape.items()}
    current = {k: v * rng.uniform(0.9, 1.1) for k, v in shape.items()}

    psi = population_stability_index(baseline, current)
    assert psi < DriftThresholds().psi_warn / 2, (
        f"noise floor {psi} is too close to the warn threshold"
    )


def test_psi_buckets_the_sparse_tail_to_stay_meaningful() -> None:
    """Two draws from an *identical* long-tailed process score ~0.25 unbucketed -
    'significant shift' from pure sampling noise. Journey distributions have
    hundreds of sparse bins; PSI's published thresholds assume about ten dense ones.
    """
    baseline = {f"path_{i}": float(200 - i) for i in range(120)}
    # Same shape, jittered as a resample would jitter it.
    current = {f"path_{i}": float(200 - i + (1 if i % 2 else -1)) for i in range(120)}

    noisy = population_stability_index(baseline, current, top_k=None)
    bucketed = population_stability_index(baseline, current)
    assert bucketed < noisy
    assert bucketed < 0.1, "an unchanged process must not read as drift"


def test_psi_bucketing_still_detects_a_real_shift() -> None:
    """Bucketing must not be able to hide genuine movement in the head."""
    baseline = {"a": 900.0, "b": 100.0}
    current = {"a": 100.0, "b": 900.0}
    assert population_stability_index(baseline, current) > 0.25


def test_bucket_tail_keeps_the_head_and_aggregates_the_rest() -> None:
    from goldenflow.phase2.drift import bucket_tail
    bucketed = bucket_tail({f"k{i}": float(100 - i) for i in range(30)}, top_k=5)
    assert len(bucketed) == 6
    assert "__other__" in bucketed
    assert bucket_tail({"a": 1.0}, top_k=5) == {"a": 1.0}


def test_novel_variant_rate_measures_new_behaviour() -> None:
    baseline = [variant("home", "cart", count=100)]
    current = [variant("home", "cart", count=80), variant("home", "checkout_v2", count=20)]
    pct, novel = novel_variant_rate(baseline, current)
    assert pct == 20.0
    assert novel[0].sequence == ("home", "checkout_v2")


def test_vanished_variants_are_reported_largest_first() -> None:
    baseline = [variant("home", "old_flow", count=90), variant("home", "cart", count=10)]
    gone = vanished_variants(baseline, [variant("home", "cart", count=10)])
    assert [v.sequence for v in gone] == [("home", "old_flow")]


def test_biggest_movers_names_what_changed() -> None:
    """'drift = 0.31' tells a QE lead nothing; 'card -> wallet' tells them
    everything."""
    movers = biggest_movers({"card": 90.0, "wallet": 10.0},
                            {"card": 40.0, "wallet": 60.0})
    assert movers[0].key in {"card", "wallet"}
    assert abs(movers[0].delta_pct) == 50.0
    assert "->" in movers[0].format()


def test_stable_traffic_reports_no_drift() -> None:
    variants = [variant("home", "cart", count=100), variant("home", "search", count=50)]
    report = detect_drift(variants, [variant("home", "cart", count=101),
                                     variant("home", "search", count=50)])
    assert report.severity == "OK"
    assert not report.drifted


def test_a_large_behaviour_shift_raises_an_alert() -> None:
    baseline = [variant("home", "cart", "payment_method", count=1000),
                variant("home", "search", count=100)]
    current = [variant("home", "cart", "payment_method", count=100),
               variant("home", "search", count=1000),
               variant("home", "wallet_checkout", count=800)]
    report = detect_drift(baseline, current)
    assert report.severity == "ALERT"
    assert report.novel and report.novel[0].sequence == ("home", "wallet_checkout")


def test_dropoff_shift_is_detected_independently() -> None:
    """A funnel failing one step earlier is drift even when the overall
    distribution barely moves."""
    baseline = [variant("home", "cart", "order_confirmation", count=100)]
    current = [variant("home", "cart", "payment_failure", count=100)]
    report = detect_drift(baseline, current)
    dropoff = next(m for m in report.metrics if m.name == "dropoff_shift")
    assert dropoff.severity == "ALERT"


def test_thresholds_are_configurable() -> None:
    baseline = [variant("home", "cart", count=100)]
    current = [variant("home", "cart", count=70), variant("home", "search", count=30)]
    strict = detect_drift(baseline, current,
                          thresholds=DriftThresholds(js_divergence_alert=0.01))
    lax = detect_drift(baseline, current,
                       thresholds=DriftThresholds(js_divergence_warn=0.9,
                                                  js_divergence_alert=0.95,
                                                  novel_variant_warn_pct=99,
                                                  novel_variant_alert_pct=99.5,
                                                  psi_warn=99, psi_alert=100,
                                                  dropoff_shift_warn=0.9,
                                                  dropoff_shift_alert=0.95))
    assert strict.severity == "ALERT"
    assert lax.severity == "OK"


def test_drift_report_serialises_and_formats() -> None:
    report = detect_drift([variant("home", "cart", count=100)],
                          [variant("home", "search", count=100)])
    payload = report.to_dict()
    assert payload["severity"] == "ALERT"
    assert len(payload["metrics"]) == 4
    assert "Journey Drift" in report.format()


# ================================================================== graph


def _graph(*sequences: tuple[str, ...]):
    traces = [trace(*seq, session=f"s{i}") for i, seq in enumerate(sequences)]
    dfg = build_dfg(traces)
    variants = [Variant(sequence=seq, count=1) for seq in sequences]
    journeys = score_journeys(cluster_variants(variants), taxonomy=TAXONOMY)
    return build_journey_graph(dfg, journeys, taxonomy=TAXONOMY)


def test_graph_enriches_screens_from_the_taxonomy() -> None:
    graph = _graph(("home", "payment_method"))
    assert graph.screens["payment_method"].critical is True
    assert graph.screens["payment_method"].sensitivity == "critical"
    assert graph.screens["home"].display_name == "Home"


def test_graph_includes_declared_screens_with_no_traffic() -> None:
    """A critical screen with no traffic is either dead code or broken
    instrumentation, and both matter."""
    graph = _graph(("home", "cart"))
    assert "account_delete" in graph.screens
    assert graph.screens["account_delete"].visits == 0
    assert "account_delete" in graph.critical_screens_without_traffic()


def test_uncovered_transitions_are_the_phase_3_gap() -> None:
    graph = _graph(("home", "cart", "payment_method"))
    gaps = graph.uncovered_transitions(covered=[("home", "cart")])
    assert [(g.source, g.target) for g in gaps] == [("cart", "payment_method")]


def test_uncovered_transitions_respect_a_weight_floor() -> None:
    graph = _graph(("home", "cart"))
    assert graph.uncovered_transitions(covered=[], min_weight=99) == []


def test_unreachable_test_transitions_are_obsolete_candidates() -> None:
    graph = _graph(("home", "cart"))
    assert graph.unreachable_transitions([("home", "cart"), ("home", "dead_screen")]) \
        == [("home", "dead_screen")]


def test_entry_and_exit_counts_are_captured() -> None:
    graph = _graph(("home", "cart"), ("home", "search"))
    assert graph.screens["home"].entry_count == 2
    assert graph.screens["home"].is_entry_point
    assert graph.screens["cart"].exit_count == 1


def test_cypher_export_is_idempotent_and_constrained() -> None:
    cypher = _graph(("home", "cart")).to_cypher()
    assert "CREATE CONSTRAINT screen_id IF NOT EXISTS" in cypher
    assert cypher.count("MERGE (s:Screen") >= 2
    assert "MERGE (a)-[r:FOLLOWS]->(b)" in cypher
    assert "CREATE (" not in cypher     # nothing that would duplicate on re-run


def test_cypher_escapes_string_literals() -> None:
    cypher = _graph(("home", "cart")).to_cypher()
    assert '"home"' in cypher


def test_gap_query_matches_the_in_memory_implementation() -> None:
    """Kept beside each other so the two cannot drift apart unnoticed."""
    query = _graph(("home", "cart")).gap_query(min_weight=25)
    assert "r.weight >= 25" in query
    assert "NOT EXISTS" in query and ":COVERS" in query


def test_graph_serialises_to_json(tmp_path) -> None:
    graph = _graph(("home", "cart"))
    path = graph.write_json(tmp_path / "graph.json")
    assert path.exists()
    payload = graph.to_dict()
    assert payload["taxonomy_fingerprint"] == TAXONOMY.fingerprint()
    assert payload["screens"][0]["visits"] >= 1


def test_neo4j_load_raises_a_useful_error_when_the_driver_is_absent() -> None:
    pytest.importorskip
    try:
        import neo4j  # noqa: F401
        pytest.skip("neo4j driver installed; the ImportError path cannot be tested")
    except ImportError:
        pass
    with pytest.raises(ImportError, match="neo4j driver required"):
        _graph(("home", "cart")).load_into_neo4j("bolt://x", "u", "p")


# ======================================================= end-to-end shape


def test_full_pipeline_from_events_to_graph() -> None:
    events = []
    for i in range(30):
        for offset, screen in enumerate(["home", "product_detail", "cart",
                                         "order_confirmation"]):
            events.append(ev(screen, offset * 10, session=f"s{i}"))
    for i in range(2):
        for offset, screen in enumerate(["home", "account_home", "account_delete"]):
            events.append(ev(screen, offset * 10, session=f"d{i}"))

    traces = sessionize(events).usable
    from goldenflow.phase2.mining import extract_variants
    variants = extract_variants(traces)
    clustering = cluster_variants(
        variants,
        preserve_terminals=[s.screen_id for s in TAXONOMY.screens if s.critical],
    )
    journeys = score_journeys(clustering, taxonomy=TAXONOMY)
    graph = build_journey_graph(build_dfg(traces), journeys, taxonomy=TAXONOMY)

    terminals = {j.sequence[-1] for j in journeys}
    assert "order_confirmation" in terminals
    assert "account_delete" in terminals, "the 2-session critical journey survived"
    assert graph.transition("home", "product_detail").weight == 30
