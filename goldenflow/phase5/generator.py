"""Test generation - rendering a spec as code in the repo's own idiom.

Two generators behind one protocol:

* :class:`TemplateGenerator` - deterministic, offline, no API key. Renders the spec
  using the repo's existing Page Objects and conventions. This is the default and it
  ships working.
* :class:`LlmGenerator` - wraps a caller-supplied completion function. **No provider
  is embedded and no API call is faked.** It receives the same spec plus real
  neighbouring test files as few-shot examples, so output matches the repo's helpers
  and fixtures rather than generic Appium.

The division of labour is deliberate and load-bearing. The spec decides *what* the
test does - every screen, locator and assertion is already fixed by
:mod:`goldenflow.phase5.spec` from production data. The generator decides only *how
it reads*. A model asked to choose coverage invents plausible journeys; a model asked
to render a fixed spec fails at syntax instead, which the validation gate catches.

Generated code always carries its provenance in the docstring. A reviewer who cannot
tell where a test came from cannot review it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, Sequence, runtime_checkable

from goldenflow.phase3.models import Language, TestSuite
from goldenflow.phase5.spec import StepSpec, TestSpec

MAX_FEWSHOT_CHARS = 6000


@dataclass
class GeneratedTest:
    """One rendered test, before validation."""

    spec: TestSpec
    code: str
    language: Language = Language.PYTHON
    imports: list[str] = field(default_factory=list)
    generator: str = "template"

    @property
    def test_name(self) -> str:
        return self.spec.test_name

    @property
    def line_count(self) -> int:
        return len(self.code.splitlines())

    def compiles(self) -> tuple[bool, str]:
        """Cheapest possible gate: does it parse at all?

        Only meaningful for Python. Other languages are compiled by the validation
        harness on the device farm, where a real toolchain exists - claiming to
        syntax-check Java here would be a lie.
        """
        if self.language is not Language.PYTHON:
            return True, "not checked here; compiled by the validation harness"
        try:
            compile(self.code, f"<{self.test_name}>", "exec")
            return True, ""
        except SyntaxError as exc:
            return False, f"line {exc.lineno}: {exc.msg}"


@runtime_checkable
class Generator(Protocol):
    def generate(self, spec: TestSpec) -> GeneratedTest: ...


class RepoConventions:
    """What the existing suite already does, learned from the parsed suite.

    Generating a test that imports a fixture the repo does not have, or names a
    driver argument nobody else uses, produces code that is technically correct and
    fails review. Conventions are read from the suite rather than assumed.
    """

    def __init__(self, suite: TestSuite | None = None) -> None:
        self.suite = suite
        self.driver_fixture = "driver"
        self.import_root = "pages"
        self.page_object_module: dict[str, str] = {}
        self.example_files: list[str] = []
        if suite is not None:
            self._learn(suite)

    def _learn(self, suite: TestSuite) -> None:
        for page_object in suite.page_objects:
            if page_object.is_bound and page_object.language is Language.PYTHON:
                module = Path(page_object.file_path).stem
                parent = Path(page_object.file_path).parent.name
                self.page_object_module[page_object.class_name] = (
                    f"{parent}.{module}" if parent else module
                )

        python_tests = [t for t in suite.tests if t.language is Language.PYTHON]
        if python_tests:
            counts: dict[str, int] = {}
            for test in python_tests:
                for path in {t.file_path for t in python_tests}:
                    counts[path] = counts.get(path, 0) + 1
            self.example_files = sorted(counts, key=lambda p: -counts[p])[:2]

    def import_line_for(self, page_objects: Sequence[str]) -> list[str]:
        by_module: dict[str, list[str]] = {}
        for name in page_objects:
            module = self.page_object_module.get(name, f"{self.import_root}.pages")
            by_module.setdefault(module, []).append(name)
        return [
            f"from {module} import {', '.join(sorted(names))}"
            for module, names in sorted(by_module.items())
        ]

    def few_shot(self) -> str:
        """Real neighbouring tests, for the LLM to imitate."""
        chunks: list[str] = []
        budget = MAX_FEWSHOT_CHARS
        for path in self.example_files:
            try:
                text = Path(path).read_text(encoding="utf-8")
            except OSError:
                continue
            snippet = text[:budget]
            chunks.append(f"# --- {Path(path).name} ---\n{snippet}")
            budget -= len(snippet)
            if budget <= 0:
                break
        return "\n\n".join(chunks)


def _method_call(step: StepSpec, conventions: RepoConventions) -> list[str]:
    """Render the interaction and assertion lines for one step.

    Assertions must be able to fail. ``assert page is not None`` cannot -
    instantiating a Page Object always returns an object - so it is never emitted.
    In descending order of strength:

    1. a Page Object getter/predicate, asserted on its return value
    2. a resolved locator, asserted via ``is_displayed()``
    3. no assertion at all, with a comment saying why

    Option 3 is deliberately preferred over a vacuous assertion. A reviewer seeing
    ``# no assertable state exposed`` will add one; a reviewer seeing
    ``assert x is not None`` will assume the screen is checked.
    """
    lines: list[str] = []
    variable = re.sub(r"[^a-z0-9]+", "_", step.screen_id.lower()).strip("_")

    if step.page_object:
        lines.append(f"    {variable} = {step.page_object}({conventions.driver_fixture})")

        action = step.action_method
        if action:
            lines.append(f"    {variable}.{action}()")

        if not step.assertions:
            return lines

        checker = step.assertion_method
        if checker:
            for assertion in step.assertions:
                if checker.startswith(("is_", "has_", "can_")):
                    lines.append(
                        f"    assert {variable}.{checker}(), \"{assertion}\""
                    )
                elif checker.endswith("_count"):
                    # >= 0 is nearly vacuous on its own; it does exercise the call,
                    # which fails if the element is missing. GoldenFlow cannot know
                    # the expected count, so it says so rather than inventing one.
                    lines.append(
                        f"    # TODO tighten: GoldenFlow cannot infer the expected "
                        f"count from telemetry"
                    )
                    lines.append(
                        f"    assert {variable}.{checker}() >= 0, \"{assertion}\""
                    )
                else:
                    lines.append(
                        f"    assert {variable}.{checker}() is not None, "
                        f"\"{assertion}\""
                    )
            return lines

        locator = step.primary_locator
        if locator is not None:
            lines.append(
                f"    assert {conventions.driver_fixture}.find_element("
                f"{locator.strategy.appium_by}, \"{locator.value}\").is_displayed(), "
                f"\\\n        \"{step.assertions[0]}\""
            )
        else:
            lines.append(
                f"    # TODO no assertable state exposed by {step.page_object}; "
                f"add a getter or an accessibility ID"
            )
        return lines

    locator = step.primary_locator
    if locator is None:
        lines.append(f"    # UNRESOLVED: no locator for {step.screen_id}")
        return lines

    lines.append(
        f"    {variable} = {conventions.driver_fixture}.find_element("
        f"{locator.strategy.appium_by}, \"{locator.value}\")"
    )
    for assertion in step.assertions:
        lines.append(f"    assert {variable}.is_displayed(), \"{assertion}\"")
    return lines


class TemplateGenerator:
    """Deterministic generation. No model, no network, no API key."""

    def __init__(self, conventions: RepoConventions | None = None) -> None:
        self.conventions = conventions or RepoConventions()

    def generate(self, spec: TestSpec) -> GeneratedTest:  # noqa: C901
        conventions = self.conventions

        body: list[str] = []
        for step in spec.steps:
            # Duplicate assertions read as sloppy and add nothing: the last step
            # collects both its critical-screen check and the journey-completion
            # check, which resolve to the same call.
            step.assertions = list(dict.fromkeys(step.assertions))
            body.append(f"    # {step.display_name}")
            body.extend(_method_call(step, conventions))
            body.append("")

        rendered = "\n".join(body)
        imports = ["import pytest"]
        if "AppiumBy." in rendered:
            # compile() checks syntax, not names - a missing import here passes the
            # cheap gate and NameErrors on the device. Cheaper to get right than to
            # discover on a device-farm minute.
            imports.append("from appium.webdriver.common.appiumby import AppiumBy")
        imports += conventions.import_line_for(spec.page_objects)

        docstring = (spec.provenance.as_docstring() if spec.provenance
                     else spec.test_name)
        marks = "\n".join(f"@pytest.mark.{tag}" for tag in spec.tags)

        code = "\n".join([
            *imports,
            "",
            "",
            marks,
            f"def {spec.test_name}({conventions.driver_fixture}):",
            '    """',
            *[f"    {line}" for line in docstring.splitlines()],
            '    """',
            *body,
        ]).rstrip() + "\n"

        return GeneratedTest(spec=spec, code=code, imports=imports,
                             generator="template")


