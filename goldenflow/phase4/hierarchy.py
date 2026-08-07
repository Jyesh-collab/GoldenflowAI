"""UI hierarchy parsing - Appium page source into a queryable element tree.

This is the raw material for every resolution path that is not a Page Object. A
crawler dump, a session-replay hierarchy and a live ``driver.page_source`` are all
the same XML, so they all land here.

Two platform dialects are supported because they are genuinely different documents:

**Android (UIAutomator)** uses ``resource-id``, ``content-desc``, ``text`` and a
``class`` attribute, with the element type also as the tag name.

**iOS (XCUITest)** uses ``name``, ``label`` and ``value``, with the element type as
the tag (``XCUIElementTypeButton``) and no resource ID concept at all - which is
exactly why iOS suites lean so heavily on accessibility identifiers.

Locator generation deliberately emits *all* viable candidates rather than picking
one. Ranking happens in :mod:`goldenflow.phase4.locators`, where uniqueness across
the whole screen is known; a generator that chose locally would pick an
accessibility ID that turns out to appear four times.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator
from xml.etree import ElementTree

from goldenflow.phase4.locators import Locator, Strategy

BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
INTERACTIVE_CLASSES = (
    "Button", "EditText", "TextView", "ImageView", "CheckBox", "Switch",
    "RadioButton", "Cell", "SecureTextField", "SearchField", "StaticText",
)


@dataclass
class UiElement:
    """One node of the view hierarchy."""

    element_type: str
    resource_id: str = ""
    accessibility_id: str = ""
    text: str = ""
    label: str = ""
    index: int = 0
    clickable: bool = False
    enabled: bool = True
    displayed: bool = True
    bounds: tuple[int, int, int, int] | None = None
    children: list["UiElement"] = field(default_factory=list)
    parent: "UiElement | None" = field(default=None, repr=False, compare=False)
    depth: int = 0

    # ------------------------------------------------------------- traversal

    def walk(self) -> Iterator["UiElement"]:
        yield self
        for child in self.children:
            yield from child.walk()

    @property
    def short_type(self) -> str:
        """``android.widget.Button`` -> ``Button``; ``XCUIElementTypeButton`` -> ``Button``."""
        name = self.element_type.rsplit(".", 1)[-1]
        return name.removeprefix("XCUIElementType")

    @property
    def short_resource_id(self) -> str:
        """``com.acme.shop:id/cart_checkout`` -> ``cart_checkout``."""
        return self.resource_id.split("/", 1)[-1] if "/" in self.resource_id else ""

    @property
    def is_interactive(self) -> bool:
        return self.clickable or self.short_type in INTERACTIVE_CLASSES

    @property
    def role(self) -> str:
        """A stable, human-meaningful name for this element.

        Prefers the semantic identifiers precisely because those are what survive
        a redesign; falls back to text, then to a structural description.
        """
        for candidate in (self.accessibility_id, self.short_resource_id):
            if candidate:
                return candidate
        if self.text:
            return re.sub(r"\W+", "_", self.text.strip().lower())[:40]
        return f"{self.short_type.lower()}_{self.index}"

    def path_from_root(self) -> list["UiElement"]:
        chain: list[UiElement] = []
        node: UiElement | None = self
        while node is not None:
            chain.append(node)
            node = node.parent
        return list(reversed(chain))

    def xpath(self, *, positional: bool) -> str:
        if positional:
            steps = [f"{n.element_type}[{n.index + 1}]" for n in self.path_from_root()[1:]]
            return "/" + "/".join(steps) if steps else f"//{self.element_type}"
        if self.text:
            return f'//{self.element_type}[@text="{self.text}"]'
        if self.label:
            return f'//{self.element_type}[@label="{self.label}"]'
        return f"//{self.element_type}"

    def candidate_locators(self, screen_id: str, source: str) -> list[Locator]:
        """Every viable way to find this element. Ranking happens elsewhere."""
        found: list[Locator] = []

        def add(strategy: Strategy, value: str, evidence: str) -> None:
            found.append(Locator(strategy=strategy, value=value, screen_id=screen_id,
                                 element_role=self.role, source=source,
                                 evidence=evidence))

        if self.accessibility_id:
            add(Strategy.ACCESSIBILITY_ID, self.accessibility_id, "content-desc/name")
        if self.resource_id:
            add(Strategy.RESOURCE_ID, self.resource_id, "resource-id")
        if self.text or self.label:
            add(Strategy.SEMANTIC_XPATH, self.xpath(positional=False), "text/label anchor")
        add(Strategy.POSITIONAL_XPATH, self.xpath(positional=True), "structural index")
        return found


@dataclass
class ScreenDump:
    """One captured screen: its hierarchy plus where it came from."""

    screen_id: str
    root: UiElement
    platform: str = "android"
    app_version: str = ""
    source: str = "crawl"

    @property
    def elements(self) -> list[UiElement]:
        return list(self.root.walk())

    @property
    def interactive_elements(self) -> list[UiElement]:
        return [e for e in self.elements if e.is_interactive]

    def element_by_role(self, role: str) -> UiElement | None:
        return next((e for e in self.elements if e.role == role), None)

    def unique_roles(self) -> dict[int, str]:
        """Screen-wide unique role per element, keyed by ``id(element)``.

        :attr:`UiElement.role` prefers the accessibility ID, which is right in
        isolation and wrong on a screen where three payment rows share one. Those
        three would collapse into a single role and the resolver would arbitrarily
        pick whichever was parsed first - silently generating a test that taps the
        card row when it meant the wallet row.

        So identifiers are used only while they stay unique, and the first unique
        one wins.
        """
        elements = self.elements
        by_accessibility: Counter[str] = Counter(
            e.accessibility_id for e in elements if e.accessibility_id
        )
        by_resource: Counter[str] = Counter(
            e.short_resource_id for e in elements if e.short_resource_id
        )
        by_text: Counter[str] = Counter(e.text for e in elements if e.text)

        roles: dict[int, str] = {}
        used: Counter[str] = Counter()
        for element in elements:
            candidates = [
                (element.accessibility_id, by_accessibility),
                (element.short_resource_id, by_resource),
                (element.text, by_text),
            ]
            role = ""
            for value, counts in candidates:
                if value and counts[value] == 1:
                    role = re.sub(r"\W+", "_", value.strip().lower())[:40]
                    break
            if not role:
                role = element.role
            used[role] += 1
            roles[id(element)] = role if used[role] == 1 else f"{role}_{used[role]}"
        return roles

    def match_counts(self) -> dict[tuple[Strategy, str], int]:
        """How many elements each candidate locator would match, screen-wide.

        Computed once per screen so uniqueness is a fact rather than an assumption.
        """
        counter: Counter[tuple[Strategy, str]] = Counter()
        for element in self.elements:
            for locator in element.candidate_locators(self.screen_id, self.source):
                counter[(locator.strategy, locator.value)] += 1
        return dict(counter)

    def locators_for(self, element: UiElement) -> list[Locator]:
        """Candidates for one element, with real screen-wide match counts applied."""
        counts = self.match_counts()
        located = element.candidate_locators(self.screen_id, self.source)
        for locator in located:
            locator.match_count = counts.get((locator.strategy, locator.value), 1)
        return located

    def summary(self) -> dict[str, object]:
        return {
            "screen_id": self.screen_id,
            "platform": self.platform,
            "app_version": self.app_version,
            "elements": len(self.elements),
            "interactive": len(self.interactive_elements),
            "with_accessibility_id": sum(
                1 for e in self.elements if e.accessibility_id
            ),
            "with_resource_id": sum(1 for e in self.elements if e.resource_id),
        }


# --------------------------------------------------------------------- parsing


def _to_bool(value: str | None) -> bool:
    return str(value).strip().lower() == "true"


def _parse_bounds(value: str | None) -> tuple[int, int, int, int] | None:
    if not value:
        return None
    match = BOUNDS.match(value)
    if match is None:
        return None
    x1, y1, x2, y2 = (int(g) for g in match.groups())
    return x1, y1, x2, y2


def _build(node: ElementTree.Element, depth: int, parent: UiElement | None) -> UiElement:
    attrib = node.attrib
    element = UiElement(
        element_type=attrib.get("class") or attrib.get("type") or node.tag,
        resource_id=attrib.get("resource-id", ""),
        # Android uses content-desc; iOS XCUITest uses name.
        accessibility_id=attrib.get("content-desc") or attrib.get("name") or "",
        text=attrib.get("text", ""),
        label=attrib.get("label", ""),
        index=int(attrib.get("index", "0") or 0),
        clickable=_to_bool(attrib.get("clickable")),
        enabled=_to_bool(attrib.get("enabled")) if "enabled" in attrib else True,
        displayed=_to_bool(attrib.get("visible")) if "visible" in attrib else True,
        bounds=_parse_bounds(attrib.get("bounds")),
        parent=parent,
        depth=depth,
    )
    for index, child in enumerate(node):
        built = _build(child, depth + 1, element)
        if not built.index:
            built.index = index
        element.children.append(built)
    return element


def parse_page_source(
    xml: str, screen_id: str, *, platform: str = "android",
    app_version: str = "", source: str = "crawl",
) -> ScreenDump:
    """Parse Appium page source into a :class:`ScreenDump`.

    Raises:
        ValueError: with the screen name attached, because a bare ExpatError in a
            batch of two hundred dumps tells you nothing about which one is broken.
    """
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ValueError(f"malformed page source for {screen_id!r}: {exc}") from exc

    # <hierarchy> is a wrapper, not an element; descend past it when present.
    if root.tag == "hierarchy" and len(root) == 1:
        root = root[0]

    return ScreenDump(
        screen_id=screen_id, root=_build(root, 0, None), platform=platform,
        app_version=app_version, source=source,
    )


def load_dump_directory(
    directory: str | Path, *, platform: str = "android", app_version: str = "",
    source: str = "crawl",
) -> dict[str, ScreenDump]:
    """Load ``<screen_id>.xml`` dumps from a directory, keyed by screen ID."""
    dumps: dict[str, ScreenDump] = {}
    for path in sorted(Path(directory).glob("*.xml")):
        dumps[path.stem] = parse_page_source(
            path.read_text(encoding="utf-8"), path.stem,
            platform=platform, app_version=app_version, source=source,
        )
    return dumps
