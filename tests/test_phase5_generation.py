"""Spec building, generation, validation and PR proposal tests.

The load-bearing tests here are the ones that prove the gate cannot be bypassed:
an unvalidated test must never reach a PR, and a flaky one must never be merged.
"""

from __future__ import annotations

import json

import pytest

from goldenflow.phase0.registry import ProtectedTestRegistry
from goldenflow.phase0.taxonomy import Taxonomy
from goldenflow.phase2.clustering import cluster_variants
from goldenflow.phase2.mining import Variant
from goldenflow.phase2.naming import RuleBasedNamer, name_journeys
from goldenflow.phase2.scoring import score_journeys
from goldenflow.phase3.models import Language
from goldenflow.phase3.parser_text import parse_repository
from goldenflow.phase4.hierarchy import load_dump_directory
from goldenflow.phase4.locators import Locator, Strategy
from goldenflow.phase4.resolver import from_dumps, from_page_objects, merge
from goldenflow.phase5.generator import (
    GeneratedTest,
    LlmGenerator,
    RepoConventions,
    TemplateGenerator,
    generate_all,
)
from goldenflow.phase5.pull_request import (
    ChangeKind,
    build_pull_request,
    deletion_pull_request,
    propose_deletions,
)
from goldenflow.phase5.spec import StepSpec, TestSpec, build_spec
from goldenflow.phase5.validation import (
    CompileOnlyRunner,
    DeviceFarmRunner,
    RunResult,
    ValidationGate,
    ValidationResult,
    Verdict,
)

TAXONOMY = Taxonomy.from_yaml("config/taxonomy.yaml")
REGISTRY = ProtectedTestRegistry.from_yaml("config/protected-tests.yaml")