class LlmGenerator:
    """Generation via a caller-supplied completion function.

    Args:
        complete: Any ``Callable[[str], str]``. Wire it to the Claude API or
            anything else - GoldenFlow does not ship a client and does not choose
            a provider for you.
        conventions: Repo conventions, used to build few-shot examples.
        fallback: Used whenever the model errors or returns code that does not
            parse. Defaults to :class:`TemplateGenerator`, so a model outage
            degrades output quality rather than stopping the pipeline.
    """

    def __init__(
        self,
        complete: Callable[[str], str],
        *,
        conventions: RepoConventions | None = None,
        fallback: Generator | None = None,
        model: str = "unspecified",
    ) -> None:
        self.complete = complete
        self.conventions = conventions or RepoConventions()
        self.fallback = fallback or TemplateGenerator(self.conventions)
        self.model = model

    def build_prompt(self, spec: TestSpec) -> str:
        """The exact prompt sent. Exposed so it can be reviewed and version-pinned."""
        import json

        steps = [
            {
                "screen": step.screen_id,
                "display_name": step.display_name,
                "page_object": step.page_object,
                "locators": {
                    role: {"by": loc.strategy.appium_by, "value": loc.value}
                    for role, loc in step.locators.items()
                },
                "assertions": step.assertions,
                "critical": step.critical,
            }
            for step in spec.steps
        ]
        examples = self.conventions.few_shot()
        return (
            "Write ONE Appium pytest test from the specification below.\n\n"
            "Rules, in priority order:\n"
            "1. Do not add, remove or reorder steps. The sequence comes from a "
            "deterministic analysis of production telemetry and is authoritative.\n"
            "2. Use ONLY the locators given. Do not invent selectors, and never "
            "fall back to XPath by index.\n"
            "3. Match the conventions of the example tests: same fixtures, same "
            "imports, same helper style.\n"
            "4. Keep the docstring exactly as provided - it carries provenance a "
            "reviewer needs.\n"
            "5. Return ONLY Python code. No prose, no markdown fences.\n\n"
            f"Test name: {spec.test_name}\n"
            f"Docstring:\n{spec.provenance.as_docstring() if spec.provenance else ''}\n\n"
            f"Steps:\n{json.dumps(steps, indent=2)}\n\n"
            + (f"Example tests from this repository:\n{examples}\n" if examples else "")
        )

    def generate(self, spec: TestSpec) -> GeneratedTest:
        try:
            raw = self.complete(self.build_prompt(spec))
            code = _strip_fences(raw)
            candidate = GeneratedTest(spec=spec, code=code, generator=f"llm:{self.model}")
            ok, _ = candidate.compiles()
            if not ok or not code.strip():
                raise ValueError("model output did not parse")
            if spec.provenance is not None:
                candidate.spec.provenance.model = self.model
            return candidate
        except Exception:
            # Generation quality is negotiable; pipeline availability is not.
            return self.fallback.generate(spec)


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        body = lines[1:-1] if len(lines) > 2 and lines[-1].strip() == "```" else lines[1:]
        return "\n".join(body)
    return stripped


def generate_all(
    specs: Sequence[TestSpec], generator: Generator | None = None
) -> list[GeneratedTest]:
    """Render every executable spec. Unresolvable specs are skipped, not faked.

    A spec with an unresolved step cannot become a runnable test, and emitting one
    with a hole in the middle would waste a reviewer's time and burn trust that
    Phase 5 has to earn.
    """
    generator = generator or TemplateGenerator()
    return [generator.generate(spec) for spec in specs if spec.executable]
