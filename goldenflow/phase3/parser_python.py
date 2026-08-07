"""Python test suite parser, using the real AST.

pytest and unittest Appium suites are parsed properly rather than by regex: the
``ast`` module is in the standard library, so there is no excuse for guessing at
Python. That matters because the whole value of Phase 3 rests on knowing which
screens a test genuinely exercises.

Attribution rules, strongest first:

``HIGH``
    A Page Object class the taxonomy binds to a screen is instantiated, or a method
    is called on a variable holding one. The test demonstrably drives that screen.

``MEDIUM``
    A string literal matching a screen ID or analytics tag, or a navigation helper
    such as ``navigate_to("cart")``. Very likely, not provable.

``LOW``
    Inferred from the test's own name. Reported for human confirmation and
    deliberately excluded from coverage - a guess must never be able to make a path
    look tested.
"""

from __future__ import annotations

import ast
from pathlib import Path

from goldenflow.phase0.taxonomy import Taxonomy
from goldenflow.phase3.models import (
    Assertion,
    Confidence,
    Language,
    PageObject,
    ParsedTest,
    ParseIssue,
    ScreenReference,
    TestSuite,
    relative_id,
)

NAVIGATION_HELPERS = {"navigate_to", "goto", "go_to", "open_screen", "launch_screen"}
UNITTEST_ASSERTIONS = {
    "assertEqual", "assertNotEqual", "assertTrue", "assertFalse", "assertIn",
    "assertIsNone", "assertIsNotNone", "assertGreater", "assertLess",
    "assertAlmostEqual", "assertRaises", "assertRegex",
}
PAGE_OBJECT_SUFFIXES = ("Page", "Screen", "View")


