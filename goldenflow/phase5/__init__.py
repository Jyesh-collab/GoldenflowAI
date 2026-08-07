"""Phase 5 - Generation & Validation (Agent 2's output stage).

The credibility of the entire product rests on one rule:

    **Nothing reaches a human reviewer until it has already run green on a real
    device.**

A QE team that merges two broken generated tests will never trust the third, so the
pipeline is built to fail closed::

    spec (deterministic) -> generate -> compile -> run N x M profiles
                                     -> assert deterministic -> PR

Three seams are deliberately left open rather than faked:

* :class:`~goldenflow.phase5.validation.DeviceFarmRunner` raises instead of
  returning a default. A runner that silently reports success is the most dangerous
  thing this codebase could contain.
* :class:`~goldenflow.phase5.generator.LlmGenerator` takes a caller-supplied
  completion function. No provider, no fabricated API calls.
* Pull requests are payloads. GoldenFlow never commits to a protected branch.
"""

from goldenflow.phase5.generator import (
    GeneratedTest,
    Generator,
    LlmGenerator,
    RepoConventions,
    TemplateGenerator,
    generate_all,
)
from goldenflow.phase5.pull_request import (
    ChangeKind,
    DeletionProposal,
    ProposedChange,
    PullRequest,
    build_pull_request,
    deletion_pull_request,
    propose_deletions,
)
from goldenflow.phase5.spec import Provenance, StepSpec, TestSpec, build_spec, build_specs
from goldenflow.phase5.validation import (
    CompileOnlyRunner,
    DeviceFarmRunner,
    RunResult,
    TestRunner,
    ValidationGate,
    ValidationReport,
    ValidationResult,
    Verdict,
)

__all__ = [
    "GeneratedTest", "Generator", "LlmGenerator", "RepoConventions",
    "TemplateGenerator", "generate_all",
    "ChangeKind", "DeletionProposal", "ProposedChange", "PullRequest",
    "build_pull_request", "deletion_pull_request", "propose_deletions",
    "Provenance", "StepSpec", "TestSpec", "build_spec", "build_specs",
    "CompileOnlyRunner", "DeviceFarmRunner", "RunResult", "TestRunner",
    "ValidationGate", "ValidationReport", "ValidationResult", "Verdict",
]
