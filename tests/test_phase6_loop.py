"""Outcome attribution, weight tuning and governance tests.

The tests that matter most here defend against two opposite failure modes: a system
that flatters itself (counting unresolved gaps as wins) and one that panics (reading
"too early to say" as "broken").
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from goldenflow.phase2.scoring import ScoringWeights
from goldenflow.phase6.governance import (
    AuditLog,
    CostLedger,
    CostRates,
    LineageRecord,
    hash_prompt,
)
from goldenflow.phase6.outcomes import (
    DefectOrigin,
    EscapedDefect,
    GapOutcome,
    attribute,
    build_report,
    load_defects,
)
from goldenflow.phase6.tuning import (
    MAX_STEP,
    MIN_EVIDENCE,
    MIN_WEIGHT,
    ComponentEvidence,
    apply_proposal,
    gather_evidence,
    propose_weights,
)

T0 = datetime(2026, 7, 14, tzinfo=timezone.utc)
FIXTURE = "fixtures/escaped-defects.json"


def defect(defect_id="BUG-1", journey_id="aj_001", **kw) -> EscapedDefect:
    return EscapedDefect(defect_id=defect_id, title="t", detected_at=T0,
                         journey_id=journey_id, **kw)


def gap(journey_id="aj_001", **kw) -> GapOutcome:
    return GapOutcome(journey_id=journey_id, reported_at=T0, **kw)


# ============================================================== attribution


def test_uncovered_golden_journey_is_an_uncovered_gap() -> None:
    result = attribute(defect(), golden_journey_ids=["aj_001"],
                       coverage_by_journey={"aj_001": 0.0})
    assert result.origin is DefectOrigin.UNCOVERED_GAP


def test_fully_covered_journey_is_covered_but_missed() -> None:
    """The most valuable signal there is: the coverage was real and the assertions
    were not."""
    result = attribute(defect(), golden_journey_ids=["aj_001"],
                       coverage_by_journey={"aj_001": 1.0},
                       tests_by_journey={"aj_001": ["tests/test_a.py::t"]})
    assert result.origin is DefectOrigin.COVERED_BUT_MISSED
    assert result.covering_tests == ["tests/test_a.py::t"]


def test_partial_coverage_still_counts_as_a_gap() -> None:
    result = attribute(defect(), golden_journey_ids=["aj_001"],
                       coverage_by_journey={"aj_001": 0.8})
    assert result.origin is DefectOrigin.UNCOVERED_GAP


def test_journey_outside_the_golden_set_is_unknown() -> None:
    result = attribute(defect(journey_id="aj_999"), golden_journey_ids=["aj_001"])
    assert result.origin is DefectOrigin.UNKNOWN_JOURNEY


def test_defect_with_no_journey_is_out_of_scope_not_a_miss() -> None:
    """Guessing would inflate the 'mining missed it' bucket with backend incidents
    that were never GoldenFlow's to catch."""
    result = attribute(defect(journey_id=None), golden_journey_ids=["aj_001"])
    assert result.origin is DefectOrigin.OUT_OF_SCOPE
    assert not result.in_scope


# =========================================================== precision/recall


def test_vindicated_gaps_are_true_positives() -> None:
    report = build_report([], [gap(caused_defect="BUG-1")],
                          golden_journey_ids=["aj_001"])
    assert report.true_positives == 1


def test_an_open_gap_is_unresolved_not_a_false_positive() -> None:
    """A gap that has not yet caused an incident is unproven, not wrong."""
    report = build_report([], [gap()], golden_journey_ids=["aj_001"])
    assert report.unresolved_gaps == 1
    assert report.false_positives == 0


def test_precision_is_none_rather_than_zero_when_nothing_has_resolved() -> None:
    """A hard zero reads as 'the system is broken' when the truth is 'too early to
    say', and that distinction decides whether a programme gets cancelled."""
    report = build_report([], [gap(), gap("aj_002")], golden_journey_ids=["aj_001"])
    assert report.precision is None


def test_precision_counts_closed_gaps_that_never_caused_an_incident() -> None:
    report = build_report([], [
        gap("aj_001", caused_defect="BUG-1"),
        gap("aj_002", closed_by_test="tests/t.py"),
    ], golden_journey_ids=["aj_001", "aj_002"])
    assert report.true_positives == 1 and report.false_positives == 1
    assert report.precision == 0.5


def test_covered_but_missed_defects_count_against_recall() -> None:
    report = build_report(
        [defect("BUG-1", "aj_001")], [gap("aj_002", caused_defect="BUG-9")],
        golden_journey_ids=["aj_001", "aj_002"],
        coverage_by_journey={"aj_001": 1.0},
    )
    assert report.false_negatives == 1
    assert report.recall == 0.5


