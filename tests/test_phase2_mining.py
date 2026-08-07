"""Sessionization, process mining, clustering and scoring tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from goldenflow.phase0.taxonomy import Taxonomy
from goldenflow.phase1.store import CanonicalEvent
from goldenflow.phase2.clustering import (
    cluster_variants,
    common_backbone,
    sequence_similarity,
)
from goldenflow.phase2.mining import (
    Variant,
    build_dfg,
    coverage_curve,
    dropoff_points,
    entropy,
    extract_variants,
    pm4py_available,
    variants_for_coverage,
)
from goldenflow.phase2.naming import (
    JourneyDescription,
    LlmJourneyNamer,
    RuleBasedNamer,
    name_journeys,
)
from goldenflow.phase2.scoring import (
    BusinessValueConfig,
    ScoringWeights,
    score_journeys,
)
from goldenflow.phase2.sessionize import MIN_TRACE_LENGTH, sessionize
from helpers import T0, ev, trace, variant

TAXONOMY = Taxonomy.from_yaml("config/taxonomy.yaml")


# ============================================================ sessionization


def test_consecutive_duplicate_screens_collapse() -> None:
    """Real SDKs emit screen_view on every re-render. Left alone the variant space
    explodes into thousands of near-identical paths."""
    result = sessionize([ev("home", 0), ev("home", 5), ev("home", 9), ev("cart", 20)])
    assert result.traces[0].sequence == ("home", "cart")


def test_collapse_can_be_disabled() -> None:
    result = sessionize([ev("home", 0), ev("home", 5)], collapse_repeats=False)
    assert result.traces[0].sequence == ("home", "home")


def test_collapsed_step_retains_all_its_events() -> None:
    result = sessionize([ev("home", 0), ev("home", 5, name="tap"), ev("cart", 20)])
    assert len(result.traces[0].steps[0].events) == 2
    assert result.traces[0].steps[0].dwell_seconds == 5


def test_long_gap_splits_the_session() -> None:
    """A user who backgrounds for two hours is on a second journey, whatever the
    SDK's session ID says."""
    result = sessionize([ev("home", 0), ev("cart", 10), ev("home", 7200),
                         ev("search", 7210)])
    assert len(result.traces) == 2
    assert result.traces_split_on_inactivity == 1
    assert result.traces[0].sequence == ("home", "cart")
    assert result.traces[1].session_id.endswith("#1")


def test_inactivity_timeout_is_configurable() -> None:
    events = [ev("home", 0), ev("cart", 600)]
    assert len(sessionize(events).traces) == 1
    assert len(sessionize(events, inactivity_timeout=timedelta(minutes=5)).traces) == 2


def test_events_without_session_or_screen_are_excluded_and_counted() -> None:
    """Dropping them silently would let a broken SDK look like a quiet week."""
    events = [
        ev("home", 0), ev("cart", 10),
        CanonicalEvent(event_name="x", screen_id="home", session_id=None,
                       timestamp=T0).finalised(),
        CanonicalEvent(event_name="x", screen_id=None, screen_tag="Legacy",
                       session_id="s2", timestamp=T0).finalised(),
    ]
    result = sessionize(events)
    assert result.events_without_session == 1
    assert result.events_without_screen == 1
    assert len(result.traces) == 1


def test_events_are_ordered_regardless_of_input_order() -> None:
    result = sessionize([ev("cart", 20), ev("home", 0), ev("search", 10)])
    assert result.traces[0].sequence == ("home", "search", "cart")


def test_single_screen_traces_are_kept_but_excluded_from_mining() -> None:
    result = sessionize([ev("home", 0), ev("home", 5)])
    assert len(result.traces) == 1
    assert result.traces_too_short == 1
    assert result.usable == []
    assert MIN_TRACE_LENGTH == 2


def test_identity_revealed_mid_session_is_attributed_to_the_whole_trace() -> None:
    """A session starts anonymous and identifies at login. Taking the first event's
    user_id returns None for exactly the sessions where a user WAS identified -
    silently undercounting the auth and checkout journeys."""
    events = [
        ev("splash", 0, user=None),
        ev("login", 10, user=None),
        ev("otp_verify", 20, user="user-42"),
        ev("home", 30, user="user-42"),
    ]
    trace = sessionize(events).traces[0]
    assert trace.user_id == "user-42"
    assert trace.identified


def test_a_later_login_supersedes_an_earlier_one() -> None:
    events = [ev("home", 0, user="user-1"), ev("login", 10, user="user-2")]
    assert sessionize(events).traces[0].user_id == "user-2"


def test_a_fully_anonymous_session_stays_anonymous() -> None:
    events = [ev("home", 0, user=None), ev("cart", 10, user=None)]
    trace = sessionize(events).traces[0]
    assert trace.user_id is None and not trace.identified


