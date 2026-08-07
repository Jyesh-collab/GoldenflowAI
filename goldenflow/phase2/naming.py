"""The semantic layer - turning mined structures into names people can act on.

``aj_003: home -> product_detail -> cart`` is a fact. "Cart abandonment from
product page" is something a QE lead can prioritise. This module does that
translation.

Two implementations behind one protocol:

* :class:`RuleBasedNamer` - deterministic, offline, no API key. Derives names from
  the taxonomy's own vocabulary: domains, display names, and the outcome implied by
  the terminal screen. This is the default, and it is deliberately good enough to
  ship without an LLM.
* :class:`LlmJourneyNamer` - wraps any callable that takes a prompt and returns
  text. **It does not embed a provider or fabricate responses.** The caller supplies
  the completion function, so the dependency is theirs and the prompt is auditable.

The LLM is used only for interpretation, never for analysis. Every number in a
journey report comes from the deterministic pipeline; the model contributes the
sentence around it. That split is what keeps the output verifiable - a reviewer can
check the name against the sequence and see immediately if it is wrong.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence, runtime_checkable

from goldenflow.phase0.taxonomy import Taxonomy
from goldenflow.phase2.scoring import GoldenJourney

COMPLETION_MARKERS = ("confirmation", "success", "complete", "thank")
FAILURE_MARKERS = ("failure", "error", "declined", "rejected")
ABANDON_MARKERS = ("cart", "payment_method", "delivery_address", "order_review",
                   "checkout")


@dataclass
class JourneyDescription:
    name: str
    description: str
    outcome: str  # completed | abandoned | failed | exploratory


@runtime_checkable
class JourneyNamer(Protocol):
    def describe(self, journey: GoldenJourney) -> JourneyDescription: ...


class RuleBasedNamer:
    """Deterministic naming from the taxonomy's own vocabulary."""

    def __init__(self, taxonomy: Taxonomy | None = None) -> None:
        self.taxonomy = taxonomy

    def _display(self, screen_id: str) -> str:
        if self.taxonomy and (screen := self.taxonomy.by_id(screen_id)):
            return screen.display_name
        return screen_id.replace("_", " ").title()

    def _domain(self, screen_id: str) -> str:
        if self.taxonomy and (screen := self.taxonomy.by_id(screen_id)):
            return screen.domain
        return ""

    def classify(self, sequence: Sequence[str]) -> str:
        if not sequence:
            return "exploratory"
        terminal = sequence[-1]
        if any(marker in terminal for marker in FAILURE_MARKERS):
            return "failed"
        if any(marker in terminal for marker in COMPLETION_MARKERS):
            return "completed"
        if any(marker in terminal for marker in ABANDON_MARKERS):
            return "abandoned"
        return "exploratory"

    def describe(self, journey: GoldenJourney) -> JourneyDescription:
        sequence = journey.sequence
        if not sequence:
            return JourneyDescription("Empty journey", "No steps observed.", "exploratory")

        outcome = self.classify(sequence)
        entry, terminal = self._display(sequence[0]), self._display(sequence[-1])
        domains = [d for d in dict.fromkeys(self._domain(s) for s in sequence) if d]
        span = " / ".join(domains[:3]) if domains else "app"

        match outcome:
            case "completed":
                name = f"{entry} to Purchase"
                verb = "completes"
            case "failed":
                name = f"{entry} to {terminal}"
                verb = "fails at"
            case "abandoned":
                name = f"{terminal} Abandonment"
                verb = "stops at"
            case _:
                name = f"{entry} to {terminal}"
                verb = "ends at"

        description = (
            f"{journey.session_count:,} sessions ({journey.session_share_pct:.1f}% of "
            f"traffic) enter at {entry}, move through {span}, and the journey {verb} "
            f"{terminal} after {len(sequence)} screens."
        )
        return JourneyDescription(name=name, description=description, outcome=outcome)


class LlmJourneyNamer:
    """Naming via a caller-supplied completion function.

    Args:
        complete: Any ``Callable[[str], str]``. Wire it to the Claude API, a local
            model, or anything else - GoldenFlow does not choose for you and does
            not ship a client.
        taxonomy: Used to give the model display names rather than raw IDs.
        fallback: Used when the model errors or returns unusable JSON. Defaults to
            the rule-based namer, so an LLM outage degrades the naming rather than
            breaking the pipeline.
    """

    def __init__(
        self,
        complete: Callable[[str], str],
        *,
        taxonomy: Taxonomy | None = None,
        fallback: JourneyNamer | None = None,
    ) -> None:
        self.complete = complete
        self.taxonomy = taxonomy
        self.fallback = fallback or RuleBasedNamer(taxonomy)

    def build_prompt(self, journey: GoldenJourney) -> str:
        """The exact prompt sent. Exposed so it can be reviewed and version-pinned."""
        namer = RuleBasedNamer(self.taxonomy)
        steps = [
            {"screen": s, "display_name": namer._display(s), "domain": namer._domain(s)}
            for s in journey.sequence
        ]
        facts = {
            "sessions": journey.session_count,
            "share_of_traffic_pct": round(journey.session_share_pct, 2),
            "distinct_variants": journey.archetype.variant_count,
            "mean_duration_seconds": journey.archetype.mean_duration,
            "failure_signals": journey.risk_events,
            "terminal_screens": journey.archetype.terminal_screens,
        }
        return (
            "You are naming a user journey discovered by process mining in a mobile "
            "app. Below are the mined facts. Do not recompute or dispute them - they "
            "come from a deterministic pipeline and are authoritative.\n\n"
            f"Steps:\n{json.dumps(steps, indent=2)}\n\n"
            f"Measured facts:\n{json.dumps(facts, indent=2)}\n\n"
            "Return ONLY JSON with keys: name (max 6 words, business language, no "
            "screen IDs), description (one sentence a QE lead can act on), outcome "
            "(one of: completed, abandoned, failed, exploratory)."
        )

    def describe(self, journey: GoldenJourney) -> JourneyDescription:
        try:
            raw = self.complete(self.build_prompt(journey))
            payload = json.loads(_strip_fences(raw))
            name = str(payload["name"]).strip()
            description = str(payload["description"]).strip()
            outcome = str(payload.get("outcome", "exploratory")).strip()
            if not name or not description:
                raise ValueError("empty name or description")
            if outcome not in {"completed", "abandoned", "failed", "exploratory"}:
                outcome = RuleBasedNamer(self.taxonomy).classify(journey.sequence)
            return JourneyDescription(name, description, outcome)
        except Exception:
            # Naming is presentation. It must never take down the pipeline that
            # produced the numbers.
            return self.fallback.describe(journey)


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        body = lines[1:-1] if len(lines) > 2 and lines[-1].strip() == "```" else lines[1:]
        return "\n".join(body)
    return stripped


def name_journeys(
    journeys: Sequence[GoldenJourney], namer: JourneyNamer | None = None,
    *, taxonomy: Taxonomy | None = None,
) -> list[GoldenJourney]:
    """Annotate journeys in place with names and descriptions."""
    namer = namer or RuleBasedNamer(taxonomy)
    for journey in journeys:
        described = namer.describe(journey)
        journey.archetype.name = described.name
        journey.archetype.description = described.description
    return list(journeys)