def test_out_of_scope_defects_do_not_damage_recall() -> None:
    report = build_report(
        [defect("BUG-1", None), defect("BUG-2", None)],
        [gap(caused_defect="BUG-9")], golden_journey_ids=["aj_001"],
    )
    assert report.false_negatives == 0
    assert report.recall == 1.0


def test_assertion_blind_spot_measures_defects_that_passed_a_green_test() -> None:
    report = build_report(
        [defect("BUG-1", "aj_001"), defect("BUG-2", "aj_002")], [],
        golden_journey_ids=["aj_001", "aj_002"],
        coverage_by_journey={"aj_001": 1.0, "aj_002": 0.0},
    )
    assert report.assertion_blind_spot == 0.5


def test_report_serialises_and_formats() -> None:
    report = build_report([defect()], [gap(caused_defect="BUG-1")],
                          golden_journey_ids=["aj_001"])
    payload = report.to_dict()
    assert "by_origin" in payload and "precision" in payload
    assert "did the loop close" in report.format()


# ============================================================ the fixture


def test_fixture_defects_load() -> None:
    defects = load_defects(FIXTURE)
    assert len(defects) == 12
    assert any(d.journey_id is None for d in defects), "backend defects present"


def test_fixture_exercises_every_attribution_branch() -> None:
    raw = json.loads(open(FIXTURE, encoding="utf-8").read())
    gaps = [
        GapOutcome(journey_id=g["journey_id"],
                   reported_at=datetime.fromisoformat(
                       g["reported_at"].replace("Z", "+00:00")),
                   closed_by_test=g.get("closed_by_test"),
                   caused_defect=g.get("caused_defect"))
        for g in raw["gaps_reported"]
    ]
    report = build_report(
        load_defects(FIXTURE), gaps,
        golden_journey_ids=[f"aj_{i:03d}" for i in range(1, 20)],
        coverage_by_journey={"aj_003": 1.0, "aj_012": 1.0},
    )
    origins = report.by_origin()
    assert origins["uncovered_gap"] > 0
    assert origins["covered_but_missed"] > 0
    assert origins["unknown_journey"] > 0
    assert origins["out_of_scope"] > 0


# ================================================================== tuning


def evidence(**rates: tuple[int, int]) -> list[ComponentEvidence]:
    return [
        ComponentEvidence(component=name, high_score_defects=hi, low_score_defects=lo)
        for name, (hi, lo) in rates.items()
    ]


def report_with(n: int):
    return build_report(
        [defect(f"BUG-{i}", "aj_001") for i in range(n)], [],
        golden_journey_ids=["aj_001"],
    )


def test_tuning_refuses_to_move_on_thin_evidence() -> None:
    """Tuning on a handful of incidents produces confident nonsense - the same
    failure mode the Phase 2 PSI calibration exposed."""
    proposal = propose_weights(ScoringWeights(), report_with(MIN_EVIDENCE - 1),
                               evidence(risk=(3, 0)))
    assert proposal.is_noop
    assert "Insufficient evidence" in proposal.reason


def test_better_predictors_gain_weight() -> None:
    proposal = propose_weights(
        ScoringWeights(), report_with(MIN_EVIDENCE),
        evidence(frequency=(2, 8), business_value=(5, 5), risk=(9, 1),
                 exposure=(5, 5)),
    )
    assert proposal.deltas["risk"] > 0
    assert proposal.deltas["frequency"] < 0


def test_movement_is_capped_per_round() -> None:
    """A model that can swing its own priorities arbitrarily produces a ranking
    nobody can plan against."""
    proposal = propose_weights(
        ScoringWeights(), report_with(50),
        evidence(frequency=(0, 50), business_value=(0, 50), risk=(50, 0),
                 exposure=(0, 50)),
    )
    assert all(abs(d) <= MAX_STEP + 1e-9 for d in proposal.deltas.values())


def test_no_weight_can_be_driven_to_zero() -> None:
    """A component driven to zero can never earn its way back - the evidence that
    would correct it is exactly what it can no longer see."""
    weights = ScoringWeights(frequency=MIN_WEIGHT, business_value=0.3,
                             risk=0.3, exposure=0.3)
    proposal = propose_weights(weights, report_with(50),
                               evidence(frequency=(0, 50), risk=(50, 0)))
    assert proposal.after.frequency >= MIN_WEIGHT


