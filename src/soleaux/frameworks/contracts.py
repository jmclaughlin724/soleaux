"""Registration claims projected into `framework.registrations`.

A detector reads workspace-relative paths plus captured bytes and returns
registrations it can prove, together with the reasons its coverage is not
authoritative. Detectors never touch the filesystem: every input is already
frozen in the request snapshot.

The row shape here is currently Next.js's vocabulary — `RouterKind`, route
groups, parallel slots, and intercepting markers are App Router concepts. It is
shared rather than framework-neutral: a second framework would extend this
vocabulary, not slot into it unchanged.
"""

from __future__ import annotations

import dataclasses
import enum


class RegistrationKind(enum.StrEnum):
    """What a registration file contributes to its framework."""

    PAGE = "page"
    ROUTE_HANDLER = "route_handler"
    LAYOUT = "layout"
    DEFAULT = "default"
    NOT_FOUND = "not_found"
    PROXY = "proxy"


class RouterKind(enum.StrEnum):
    """Which router owns a registration."""

    APP = "app"
    PAGES = "pages"


class SegmentKind(enum.StrEnum):
    """How a dynamic segment matches path parts."""

    REQUIRED = "required"
    CATCH_ALL = "catch_all"
    OPTIONAL_CATCH_ALL = "optional_catch_all"


@dataclasses.dataclass(frozen=True, slots=True)
class DynamicSegment:
    """One parameterized segment of a route pattern."""

    segment: str
    param: str
    kind: SegmentKind
    index: int


@dataclasses.dataclass(frozen=True, slots=True)
class Registration:
    """One proven framework registration anchored to a captured file.

    `route` is the framework's own filesystem route pattern, project-relative:
    never prefixed with `project_dir` and never rewritten by configured base
    paths or redirects. `project_dir` is the only disambiguator between two
    projects that expose the same route.
    """

    framework: str
    project_dir: str
    path: str
    kind: RegistrationKind
    route: str | None = None
    router: RouterKind | None = None
    dynamic_segments: tuple[DynamicSegment, ...] = ()
    route_groups: tuple[str, ...] = ()
    parallel_slots: tuple[str, ...] = ()
    intercepting_marker: str | None = None
    intercepting_target: str | None = None
    note: str = ""
    confidence: float = 1.0

    @property
    def sort_key(self) -> tuple[str, str, str, str]:
        """Deterministic order that stays stable as projects are added."""
        return (self.project_dir, self.router or "", self.route or "", self.path)