def test_trace_exposes_interactions_and_property_sums() -> None:
    result = sessionize([
        ev("cart", 0), ev("cart", 5, name="checkout_start", cart_value=40.0),
        ev("order_review", 10), ev("order_review", 12, name="place_order",
                                   order_value=99.5),
    ])
    t = result.traces[0]
    assert t.all_interactions == ["checkout_start", "place_order"]
    assert t.property_sum("order_value") == 99.5
    assert t.terminal_screen == "order_review"


# =============================================================== mining


def test_dfg_counts_nodes_edges_starts_and_ends() -> None:
    dfg = build_dfg([trace("home", "cart", "payment_method"),
                     trace("home", "cart", "payment_failure", session="s2")])
    assert dfg.trace_count == 2
    assert dfg.node_counts["cart"] == 2
    assert dfg.edge_weight("home", "cart") == 2
    assert dfg.edge_weight("cart", "payment_method") == 1
    assert dfg.start_counts == {"home": 2}
    assert set(dfg.end_counts) == {"payment_method", "payment_failure"}


def test_transition_probability_is_the_markov_view() -> None:
    dfg = build_dfg([
        trace("cart", "payment_method"), trace("cart", "payment_method", session="s2"),
        trace("cart", "home", session="s3"),
    ])
    assert dfg.transition_probability("cart", "payment_method") == pytest.approx(2 / 3)
    assert dfg.transition_probability("cart", "nowhere") == 0.0


def test_single_screen_traces_do_not_enter_the_dfg() -> None:
    assert build_dfg([trace("home")]).trace_count == 0


def test_noise_filter_drops_rare_edges_and_orphaned_nodes() -> None:
    traces = [trace("home", "cart") for _ in range(10)] + [trace("home", "oddity")]
    filtered = build_dfg(traces).filter_noise(min_weight=2)
    assert filtered.edge_weight("home", "oddity") == 0
    assert "oddity" not in filtered.nodes


def test_variants_group_by_sequence_and_rank_by_frequency() -> None:
    traces = [trace("home", "cart") for _ in range(3)] + [trace("home", "search")]
    variants = extract_variants(traces)
    assert variants[0].sequence == ("home", "cart") and variants[0].count == 3
    assert variants[1].count == 1


def test_variant_labels_truncate_long_paths() -> None:
    long = variant(*[f"s{i}" for i in range(10)])
    assert "..." in long.label() and long.label().endswith("s9")
    assert variant("home", "cart").label() == "home -> cart"


def test_coverage_curve_is_cumulative_and_reaches_100() -> None:
    variants = [variant("a", "b", count=80), variant("c", "d", count=15),
                variant("e", "f", count=5)]
    curve = coverage_curve(variants)
    assert curve[0] == (1, 80.0)
    assert curve[-1][1] == 100.0
    assert variants_for_coverage(variants, 95.0) == 2


def test_entropy_distinguishes_concentrated_from_diffuse_behaviour() -> None:
    """A high value means no top-N list will be representative, however scored."""
    concentrated = [variant("a", "b", count=1000), variant("c", "d", count=1)]
    diffuse = [variant(f"s{i}", f"t{i}", count=10) for i in range(16)]
    assert entropy(concentrated) < 0.1
    assert entropy(diffuse) == pytest.approx(4.0)
    assert entropy([]) == 0.0


def test_dropoff_points_rank_terminal_screens() -> None:
    traces = [trace("home", "cart") for _ in range(3)] + [trace("home", "search")]
    assert list(dropoff_points(traces)) == ["cart", "search"]


@pytest.mark.skipif(not pm4py_available(), reason="pm4py not installed")
def test_pm4py_inductive_miner_discovers_a_sound_model() -> None:
    from goldenflow.phase2.mining import discover_process_model
    traces = [trace("home", "cart", "order_review", session=f"s{i}") for i in range(5)]
    tree, net, initial, final = discover_process_model(traces)
    assert tree is not None and len(net.transitions) > 0


# ============================================================ clustering


@pytest.mark.parametrize("a,b,expected", [
    (("home", "cart"), ("home", "cart"), 1.0),
    (("home", "cart"), ("home", "search"), 0.5),
    (("a", "b"), ("c", "d"), 0.0),
    ((), (), 1.0),
])
def test_sequence_similarity(a, b, expected) -> None:
    assert sequence_similarity(a, b) == pytest.approx(expected)


def test_similarity_rewards_order_and_tolerates_insertions() -> None:
    """An extra screen en route to checkout is still the checkout journey, which is
    why LCS is used rather than edit distance."""
    base = ("home", "cart", "payment_method")
    inserted = ("home", "search", "cart", "payment_method")
    reordered = ("payment_method", "cart", "home")
    assert sequence_similarity(base, inserted) > sequence_similarity(base, reordered)