def test_proposal_explains_itself() -> None:
    proposal = propose_weights(ScoringWeights(), report_with(MIN_EVIDENCE),
                               evidence(risk=(9, 1), frequency=(1, 9)))
    assert "predicted best" in proposal.reason
    assert "risk" in proposal.format() and "Evidence" in proposal.format()


def test_nothing_applies_itself() -> None:
    proposal = propose_weights(ScoringWeights(), report_with(MIN_EVIDENCE),
                               evidence(risk=(9, 1)))
    assert not proposal.applied
    apply_proposal(proposal)
    assert proposal.applied, "acceptance is explicit"


def test_gather_evidence_scores_components_against_defects() -> None:
    report = build_report([defect("BUG-1", "aj_001")], [],
                          golden_journey_ids=["aj_001"])
    found = gather_evidence(report, {"aj_001": {"risk": 0.9, "frequency": 0.1}})
    by_name = {e.component: e for e in found}
    assert by_name["risk"].high_score_defects == 1
    assert by_name["frequency"].low_score_defects == 1


# ============================================================== governance


def test_lineage_record_knows_when_it_is_incomplete() -> None:
    """An artifact whose origin cannot be reconstructed is not auditable."""
    complete = LineageRecord("t1", "test", journey_id="aj_001",
                             generator="template", taxonomy_fingerprint="abc")
    assert complete.complete
    assert not LineageRecord("t2", "test").complete


def test_audit_log_tracks_review_decisions() -> None:
    log = AuditLog()
    log.add(LineageRecord("t1", "test", review_decision="approved", reviewer="qa-lead"))
    log.add(LineageRecord("t2", "test", review_decision="rejected",
                          rejection_reason="locator too fragile"))
    log.add(LineageRecord("t3", "test", review_decision="pending"))

    summary = log.summary()
    assert summary["approved"] == 1 and summary["rejected"] == 1
    assert summary["pending"] == 1
    assert log.rejection_themes() == {"locator too fragile": 1}


def test_rejections_are_captured_as_labelled_data() -> None:
    """A reviewer saying no is information, not just a blocked merge."""
    log = AuditLog()
    for reason in ("assertion too weak", "assertion too weak", "wrong journey"):
        log.add(LineageRecord("t", "test", review_decision="rejected",
                              rejection_reason=reason))
    assert log.rejection_themes() == {"assertion too weak": 2, "wrong journey": 1}


def test_prompt_is_hashed_not_stored() -> None:
    """Prompts carry screen names and property keys; the hash proves which version
    ran without duplicating that content into a second store."""
    digest = hash_prompt("some prompt with ScreenName and order_value")
    assert len(digest) == 16
    assert "ScreenName" not in digest
    assert digest == hash_prompt("some prompt with ScreenName and order_value")


def test_audit_log_writes(tmp_path) -> None:
    log = AuditLog()
    log.add(LineageRecord("t1", "test", journey_id="aj_001", generator="template",
                          taxonomy_fingerprint="abc"))
    payload = json.loads(log.write(tmp_path / "lineage.json").read_text())
    assert payload["records"][0]["artifact_id"] == "t1"
    assert payload["summary"]["complete_lineage"] == 1


# ==================================================================== cost


def test_cost_ledger_accumulates_by_category() -> None:
    ledger = CostLedger(rates=CostRates(llm_per_1k_input_tokens=0.01,
                                        llm_per_1k_output_tokens=0.02,
                                        device_minute=0.10))
    ledger.record_generation("t1", input_tokens=1000, output_tokens=500)
    ledger.record_validation("t1", device_minutes=10)
    assert ledger.by_category()["generation"] == pytest.approx(0.02)
    assert ledger.by_category()["validation"] == pytest.approx(1.0)
    assert ledger.total == pytest.approx(1.02)


def test_cost_per_artifact() -> None:
    ledger = CostLedger()
    ledger.record_generation("t1", 1000, 1000)
    ledger.record_generation("t2", 1000, 1000)
    assert ledger.cost_per_artifact(["t1", "t2"]) is not None
    assert ledger.cost_per_artifact([]) is None


def test_unit_economics_flags_its_own_assumption() -> None:
    """hours-saved is an assumption, not a measurement, and says so in the output."""
    ledger = CostLedger()
    ledger.record_mining(terabytes_scanned=1.0)
    economics = ledger.unit_economics(merged_tests=10)
    assert economics["net"] > 0
    assert "assumption" in economics["caveat"]
    assert economics["assumed_hours_saved_per_test"] == 3.0


def test_unit_economics_handles_zero_merged_tests() -> None:
    assert CostLedger().unit_economics(merged_tests=0)["cost_per_merged_test"] is None