class PythonSuiteParser:
    """Parses a directory of Python tests and Page Objects."""

    def __init__(self, taxonomy: Taxonomy, root: str | Path) -> None:
        self.taxonomy = taxonomy
        self.root = Path(root)
        self.page_object_to_screen: dict[str, str] = {
            page_object: screen.screen_id
            for screen in taxonomy.screens
            for page_object in screen.page_objects
        }
        self.tag_to_screen: dict[str, str] = {
            tag: screen.screen_id
            for screen in taxonomy.screens
            for tag in screen.analytics_tags
        }
        self.screen_ids = {s.screen_id for s in taxonomy.screens}

    # ------------------------------------------------------------- entrypoints

    def parse_directory(self, directory: str | Path) -> TestSuite:
        suite = TestSuite()
        for path in sorted(Path(directory).rglob("*.py")):
            if path.name.startswith("__"):
                continue
            suite = suite.merge(self.parse_file(path))
        return suite

    def parse_file(self, path: str | Path) -> TestSuite:
        path = Path(path)
        suite = TestSuite(files_scanned=1)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            suite.issues.append(ParseIssue(
                file_path=str(path), line=exc.lineno or 0,
                message=f"syntax error, file skipped: {exc.msg}",
            ))
            return suite

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and self._is_page_object(node):
                suite.page_objects.append(self._parse_page_object(node, path))

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                            and self._is_test(child):
                        suite.tests.append(
                            self._parse_test(child, path, class_name=node.name)
                        )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and self._is_test(node):
                suite.tests.append(self._parse_test(node, path))

        return suite

    # --------------------------------------------------------------- detection

    @staticmethod
    def _is_test(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        return node.name.startswith("test_") or node.name.startswith("test")

    def _is_page_object(self, node: ast.ClassDef) -> bool:
        return (
            node.name in self.page_object_to_screen
            or node.name.endswith(PAGE_OBJECT_SUFFIXES)
        )

    # ------------------------------------------------------------ page objects

    def _parse_page_object(self, node: ast.ClassDef, path: Path) -> PageObject:
        locators: dict[str, str] = {}
        methods: list[str] = []

        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not child.name.startswith("_"):
                    methods.append(child.name)
            elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                targets = (
                    child.targets if isinstance(child, ast.Assign) else [child.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        value = self._locator_value(child.value)
                        if value is not None:
                            locators[target.id] = value

        return PageObject(
            class_name=node.name,
            file_path=str(path),
            language=Language.PYTHON,
            screen_id=self.page_object_to_screen.get(node.name),
            locators=locators,
            methods=methods,
            line=node.lineno,
        )

    @staticmethod
    def _locator_value(value: ast.expr | None) -> str | None:
        """Read a locator from either ``"~cart"`` or ``(AppiumBy.ID, "com.x:id/y")``."""
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        if isinstance(value, ast.Tuple):
            for element in reversed(value.elts):
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    return element.value
        return None

    # ------------------------------------------------------------------- tests

    def _parse_test(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        path: Path,
        class_name: str | None = None,
    ) -> ParsedTest:
        qualified = f"{class_name}::{node.name}" if class_name else node.name
        test = ParsedTest(
            test_id=relative_id(path, self.root, qualified),
            name=node.name,
            file_path=str(path),
            language=Language.PYTHON,
            line=node.lineno,
        )
        test.tags, test.skipped = self._decorators(node)

        variables: dict[str, str] = {}   # local variable -> Page Object class
        order = 0

        def note(screen_id: str, confidence: Confidence, evidence: str, line: int) -> None:
            nonlocal order
            order += 1
            test.screens.append(ScreenReference(
                screen_id=screen_id, confidence=confidence,
                evidence=evidence, line=line, order=order,
            ))

        for child in ast.walk(node):
            # cart = CartPage(driver)
            if isinstance(child, ast.Assign) and isinstance(child.value, ast.Call):
                class_ref = self._called_name(child.value.func)
                if class_ref and class_ref.endswith(PAGE_OBJECT_SUFFIXES):
                    for target in child.targets:
                        if isinstance(target, ast.Name):
                            variables[target.id] = class_ref

            if isinstance(child, ast.Call):
                self._visit_call(child, test, variables, note)

            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                screen = self._screen_from_literal(child.value)
                if screen:
                    note(screen, Confidence.MEDIUM,
                         f"string literal {child.value!r}", child.lineno)

        self._collect_assertions(node, test)

        if not test.screens:
            inferred = self._screen_from_name(node.name)
            if inferred:
                note(inferred, Confidence.LOW,
                     f"inferred from test name {node.name!r}", node.lineno)

        return test

    def _visit_call(self, call: ast.Call, test: ParsedTest,
                    variables: dict[str, str], note) -> None:
        # CartPage(driver) - direct instantiation
        name = self._called_name(call.func)
        if name and name in self.page_object_to_screen:
            if name not in test.page_objects:
                test.page_objects.append(name)
            note(self.page_object_to_screen[name], Confidence.HIGH,
                 f"instantiates {name}", call.lineno)
            return

        if isinstance(call.func, ast.Attribute):
            # cart.tap_checkout() - method on a variable holding a Page Object
            if isinstance(call.func.value, ast.Name):
                class_ref = variables.get(call.func.value.id)
                screen = self.page_object_to_screen.get(class_ref or "")
                if screen:
                    if class_ref not in test.page_objects:
                        test.page_objects.append(class_ref)
                    note(screen, Confidence.HIGH,
                         f"{call.func.value.id}.{call.func.attr}() on {class_ref}",
                         call.lineno)
                    return

            # self.navigate_to("cart")
            if call.func.attr in NAVIGATION_HELPERS:
                for arg in call.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        screen = self._screen_from_literal(arg.value)
                        if screen:
                            note(screen, Confidence.MEDIUM,
                                 f"{call.func.attr}({arg.value!r})", call.lineno)

        elif isinstance(call.func, ast.Name) and call.func.id in NAVIGATION_HELPERS:
            for arg in call.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    screen = self._screen_from_literal(arg.value)
                    if screen:
                        note(screen, Confidence.MEDIUM,
                             f"{call.func.id}({arg.value!r})", call.lineno)

    def _collect_assertions(self, node: ast.AST, test: ParsedTest) -> None:
        def screen_at(line: int) -> str | None:
            preceding = [
                r for r in test.screens
                if r.line <= line and r.confidence.counts_as_coverage
            ]
            return max(preceding, key=lambda r: r.line).screen_id if preceding else None

        for child in ast.walk(node):
            if isinstance(child, ast.Assert):
                test.assertions.append(Assertion(
                    screen_id=screen_at(child.lineno), kind="assert",
                    expression=ast.unparse(child.test)[:120], line=child.lineno,
                ))
            elif isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) \
                    and child.func.attr in UNITTEST_ASSERTIONS:
                test.assertions.append(Assertion(
                    screen_id=screen_at(child.lineno), kind=child.func.attr,
                    expression=ast.unparse(child)[:120], line=child.lineno,
                ))

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _called_name(func: ast.expr) -> str | None:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    def _decorators(self, node) -> tuple[list[str], bool]:
        tags: list[str] = []
        skipped = False
        for decorator in node.decorator_list:
            text = ast.unparse(decorator)
            if "pytest.mark." in text:
                mark = text.split("pytest.mark.")[1].split("(")[0].strip()
                tags.append(mark)
                if mark.startswith("skip") or mark == "xfail":
                    skipped = True
            elif "skip" in text.lower():
                skipped = True
        return tags, skipped

    def _screen_from_literal(self, text: str) -> str | None:
        if text in self.screen_ids:
            return text
        if text in self.tag_to_screen:
            return self.tag_to_screen[text]
        return None

    def _screen_from_name(self, name: str) -> str | None:
        """Last-resort inference from a test name. Always LOW confidence."""
        words = name.removeprefix("test_").lower()
        best: str | None = None
        for screen_id in self.screen_ids:
            if screen_id in words and (best is None or len(screen_id) > len(best)):
                best = screen_id
        return best
