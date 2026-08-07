"""Java, Kotlin and JavaScript suite parsers.

Unlike Python - where ``ast`` is in the standard library and guessing would be
inexcusable - these are **regex-based and lower fidelity**. Parsing Java properly
would mean shipping a JVM or a full grammar, which is not a reasonable dependency
for a coverage estimate.

The honest consequence: these parsers can *miss* things. What they should not do is
*invent* things, so two precautions apply:

* Comments and string bodies are stripped before matching, so a Page Object named
  in a ``// TODO: use CartPage`` comment never counts as coverage.
* Confidence semantics are identical across languages. An explicit Page Object
  reference is HIGH whatever the language; the difference between parsers is recall,
  not trustworthiness.

Files that yield no tests are reported as parse issues rather than passed over, so
a suite silently failing to parse shows up as a falling parse rate instead of a
mysteriously large coverage gap.
"""

from __future__ import annotations

import re
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

LINE_COMMENT = re.compile(r"//[^\n]*")
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

JAVA_TEST = re.compile(
    r"@Test(?:\s*\([^)]*\))?\s*(?:@\w+(?:\([^)]*\))?\s*)*"
    r"(?:public|private|protected)?\s*(?:void|fun)\s+(\w+)",
    re.MULTILINE,
)
KOTLIN_TEST = re.compile(r"@Test\s*(?:@\w+(?:\([^)]*\))?\s*)*fun\s+`?([\w\s]+?)`?\s*\(")
JS_TEST = re.compile(
    r"""\b(?:x?it|test)(?:\.(?:skip|only|todo|failing))?\s*\(\s*['"`](.+?)['"`]"""
)
"""Matches ``it``, ``test``, ``xit``, and the ``.skip``/``.only`` variants.

Missing the variants is not a cosmetic recall problem: an unmatched ``it.skip``
leaves the *previous* test's body running to end-of-file, so that test absorbs the
skip marker and is reported as skipped while the genuinely skipped one vanishes.
"""

ANNOTATION_ARGS = re.compile(r"@\w+\s*\([^)]*\)", re.DOTALL)
"""TestNG/JUnit annotation argument lists. Stripped before screen attribution:
``@Test(groups = {"regression", "cart"})`` would otherwise register the group name
``"cart"`` as coverage of the cart screen."""
JS_DESCRIBE = re.compile(r"""\bdescribe\s*\(\s*['"`](.+?)['"`]""")

JAVA_CLASS = re.compile(r"(?:public\s+)?class\s+(\w+)")
JS_CLASS = re.compile(r"class\s+(\w+)")

JAVA_LOCATOR = re.compile(
    r"""(?:@AndroidFindBy|@iOSXCUITFindBy)\s*\(\s*\w+\s*=\s*["'](.+?)["']\s*\)\s*"""
    r"""(?:private|public|protected)?\s*\w+\s+(\w+)""",
    re.DOTALL,
)
JAVA_BY_LOCATOR = re.compile(
    r"""(\w+)\s*=\s*(?:By|AppiumBy|MobileBy)\.\w+\(\s*["'](.+?)["']\s*\)"""
)
JS_LOCATOR = re.compile(r"""(?:get\s+)?(\w+)\s*[:(]\s*.{0,40}?\$\(\s*['"`](.+?)['"`]""")

JAVA_ASSERT = re.compile(r"\b(Assert\.\w+|assertThat|assertEquals|assertTrue)\s*\(")
JS_ASSERT = re.compile(r"\bexpect\s*\(")

SKIP_MARKERS = ("@Ignore", "@Disabled", "it.skip", "test.skip", "xit(", "describe.skip")
PAGE_OBJECT_SUFFIXES = ("Page", "Screen", "View")

LANGUAGE_BY_SUFFIX = {
    ".java": Language.JAVA,
    ".kt": Language.KOTLIN,
    ".js": Language.JAVASCRIPT,
    ".ts": Language.JAVASCRIPT,
}


def strip_comments(source: str) -> str:
    """Blank out comments, preserving line numbers so reported lines stay correct."""
    def blank(match: re.Match[str]) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))
    return LINE_COMMENT.sub(blank, BLOCK_COMMENT.sub(blank, source))