def test_common_backbone_extracts_the_shared_skeleton() -> None:
    assert common_backbone(("a", "b", "c", "d"), ("a", "x", "c", "y")) == ("a", "c")
    assert common_backbone(("a",), ()) == ()


def test_clustering_merges_similar_and_separates_dissimilar() -> None:
    result = cluster_variants([
        variant("home", "cart", "payment_method", count=100),
        variant("home", "search", "cart", "payment_method", count=50),
        variant("account_home", "help_center", count=30),
    ])
    assert len(result.archetypes) == 2
    assert result.archetypes[0].session_count == 150


def test_clustering_is_deterministic_regardless_of_input_order() -> None:
    """The archetype a journey belongs to must not change because the sample was
    shuffled, or drift detection becomes noise."""
    variants = [variant("home", "cart", count=50), variant("home", "search", count=30),
                variant("account_home", "profile_edit", count=10)]
    a = cluster_variants(variants)
    b = cluster_variants(list(reversed(variants)))
    assert [x.sequence for x in a.archetypes] == [x.sequence for x in b.archetypes]


def test_archetype_representative_is_the_most_frequent_member() -> None:
    result = cluster_variants([
        variant("home", "cart", "payment_method", count=10),
        variant("home", "cart", "payment_method", "order_review", count=100),
    ])
    assert result.archetypes[0].sequence[-1] == "order_review"


# ------------------------------------------------- the terminal-preservation rule


def test_critical_terminals_are_never_merged_away() -> None:
    """The defect this guards against is catastrophic, not untidy: an invisible
    journey produces no gap in Phase 3, so Phase 5 never generates its test."""
    variants = [
        variant("home", "account_home", "order_history", count=500),
        variant("home", "account_home", "account_delete", count=2),
        variant("home", "account_home", "data_export", count=5),
    ]
    merged = cluster_variants(variants)
    assert len(merged.archetypes) == 1, "0.67 similarity merges them by default"

    preserved = cluster_variants(
        variants, preserve_terminals=["account_delete", "data_export"]
    )
    terminals = {a.representative.terminal_screen for a in preserved.archetypes}
    assert {"account_delete", "data_export", "order_history"} == terminals


def test_non_critical_terminals_still_merge_normally() -> None:
    """Preservation must be precisely scoped or it defeats clustering entirely."""
    result = cluster_variants(
        [variant("home", "account_home", "order_history", count=100),
         variant("home", "account_home", "address_book", count=20)],
        preserve_terminals=["account_delete"],
    )
    assert len(result.archetypes) == 1


def test_capacity_cap_cannot_erase_a_preserved_journey() -> None:
    """A capacity limit must not be able to do what the threshold is forbidden to."""
    result = cluster_variants(
        [variant("home", "account_home", "order_history", count=100),
         variant("home", "account_home", "account_delete", count=1)],
        max_archetypes=1, preserve_terminals=["account_delete"],
    )
    assert len(result.archetypes) == 2


def test_capacity_cap_forces_merges_where_it_is_allowed_to() -> None:
    result = cluster_variants(
        [variant("home", "cart", count=100), variant("account_home", "help_center", count=1)],
        max_archetypes=1,
    )
    assert len(result.archetypes) == 1


def test_clustering_summary_reports_compression() -> None:
    result = cluster_variants([variant("home", "cart", count=10),
                               variant("home", "cart", "search", count=5)])
    summary = result.summary()
    assert summary["archetypes"] == 1 and summary["variants"] == 2
    assert summary["compression_ratio"] == 2.0


def test_backbone_is_shared_by_every_member() -> None:
    result = cluster_variants([
        variant("home", "cart", "payment_method", count=10),
        variant("home", "search", "cart", "payment_method", count=8),
    ])
    assert result.archetypes[0].backbone() == ("home", "cart", "payment_method")


# ============================================================== scoring


def _clustered(*variants_):
    return cluster_variants(list(variants_), preserve_terminals=["order_confirmation"])


def test_weighted_sum_not_product_so_a_zero_does_not_erase_a_journey() -> None:
    """Multiplying the terms would zero a high-traffic revenue journey that happens
    to have no recorded crashes."""
    clustering = _clustered(variant("home", "cart", "order_confirmation", count=1000))
    journeys = score_journeys(clustering, taxonomy=TAXONOMY, risk_by_screen={})
    assert journeys[0].components.risk == 0.0
    assert journeys[0].score > 0


def test_frequency_alone_does_not_win() -> None:
    """The product thesis: browse-and-leave is the most-walked path and not the
    most important one."""
    clustering = _clustered(
        variant("home", "product_list", "product_detail", count=5000),
        variant("home", "cart", "order_confirmation", count=200),
    )
    journeys = score_journeys(
        clustering, taxonomy=TAXONOMY,
        monetary_by_archetype={a.archetype_id: (50000.0 if "order_confirmation"
                                                in a.sequence else 0.0)
                               for a in clustering.archetypes},
    )
    assert journeys[0].sequence[-1] == "order_confirmation"


