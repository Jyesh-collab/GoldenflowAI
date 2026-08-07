"""Phase 3 - QA Asset Graph & Gap Analysis.

**The first phase that delivers standalone value.** Before any code generation
exists, "here are the journeys your suite does not cover" is worth the build on its
own - and shipping insight before automation earns the trust that Phase 5 will need.
Reverse the order and QE teams meet the product as a code-spam machine.

The pipeline::

    automation repo -> parse -> project onto the journey graph -> diff -> findings
                                        (Phase 2)                        |
                                                                         +-> Jira
                                                                         +-> Grafana

Coverage is measured in **transitions**, not screens. A suite can touch every screen
in the app and never walk ``cart -> payment_method``, and it is the transitions where
navigation regressions live.
"""

from goldenflow.phase3.coverage import (
    CoverageModel,
    JourneyCoverage,
    TransitionCoverage,
    build_coverage,
)
from goldenflow.phase3.gaps import (
    Finding,
    FindingKind,
    GapReport,
    Severity,
    analyse_gaps,
    deletable_candidates,
)
from goldenflow.phase3.models import (
    Assertion,
    Confidence,
    Language,
    PageObject,
    ParsedTest,
    ParseIssue,
    ScreenReference,
    TestSuite,
)
from goldenflow.phase3.parser_python import PythonSuiteParser
from goldenflow.phase3.parser_text import TextSuiteParser, parse_repository
from goldenflow.phase3.report import (
    JiraTicket,
    to_jira_tickets,
    to_prometheus,
    write_jira_payloads,
    write_prometheus,
)

__all__ = [
    "CoverageModel", "JourneyCoverage", "TransitionCoverage", "build_coverage",
    "Finding", "FindingKind", "GapReport", "Severity", "analyse_gaps",
    "deletable_candidates",
    "Assertion", "Confidence", "Language", "PageObject", "ParsedTest",
    "ParseIssue", "ScreenReference", "TestSuite",
    "PythonSuiteParser", "TextSuiteParser", "parse_repository",
    "JiraTicket", "to_jira_tickets", "to_prometheus", "write_jira_payloads",
    "write_prometheus",
]