class TextSuiteParser:
    """Heuristic parser for Java, Kotlin and JavaScript automation suites."""

    def __init__(self, taxonomy: Taxonomy, root: str | Path) -> None:
        self.taxonomy = taxonomy
        self.root = Path(root)
        self.page_object_to_screen = {
            page_object: screen.screen_id
            for screen in taxonomy.screens
            for page_object in screen.page_objects
        }
        self.tag_to_screen = {
            tag: screen.screen_id
            for screen in taxonomy.screens
            for tag in screen.analytics_tags
        }
        self.screen_ids = {s.screen_id for s in taxonomy.screens}

    # ------------------------------------------------------------- entrypoints

    def parse_directory(self, directory: str | Path) -> TestSuite:
        suite = TestSuite()
        for suffix in LANGUAGE_BY_SUFFIX:
            for path in sorted(Path(directory).rglob(f"*{suffix}")):
                suite = suite.merge(self.parse_file(path))
        return suite

    def parse_file(self, path: str | Path) -> TestSuite:
        path = Path(path)
        language = LANGUAGE_BY_SUFFIX.get(path.suffix)
        suite = TestSuite(files_scanned=1)
        if language is None:
            suite.issues.append(ParseIssue(str(path), f"unsupported suffix {path.suffix}"))
            return suite

        source = strip_comments(path.read_text(encoding="utf-8"))
        page_object = self._parse_page_object(source, path, language)
        if page_object is not None:
            suite.page_objects.append(page_object)

        tests = self._parse_tests(source, path, language)
        suite.tests.extend(tests)

        if not tests and page_object is None:
            suite.issues.append(ParseIssue(
                str(path),
                "no tests or Page Objects recognised; heuristic parser may not "
                "support this file's structure",
            ))
        return suite

    # ------------------------------------------------------------ page objects

    def _parse_page_object(
        self, source: str, path: Path, language: Language
    ) -> PageObject | None:
        pattern = JS_CLASS if language is Language.JAVASCRIPT else JAVA_CLASS
        match = pattern.search(source)
        if match is None:
            return None
        class_name = match.group(1)
        if class_name not in self.page_object_to_screen \
                and not class_name.endswith(PAGE_OBJECT_SUFFIXES):
            return None

        locators: dict[str, str] = {}
        if language is Language.JAVASCRIPT:
            for name, value in JS_LOCATOR.findall(source):
                locators[name] = value
        else:
            for value, name in JAVA_LOCATOR.findall(source):
                locators[name] = value
            for name, value in JAVA_BY_LOCATOR.findall(source):
                locators.setdefault(name, value)

        return PageObject(
            class_name=class_name,
            file_path=str(path),
            language=language,
            screen_id=self.page_object_to_screen.get(class_name),
            locators=locators,
            line=source[: match.start()].count("\n") + 1,
        )

    # ------------------------------------------------------------------- tests

    def _parse_tests(
        self, source: str, path: Path, language: Language
    ) -> list[ParsedTest]:
        if language is Language.JAVASCRIPT:
            matches = list(JS_TEST.finditer(source))
            assert_pattern = JS_ASSERT
        else:
            matches = list(JAVA_TEST.finditer(source)) or list(KOTLIN_TEST.finditer(source))
            assert_pattern = JAVA_ASSERT

        if not matches:
            return []

        suite_name = ""
        if language is Language.JAVASCRIPT and (d := JS_DESCRIBE.search(source)):
            suite_name = d.group(1)

        tests: list[ParsedTest] = []
        for index, match in enumerate(matches):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
            body = source[start:end]
            line = source[:start].count("\n") + 1
            name = match.group(1).strip()

            declaration = source[max(0, start - 200):start] + body[:120]
            test = ParsedTest(
                test_id=relative_id(path, self.root, name),
                name=name,
                file_path=str(path),
                language=language,
                line=line,
                tags=[suite_name] if suite_name else [],
                skipped=any(marker in declaration for marker in SKIP_MARKERS),
            )
            # Annotation arguments are metadata, not navigation.
            self._attribute_screens(ANNOTATION_ARGS.sub(
                lambda m: " " * len(m.group(0)), body), line, test)
            for assertion in assert_pattern.finditer(body):
                offset = body[: assertion.start()].count("\n")
                test.assertions.append(Assertion(
                    screen_id=self._screen_before(test, line + offset),
                    kind=assertion.group(0).strip("( "),
                    expression=body[assertion.start():assertion.start() + 90]
                        .replace("\n", " ").strip(),
                    line=line + offset,
                ))
            if not test.screens and (inferred := self._screen_from_name(name)):
                test.screens.append(ScreenReference(
                    screen_id=inferred, confidence=Confidence.LOW,
                    evidence=f"inferred from test name {name!r}", line=line, order=1,
                ))
            tests.append(test)
        return tests

    def _attribute_screens(self, body: str, base_line: int, test: ParsedTest) -> None:
        order = 0
        hits: list[tuple[int, str, Confidence, str]] = []

        for class_name, screen_id in self.page_object_to_screen.items():
            for match in re.finditer(rf"\b{re.escape(class_name)}\b", body):
                hits.append((
                    match.start(), screen_id, Confidence.HIGH,
                    f"references {class_name}",
                ))
                if class_name not in test.page_objects:
                    test.page_objects.append(class_name)

        for literal, screen_id in {**{s: s for s in self.screen_ids},
                                   **self.tag_to_screen}.items():
            for match in re.finditer(rf"""['"`]{re.escape(literal)}['"`]""", body):
                hits.append((
                    match.start(), screen_id, Confidence.MEDIUM,
                    f"string literal {literal!r}",
                ))

        for position, screen_id, confidence, evidence in sorted(hits):
            order += 1
            test.screens.append(ScreenReference(
                screen_id=screen_id, confidence=confidence, evidence=evidence,
                line=base_line + body[:position].count("\n"), order=order,
            ))

    @staticmethod
    def _screen_before(test: ParsedTest, line: int) -> str | None:
        preceding = [
            r for r in test.screens
            if r.line <= line and r.confidence.counts_as_coverage
        ]
        return max(preceding, key=lambda r: r.line).screen_id if preceding else None

    def _screen_from_name(self, name: str) -> str | None:
        words = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower().replace(" ", "_")
        best: str | None = None
        for screen_id in self.screen_ids:
            if screen_id in words and (best is None or len(screen_id) > len(best)):
                best = screen_id
        return best


def parse_repository(taxonomy: Taxonomy, root: str | Path) -> TestSuite:
    """Parse every supported language under ``root`` into one suite."""
    from goldenflow.phase3.parser_python import PythonSuiteParser

    root = Path(root)
    python = PythonSuiteParser(taxonomy, root).parse_directory(root)
    text = TextSuiteParser(taxonomy, root).parse_directory(root)
    return python.merge(text)