def test_risk_signals_raise_a_journey() -> None:
    clustering = _clustered(variant("home", "cart", count=100),
                            variant("home", "search", count=100))
    journeys = score_journeys(clustering, taxonomy=TAXONOMY,
                              risk_by_screen={"search": 500})
    assert journeys[0].sequence[-1] == "search"


def test_exposure_defaults_to_fully_uncovered() -> None:
    """The correct prior before Phase 3 has analysed the suite."""
    clustering = _clustered(variant("home", "cart", count=10))
    assert score_journeys(clustering)[0].components.exposure == 1.0

    covered = score_journeys(clustering, coverage_by_archetype={"aj_001": 1.0})
    assert covered[0].components.exposure == 0.0


def test_weights_are_normalised_so_they_need_not_sum_to_one() -> None:
    clustering = _clustered(variant("home", "cart", count=10))
    a = score_journeys(clustering, weights=ScoringWeights(1, 1, 1, 1))
    b = score_journeys(clustering, weights=ScoringWeights(10, 10, 10, 10))
    assert a[0].score == pytest.approx(b[0].score)


def test_critical_screens_carry_business_value_without_revenue_data() -> None:
    clustering = _clustered(variant("home", "account_home", "account_delete", count=2))
    journeys = score_journeys(clustering, taxonomy=TAXONOMY)
    assert journeys[0].components.business_value > 0


def test_ranks_are_assigned_and_explanations_render() -> None:
    clustering = _clustered(variant("home", "cart", count=100),
                            variant("account_home", "help_center", count=10))
    journeys = score_journeys(clustering, taxonomy=TAXONOMY)
    assert [j.rank for j in journeys] == [1, 2]
    assert "score" in journeys[0].explain()
    assert set(journeys[0].to_dict()) >= {"journey_id", "score", "components"}


def test_scoring_empty_clustering_returns_nothing() -> None:
    assert score_journeys(cluster_variants([])) == []


# =============================================================== naming


def _journey(*screens: str, count: int = 10):
    clustering = cluster_variants([variant(*screens, count=count)])
    return score_journeys(clustering, taxonomy=TAXONOMY)[0]


@pytest.mark.parametrize("terminal,outcome", [
    ("order_confirmation", "completed"),
    ("payment_failure", "failed"),
    ("cart", "abandoned"),
    ("help_center", "exploratory"),
])
def test_rule_based_namer_classifies_outcomes(terminal: str, outcome: str) -> None:
    namer = RuleBasedNamer(TAXONOMY)
    assert namer.classify(("home", terminal)) == outcome


def test_rule_based_namer_uses_taxonomy_display_names() -> None:
    described = RuleBasedNamer(TAXONOMY).describe(_journey("home", "product_detail"))
    assert "Product Detail" in described.name
    assert "sessions" in described.description


def test_name_journeys_annotates_in_place() -> None:
    journeys = [_journey("home", "cart")]
    name_journeys(journeys, RuleBasedNamer(TAXONOMY))
    assert journeys[0].archetype.name and journeys[0].archetype.description


def test_llm_namer_parses_a_well_formed_response() -> None:
    def complete(_prompt: str) -> str:
        return '```json\n{"name": "Guest Checkout", "description": "d", ' \
               '"outcome": "completed"}\n```'
    described = LlmJourneyNamer(complete, taxonomy=TAXONOMY).describe(
        _journey("home", "cart"))
    assert described == JourneyDescription("Guest Checkout", "d", "completed")


@pytest.mark.parametrize("response", ["not json", '{"name": ""}', ""])
def test_llm_namer_falls_back_rather_than_breaking_the_pipeline(response: str) -> None:
    """Naming is presentation. It must never take down the pipeline that produced
    the numbers."""
    namer = LlmJourneyNamer(lambda _p: response, taxonomy=TAXONOMY)
    assert namer.describe(_journey("home", "cart")).name


def test_llm_namer_falls_back_when_the_model_raises() -> None:
    def boom(_prompt: str) -> str:
        raise RuntimeError("503 from provider")
    assert LlmJourneyNamer(boom, taxonomy=TAXONOMY).describe(_journey("home", "cart")).name


def test_llm_prompt_is_inspectable_and_carries_the_mined_facts() -> None:
    """Exposed so it can be reviewed and version-pinned rather than trusted."""
    prompt = LlmJourneyNamer(lambda _p: "", taxonomy=TAXONOMY).build_prompt(
        _journey("home", "cart"))
    assert "deterministic pipeline and are authoritative" in prompt
    assert "sessions" in prompt and "Return ONLY JSON" in prompt
