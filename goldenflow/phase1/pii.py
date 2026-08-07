"""PII scanner - enforcement for docs/pii-policy.md.

The policy says never put personal data in event properties. Policy that is not
scanned for is policy that is not followed: instrumenting an interaction feels like
telemetry, so attaching its payload feels harmless, and email addresses end up in a
warehouse nobody classified as containing personal data.

This runs over real event properties and fails the Phase 1 gate on any detection.

**Findings never contain the matched value.** A PII report that quotes the PII it
found is itself a leak - it gets pasted into tickets, Slack and CI logs, none of
which are classified for personal data. Findings carry a redacted fingerprint
sufficient to locate the source and useless for identifying the person.

Detection is conservative by design. Card numbers are Luhn-validated, so an order
ID that happens to be sixteen digits does not trigger a false alarm - a scanner
people learn to ignore protects nobody.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from goldenflow.phase1.store import CanonicalEvent


class PiiKind(str, Enum):
    EMAIL = "email"
    PHONE = "phone"
    CARD_NUMBER = "card_number"
    NATIONAL_ID = "national_id"
    IP_ADDRESS = "ip_address"
    CREDENTIAL = "credential"
    POSTAL_ADDRESS = "postal_address"
    SUSPICIOUS_KEY = "suspicious_key"


PATTERNS: dict[PiiKind, re.Pattern[str]] = {
    PiiKind.EMAIL: re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}"),
    PiiKind.PHONE: re.compile(r"(?<!\d)(?:\+\d{1,3}[\s-]?)?(?:\d[\s-]?){9,14}\d(?!\d)"),
    PiiKind.NATIONAL_ID: re.compile(
        r"(?<!\w)(?:\d{3}-\d{2}-\d{4}|[A-Z]{5}\d{4}[A-Z]|\d{4}\s?\d{4}\s?\d{4})(?!\w)"
    ),
    PiiKind.IP_ADDRESS: re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"),
    PiiKind.CREDENTIAL: re.compile(
        r"(?i)(?:bearer\s+[\w.-]{20,}|eyJ[\w-]{10,}\.[\w-]{10,}|"
        r"(?:api[_-]?key|token|secret|password)\s*[=:]\s*\S{8,})"
    ),
}

SUSPICIOUS_KEYS = {
    "email", "email_address", "phone", "phone_number", "mobile", "msisdn",
    "password", "passwd", "pin", "otp", "cvv", "card", "card_number", "pan",
    "ssn", "aadhaar", "national_id", "passport", "dob", "date_of_birth",
    "address", "street", "postcode", "zip", "full_name", "first_name",
    "last_name", "latitude", "longitude", "token", "api_key", "secret",
}
"""Property names that should never appear regardless of the value observed in a
sample. A field named `email` that is null today will not be null tomorrow."""

CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def _luhn_valid(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _redact(value: str) -> str:
    """A locator, not the value. Length plus a short salted-ish digest."""
    digest = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"<redacted len={len(value)} sha={digest}>"


@dataclass(frozen=True)
class PiiFinding:
    kind: PiiKind
    event_name: str
    property_path: str
    redacted_sample: str
    occurrences: int = 1

    def format(self) -> str:
        return (
            f"{self.kind.value:16} {self.event_name:24} {self.property_path:28} "
            f"x{self.occurrences}  {self.redacted_sample}"
        )


@dataclass
class PiiReport:
    events_scanned: int = 0
    findings: list[PiiFinding] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.findings

    @property
    def affected_events(self) -> set[str]:
        return {f.event_name for f in self.findings}

    def by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.kind.value] = counts.get(f.kind.value, 0) + f.occurrences
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def format(self, limit: int = 20) -> str:
        header = f"PII scan: {self.events_scanned:,} events"
        if self.clean:
            return f"{header}\nCLEAN - no personal data detected in event properties."
        lines = [
            header,
            f"FAILED - {len(self.findings)} finding(s) across "
            f"{len(self.affected_events)} event type(s).",
            "Values are redacted; see docs/pii-policy.md for remediation.",
            "",
            f"{'kind':16} {'event':24} {'property':28} {'count':6} sample",
        ]
        lines += [f"  {f.format()}" for f in self.findings[:limit]]
        if len(self.findings) > limit:
            lines.append(f"  ... and {len(self.findings) - limit} more")
        return "\n".join(lines)


class PiiScanner:
    """Scans property trees for personal data."""

    def __init__(
        self,
        *,
        suspicious_keys: set[str] | None = None,
        check_keys: bool = True,
        allowlist: set[str] | None = None,
    ) -> None:
        self.suspicious_keys = suspicious_keys or SUSPICIOUS_KEYS
        self.check_keys = check_keys
        self.allowlist = allowlist or set()

    def scan_value(self, value: Any) -> list[tuple[PiiKind, str]]:
        if not isinstance(value, str) or not value.strip():
            return []
        hits: list[tuple[PiiKind, str]] = []

        for candidate in CARD_CANDIDATE.findall(value):
            digits = re.sub(r"\D", "", candidate)
            if 13 <= len(digits) <= 19 and _luhn_valid(digits):
                hits.append((PiiKind.CARD_NUMBER, candidate))

        for kind, pattern in PATTERNS.items():
            for match in pattern.findall(value):
                text = match if isinstance(match, str) else next(
                    (m for m in match if m), ""
                )
                if not text:
                    continue
                # A phone match that is really a Luhn-valid card is already reported.
                if kind is PiiKind.PHONE and any(
                    h[0] is PiiKind.CARD_NUMBER for h in hits
                ):
                    continue
                hits.append((kind, text))
        return hits

    def scan_properties(
        self, properties: dict[str, Any], event_name: str, prefix: str = ""
    ) -> list[PiiFinding]:
        findings: list[PiiFinding] = []
        for key, value in properties.items():
            path = f"{prefix}{key}"
            if path in self.allowlist:
                continue

            if self.check_keys and str(key).lower() in self.suspicious_keys:
                findings.append(PiiFinding(
                    kind=PiiKind.SUSPICIOUS_KEY,
                    event_name=event_name,
                    property_path=path,
                    redacted_sample="<key name forbidden by policy>",
                ))

            if isinstance(value, dict):
                findings += self.scan_properties(value, event_name, f"{path}.")
                continue
            if isinstance(value, (list, tuple)):
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        findings += self.scan_properties(item, event_name, f"{path}[{i}].")
                    else:
                        findings += [
                            PiiFinding(kind=k, event_name=event_name,
                                       property_path=f"{path}[{i}]",
                                       redacted_sample=_redact(text))
                            for k, text in self.scan_value(item)
                        ]
                continue

            findings += [
                PiiFinding(kind=kind, event_name=event_name, property_path=path,
                           redacted_sample=_redact(text))
                for kind, text in self.scan_value(value)
            ]
        return findings


def scan_events(
    events: Iterable[CanonicalEvent], scanner: PiiScanner | None = None
) -> PiiReport:
    """Scan a batch, aggregating identical findings so one bad field reported ten
    thousand times reads as one problem rather than ten thousand."""
    scanner = scanner or PiiScanner()
    report = PiiReport()
    aggregated: dict[tuple, PiiFinding] = {}

    for event in events:
        report.events_scanned += 1
        for finding in scanner.scan_properties(event.properties, event.event_name):
            key = (finding.kind, finding.event_name, finding.property_path)
            existing = aggregated.get(key)
            if existing is None:
                aggregated[key] = finding
            else:
                aggregated[key] = PiiFinding(
                    kind=existing.kind,
                    event_name=existing.event_name,
                    property_path=existing.property_path,
                    redacted_sample=existing.redacted_sample,
                    occurrences=existing.occurrences + 1,
                )

    report.findings = sorted(
        aggregated.values(), key=lambda f: (-f.occurrences, f.event_name)
    )
    return report
