"""Validation - nothing reaches a reviewer until it has run green on a real device.

This is the rule the whole phase rests on. Without it, GoldenFlow is a machine that
opens pull requests full of plausible-looking code, and a QE team that merges two
broken generated tests will never trust the third.

    generate -> compile -> run on device farm -> repeat N times across M profiles
             -> assert deterministic -> only then open a PR

**The device farm is an interface, not an implementation.** No BrowserStack or Test
Lab client ships here, because there is no device farm in this environment and
pretending otherwise would produce a green report that means nothing. The seam is
:class:`TestRunner`; wire a real farm to it and the gate works unchanged.

:class:`CompileOnlyRunner` is the honest default: it reports what it actually did -
a syntax check - and :class:`ValidationGate` refuses to certify anything on that
basis alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, Sequence, runtime_checkable

from goldenflow.phase5.generator import GeneratedTest

DEFAULT_RUNS = 10
DEFAULT_PROFILES = ("pixel_7_android_14", "galaxy_s22_android_13", "iphone_14_ios_17")
MIN_PROFILES = 3


class Verdict(str, Enum):
    VALIDATED = "validated"
    """Ran green, every run, every profile. Eligible for a PR."""

    FAILED = "failed"
    """Ran red at least once. Never becomes a PR."""

    FLAKY = "flaky"
    """Passed sometimes. Quarantined - a flaky generated test is worse than none,
    because it teaches the team to ignore failures."""

    UNVALIDATED = "unvalidated"
    """No real execution happened. Explicitly not a pass."""


@dataclass
class RunResult:
    """One execution of one test on one device profile."""

    test_name: str
    profile: str
    passed: bool
    duration_s: float = 0.0
    output: str = ""
    error: str = ""
    executed: bool = False
    """False when nothing actually ran - a syntax check must never masquerade as a
    device run."""


@runtime_checkable
class TestRunner(Protocol):
    """The device-farm seam."""

    name: str

    def run(self, test: GeneratedTest, profile: str) -> RunResult: ...


class CompileOnlyRunner:
    """Parses the generated code. Does not execute it, and says so.

    Useful as a cheap pre-filter - there is no point spending device minutes on code
    that does not parse - but it sets ``executed=False`` so the gate cannot mistake
    it for evidence.
    """

    name = "compile-only"

    def run(self, test: GeneratedTest, profile: str) -> RunResult:
        ok, detail = test.compiles()
        return RunResult(
            test_name=test.test_name, profile=profile, passed=ok,
            output="syntax check only; no device involved",
            error="" if ok else f"does not compile: {detail}",
            executed=False,
        )


class DeviceFarmRunner:
    """Adapter for a real device farm. Deliberately unimplemented.

    Implement :meth:`run` against BrowserStack App Automate, Firebase Test Lab or a
    self-hosted Appium grid. The contract is: upload the build, run this one test on
    ``profile``, return whether it passed and how long it took.

    It raises rather than returning a default, because a runner that silently
    reports success is the single most dangerous thing this codebase could contain.
    """

    name = "device-farm"

    def __init__(self, endpoint: str = "", credentials: Any = None) -> None:
        self.endpoint = endpoint
        self.credentials = credentials

    def run(self, test: GeneratedTest, profile: str) -> RunResult:
        raise NotImplementedError(
            "No device farm is wired up. Implement DeviceFarmRunner.run against "
            "BrowserStack App Automate, Firebase Test Lab, or an Appium grid. "
            "Until then ValidationGate will report UNVALIDATED, which is the "
            "correct answer - not a failure to work around."
        )


@dataclass
class ValidationResult:
    """The full evidence for one test."""

    test: GeneratedTest
    runs: list[RunResult] = field(default_factory=list)
    verdict: Verdict = Verdict.UNVALIDATED
    reason: str = ""

    @property
    def profiles(self) -> set[str]:
        return {r.profile for r in self.runs}

    @property
    def executed_runs(self) -> list[RunResult]:
        return [r for r in self.runs if r.executed]

    @property
    def pass_rate(self) -> float:
        executed = self.executed_runs
        if not executed:
            return 0.0
        return round(100.0 * sum(1 for r in executed if r.passed) / len(executed), 1)

    @property
    def mean_duration(self) -> float:
        executed = self.executed_runs
        if not executed:
            return 0.0
        return round(sum(r.duration_s for r in executed) / len(executed), 2)

    @property
    def eligible_for_pr(self) -> bool:
        return self.verdict is Verdict.VALIDATED

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_name": self.test.test_name,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "runs": len(self.runs),
            "executed_runs": len(self.executed_runs),
            "profiles": sorted(self.profiles),
            "pass_rate": self.pass_rate,
            "mean_duration_s": self.mean_duration,
            "eligible_for_pr": self.eligible_for_pr,
        }

    def format(self) -> str:
        mark = {
            Verdict.VALIDATED: "PASS", Verdict.FAILED: "FAIL",
            Verdict.FLAKY: "FLAKY", Verdict.UNVALIDATED: "----",
        }[self.verdict]
        return (f"  [{mark:5}] {self.test.test_name:<44} "
                f"{len(self.executed_runs)} run(s), {len(self.profiles)} profile(s)"
                + (f"  {self.reason}" if self.reason else ""))


@dataclass
class ValidationReport:
    results: list[ValidationResult] = field(default_factory=list)
    runner: str = ""
    runs_per_profile: int = DEFAULT_RUNS
    profiles: tuple[str, ...] = DEFAULT_PROFILES

    def of_verdict(self, verdict: Verdict) -> list[ValidationResult]:
        return [r for r in self.results if r.verdict is verdict]

    @property
    def validated(self) -> list[ValidationResult]:
        return self.of_verdict(Verdict.VALIDATED)

    @property
    def first_attempt_pass_rate(self) -> float:
        """Share of generated tests that validated without human intervention.

        The Phase 5 exit criterion, and the number that says whether generation is
        actually working.
        """
        if not self.results:
            return 0.0
        return round(100.0 * len(self.validated) / len(self.results), 1)

    def summary(self) -> dict[str, Any]:
        return {
            "runner": self.runner,
            "generated": len(self.results),
            "validated": len(self.validated),
            "failed": len(self.of_verdict(Verdict.FAILED)),
            "flaky": len(self.of_verdict(Verdict.FLAKY)),
            "unvalidated": len(self.of_verdict(Verdict.UNVALIDATED)),
            "first_attempt_pass_rate": self.first_attempt_pass_rate,
            "runs_per_profile": self.runs_per_profile,
            "profiles": list(self.profiles),
        }

    def format(self) -> str:
        summary = self.summary()
        lines = [
            "Validation",
            "=" * 76,
            f"  runner            : {self.runner}",
            f"  policy            : {self.runs_per_profile} run(s) x "
            f"{len(self.profiles)} profile(s), all must pass",
            f"  generated         : {summary['generated']}",
            f"  validated         : {summary['validated']} "
            f"({summary['first_attempt_pass_rate']}% first attempt)",
            f"  failed            : {summary['failed']}",
            f"  flaky (quarantine): {summary['flaky']}",
            f"  unvalidated       : {summary['unvalidated']}",
            "=" * 76,
        ]
        lines += [r.format() for r in self.results]
        if summary["unvalidated"]:
            lines += [
                "",
                "  UNVALIDATED is not a pass. No device farm executed these, so",
                "  nothing here is eligible for a pull request.",
            ]
        return "\n".join(lines)


class ValidationGate:
    """Runs the policy: N runs across M profiles, all green, or no PR."""

    def __init__(
        self,
        runner: TestRunner | None = None,
        *,
        runs_per_profile: int = DEFAULT_RUNS,
        profiles: Sequence[str] = DEFAULT_PROFILES,
        min_profiles: int = MIN_PROFILES,
    ) -> None:
        self.runner = runner or CompileOnlyRunner()
        self.runs_per_profile = runs_per_profile
        self.profiles = tuple(profiles)
        self.min_profiles = min_profiles

    def validate(self, test: GeneratedTest) -> ValidationResult:
        result = ValidationResult(test=test)

        compiled, detail = test.compiles()
        if not compiled:
            result.verdict = Verdict.FAILED
            result.reason = f"does not compile ({detail})"
            return result

        for profile in self.profiles:
            for _ in range(self.runs_per_profile):
                try:
                    result.runs.append(self.runner.run(test, profile))
                except NotImplementedError as exc:
                    result.verdict = Verdict.UNVALIDATED
                    result.reason = str(exc).split(".")[0]
                    return result

        executed = result.executed_runs
        if not executed:
            result.verdict = Verdict.UNVALIDATED
            result.reason = (
                f"{self.runner.name} did not execute anything on a device"
            )
            return result

        if len(result.profiles) < self.min_profiles:
            result.verdict = Verdict.UNVALIDATED
            result.reason = (
                f"only {len(result.profiles)} profile(s); policy requires "
                f"{self.min_profiles}"
            )
            return result

        passed = sum(1 for r in executed if r.passed)
        if passed == len(executed):
            result.verdict = Verdict.VALIDATED
            result.reason = f"{passed}/{len(executed)} green"
        elif passed == 0:
            result.verdict = Verdict.FAILED
            result.reason = next(
                (r.error for r in executed if r.error), "failed every run"
            )
        else:
            # Intermittent is the worst outcome: it merges, then erodes trust in
            # every red build that follows.
            result.verdict = Verdict.FLAKY
            result.reason = (
                f"non-deterministic: {passed}/{len(executed)} passed. Quarantined."
            )
        return result

    def validate_all(self, tests: Sequence[GeneratedTest]) -> ValidationReport:
        report = ValidationReport(
            runner=self.runner.name,
            runs_per_profile=self.runs_per_profile,
            profiles=self.profiles,
        )
        report.results = [self.validate(test) for test in tests]
        return report