class ScriptedRunner:
    """A runner with dictated outcomes, for exercising the gate's branches.

    Lives in the test suite, not in product code. A configurable "device runner"
    that returns success without touching a device is the single most dangerous
    thing this codebase could ship.
    """

    name = "scripted"

    def __init__(self, outcomes: list[bool]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def run(self, test: GeneratedTest, profile: str) -> RunResult:
        passed = self.outcomes[self.calls % len(self.outcomes)]
        self.calls += 1
        return RunResult(
            test_name=test.test_name, profile=profile, passed=passed,
            duration_s=12.5, executed=True,
            error="" if passed else "element not found: cart_checkout",
        )


@pytest.fixture(scope="module")
def suite():
    return parse_repository(TAXONOMY, "fixtures/appium-suite")


@pytest.fixture(scope="module")
def screens(suite):
    dumps = load_dump_directory("fixtures/ui-dumps/v8.2.0", app_version="8.2.0")
    return merge(from_page_objects(suite, TAXONOMY), from_dumps(dumps))


def journey(*path: str, count: int = 100):
    clustering = cluster_variants([Variant(sequence=tuple(path), count=count)])
    journeys = score_journeys(clustering, taxonomy=TAXONOMY)
    name_journeys(journeys, RuleBasedNamer(TAXONOMY))
    return journeys[0]


def spec_for(*path: str, suite=None, screens=None, count: int = 100) -> TestSpec:
    page_object_for = {}
    page_object_methods = {}
    if suite is not None:
        page_object_for = {p.screen_id: p.class_name for p in suite.page_objects
                           if p.is_bound}
        page_object_methods = {p.class_name: p.methods for p in suite.page_objects
                               if p.is_bound}
    return build_spec(
        journey(*path, count=count), screens or {}, taxonomy=TAXONOMY,
        page_object_for=page_object_for, page_object_methods=page_object_methods,
    )


def fake_test(name: str = "test_x", code: str = "def test_x():\n    assert True\n"):
    return GeneratedTest(spec=TestSpec(test_name=name), code=code)


# ==================================================================== specs


def test_spec_carries_provenance(screens) -> None:
    """A generated test whose origin cannot be reconstructed is not reviewable."""
    spec = spec_for("cart", "payment_method", screens=screens)
    assert spec.provenance is not None
    assert spec.provenance.sessions == 100
    assert spec.provenance.journey_id
    docstring = spec.provenance.as_docstring()
    assert "sessions" in docstring and "score" in docstring


def test_spec_is_not_executable_when_a_step_is_unresolved(screens) -> None:
    """Phase 5 cannot generate a test that navigates eight screens and guesses at
    the ninth."""
    spec = spec_for("cart", "help_center", screens=screens)
    assert not spec.executable
    assert spec.unresolvable_steps == ["help_center"]


def test_critical_screens_get_assertions(screens, suite) -> None:
    spec = spec_for("home", "payment_method", suite=suite, screens=screens)
    payment = next(s for s in spec.steps if s.screen_id == "payment_method")
    assert payment.critical and payment.assertions


def test_assertion_method_prefers_a_real_getter(suite, screens) -> None:
    spec = spec_for("cart", suite=suite, screens=screens, count=10)
    cart = spec.steps[0]
    assert cart.assertion_method == "item_count"
    assert cart.action_method not in (None, "item_count")


def test_terminal_assertion_is_not_duplicated(suite, screens) -> None:
    """Two identical asserts with different messages read as sloppy and check
    nothing extra."""
    spec = spec_for("home", "order_confirmation", suite=suite, screens=screens)
    assert len(spec.steps[-1].assertions) == 1


# =============================================================== generation


def test_generated_code_compiles(suite, screens) -> None:
    spec = spec_for("cart", "payment_method", suite=suite, screens=screens)
    test = TemplateGenerator(RepoConventions(suite)).generate(spec)
    ok, detail = test.compiles()
    assert ok, detail


def test_generated_code_never_asserts_is_not_none_on_a_page_object(suite, screens) -> None:
    """`assert page is not None` cannot fail - instantiating a Page Object always
    returns an object. It is exactly the vacuous coverage Phase 3 flags."""
    spec = spec_for("cart", "payment_method", "order_review", suite=suite,
                    screens=screens)
    code = TemplateGenerator(RepoConventions(suite)).generate(spec).code
    for line in code.splitlines():
        if line.strip().startswith("assert "):
            variable = line.strip().split()[1].split(".")[0].rstrip(",")
            assert f"assert {variable} is not None" not in line, (
                f"vacuous assertion: {line.strip()}"
            )


def test_appium_by_is_imported_when_used(suite, screens) -> None:
    """compile() checks syntax, not names - a missing import passes the cheap gate
    and NameErrors on a device-farm minute."""
    spec = spec_for("cart", "delivery_address", suite=suite, screens=screens)
    code = TemplateGenerator(RepoConventions(suite)).generate(spec).code
    if "AppiumBy." in code:
        assert "from appium.webdriver.common.appiumby import AppiumBy" in code


def test_provenance_appears_in_the_docstring(suite, screens) -> None:
    spec = spec_for("cart", "payment_method", suite=suite, screens=screens)
    code = TemplateGenerator(RepoConventions(suite)).generate(spec).code
    assert "Generated by GoldenFlow" in code
    assert "sessions" in code


def test_repo_conventions_are_learned_from_the_suite(suite) -> None:
    conventions = RepoConventions(suite)
    assert conventions.page_object_module.get("CartScreen", "").endswith(
        "checkout_pages")
    lines = conventions.import_line_for(["CartScreen", "HomeScreen"])
    assert any("checkout_pages import CartScreen" in line for line in lines)


def test_unresolvable_specs_are_skipped_not_faked(suite, screens) -> None:
    good = spec_for("cart", "payment_method", suite=suite, screens=screens)
    bad = spec_for("cart", "help_center", suite=suite, screens=screens)
    tests = generate_all([good, bad], TemplateGenerator(RepoConventions(suite)))
    assert len(tests) == 1


def test_llm_generator_falls_back_when_output_does_not_parse(suite, screens) -> None:
    """Generation quality is negotiable; pipeline availability is not."""
    spec = spec_for("cart", suite=suite, screens=screens)
    generator = LlmGenerator(lambda _p: "this is not python (((",
                             conventions=RepoConventions(suite))
    test = generator.generate(spec)
    assert test.compiles()[0]
    assert test.generator == "template"


def test_llm_generator_falls_back_when_the_model_raises(suite, screens) -> None:
    def boom(_prompt: str) -> str:
        raise RuntimeError("provider 503")
    spec = spec_for("cart", suite=suite, screens=screens)
    assert LlmGenerator(boom, conventions=RepoConventions(suite)).generate(spec).code


def test_llm_prompt_forbids_changing_the_journey(suite, screens) -> None:
    """The spec decides what is tested. The model decides only how it reads."""
    spec = spec_for("cart", "payment_method", suite=suite, screens=screens)
    prompt = LlmGenerator(lambda _p: "", conventions=RepoConventions(suite)).build_prompt(spec)
    assert "Do not add, remove or reorder steps" in prompt
    assert "Use ONLY the locators given" in prompt
    assert "never" in prompt and "XPath by index" in prompt


# =============================================================== validation


def test_compile_only_runner_never_claims_execution() -> None:
    """A syntax check must not masquerade as a device run."""
    result = CompileOnlyRunner().run(fake_test(), "pixel_7")
    assert result.passed and not result.executed


def test_gate_reports_unvalidated_without_a_real_runner() -> None:
    report = ValidationGate(runs_per_profile=2).validate_all([fake_test()])
    assert report.results[0].verdict is Verdict.UNVALIDATED
    assert not report.results[0].eligible_for_pr


def test_device_farm_runner_raises_rather_than_defaulting() -> None:
    """A runner that silently reports success is the most dangerous thing this
    codebase could contain."""
    with pytest.raises(NotImplementedError, match="No device farm"):
        DeviceFarmRunner().run(fake_test(), "pixel_7")


def test_gate_surfaces_the_missing_farm_rather_than_crashing() -> None:
    result = ValidationGate(DeviceFarmRunner(), runs_per_profile=2).validate(fake_test())
    assert result.verdict is Verdict.UNVALIDATED


def test_all_green_validates() -> None:
    gate = ValidationGate(ScriptedRunner([True]), runs_per_profile=5)
    result = gate.validate(fake_test())
    assert result.verdict is Verdict.VALIDATED
    assert result.pass_rate == 100.0
    assert len(result.profiles) == 3
    assert len(result.executed_runs) == 15


def test_all_red_fails_and_reports_the_error() -> None:
    result = ValidationGate(ScriptedRunner([False]), runs_per_profile=3).validate(
        fake_test())
    assert result.verdict is Verdict.FAILED
    assert "element not found" in result.reason


def test_intermittent_is_quarantined_as_flaky() -> None:
    """A flaky generated test is worse than none - it teaches the team to ignore
    failures."""
    result = ValidationGate(ScriptedRunner([True, True, False]),
                            runs_per_profile=3).validate(fake_test())
    assert result.verdict is Verdict.FLAKY
    assert "non-deterministic" in result.reason
    assert not result.eligible_for_pr


def test_non_compiling_code_never_reaches_a_device() -> None:
    runner = ScriptedRunner([True])
    result = ValidationGate(runner, runs_per_profile=3).validate(
        fake_test(code="def broken(:\n"))
    assert result.verdict is Verdict.FAILED
    assert runner.calls == 0, "device minutes must not be spent on code that fails to parse"


def test_too_few_profiles_is_not_a_pass() -> None:
    gate = ValidationGate(ScriptedRunner([True]), runs_per_profile=5,
                          profiles=("pixel_7",))
    assert gate.validate(fake_test()).verdict is Verdict.UNVALIDATED


def test_first_attempt_pass_rate_is_reported() -> None:
    gate = ValidationGate(ScriptedRunner([True]), runs_per_profile=2)
    report = gate.validate_all([fake_test("a"), fake_test("b")])
    assert report.first_attempt_pass_rate == 100.0


# ================================================================ pull request


def test_only_validated_tests_reach_a_pull_request() -> None:
    """There must be no code path in which an unvalidated test reaches a reviewer."""
    validated = ValidationResult(test=fake_test("test_good"), verdict=Verdict.VALIDATED)
    for rejected in (Verdict.FAILED, Verdict.FLAKY, Verdict.UNVALIDATED):
        pr = build_pull_request([
            validated,
            ValidationResult(test=fake_test("test_bad"), verdict=rejected),
        ])
        names = {c.test_name for c in pr.changes}
        assert names == {"test_good"}, f"{rejected.value} leaked into the PR"


def test_pr_body_carries_the_evidence_a_reviewer_needs(suite, screens) -> None:
    spec = spec_for("cart", "payment_method", suite=suite, screens=screens)
    test = TemplateGenerator(RepoConventions(suite)).generate(spec)
    result = ValidationResult(test=test, verdict=Verdict.VALIDATED,
                              runs=[RunResult("t", "pixel_7", True, executed=True)])
    body = build_pull_request([result]).body()
    assert "Production traffic" in body
    assert "Journey score" in body
    assert "device profiles" in body
    assert "Review checklist" in body


def test_pr_payload_shape(tmp_path) -> None:
    pr = build_pull_request([
        ValidationResult(test=fake_test("test_a"), verdict=Verdict.VALIDATED)
    ])
    path = pr.write(tmp_path / "pr.json", repo="acme/mobile")
    payload = json.loads(path.read_text())
    assert payload["head"] == "goldenflow/generated-tests"
    assert payload["base"] == "main"
    assert payload["files"][0]["operation"] == "add"
    assert "goldenflow" in payload["labels"]


def test_empty_pr_when_nothing_validated() -> None:
    pr = build_pull_request([
        ValidationResult(test=fake_test(), verdict=Verdict.UNVALIDATED)
    ])
    assert pr.is_empty


# ================================================== dual-signal deletion rule


def test_one_signal_is_not_enough_to_propose_deletion() -> None:
    """A path can be absent from a sample for innocent reasons: seasonality, a flag
    rollout, or simply being rare."""
    proposals = propose_deletions(["tests/x/test_a.py::t"],
                                  zero_traffic=["tests/x/test_a.py::t"])
    assert not proposals[0].proposable
    assert "only 1 signal" in proposals[0].rationale()


def test_two_independent_signals_permit_a_proposal() -> None:
    proposals = propose_deletions(
        ["tests/legacy_promo/test_a.py::t"],
        zero_traffic=["tests/legacy_promo/test_a.py::t"],
        removed_screens=["legacy_promo"],
    )
    assert proposals[0].proposable
    assert len(proposals[0].signals) == 2


def test_retired_flag_counts_as_a_second_signal() -> None:
    proposals = propose_deletions(
        ["tests/test_beta_wallet.py::t"],
        zero_traffic=["tests/test_beta_wallet.py::t"],
        retired_flags=["beta_wallet"],
    )
    assert proposals[0].proposable


def test_protected_registry_overrides_both_signals() -> None:
    """Whatever the signals say. This is the last gate before deletion."""
    test_id = "tests/a11y/test_talkback.py::t"
    proposals = propose_deletions(
        [test_id], registry=REGISTRY,
        zero_traffic=[test_id], removed_screens=["a11y"],
    )
    assert len(proposals[0].signals) == 2, "both signals fired"
    assert not proposals[0].proposable, "and it is still blocked"
    assert "protected under accessibility" in proposals[0].rationale()


def test_deletion_pr_contains_only_cleared_candidates() -> None:
    proposals = propose_deletions(
        ["tests/legacy/test_a.py::t", "tests/a11y/test_b.py::t"],
        registry=REGISTRY,
        zero_traffic=["tests/legacy/test_a.py::t", "tests/a11y/test_b.py::t"],
        removed_screens=["legacy", "a11y"],
    )
    pr = deletion_pull_request(proposals)
    assert [c.test_name for c in pr.changes] == ["tests/legacy/test_a.py::t"]
    assert pr.draft, "deletion PRs open as drafts"
    assert all(c.kind is ChangeKind.DELETE for c in pr.changes)
