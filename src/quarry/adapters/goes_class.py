"""NOAA GOES soft X-ray flare class inline vocabulary (`goes-class:`).

All five documented classes (A, B, C, M, X — decades of 0.1–0.8 nm peak flux from 1e-8 W/m^2)
and validated decimal-multiplier labels (`M5.2`, `X17.2`, `C3`) resolve offline without HTTP.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from refs import Registry

from quarry.catalog import Resource, Route
from quarry.errors import QuarryError
from quarry.http import Client
from quarry.ids import Curie, parse
from quarry.search import completeness
from quarry.terms import Release, Term, TermCoverage

SCALE_VERSION = "NOAA-SWPC"
SCALED_RE = re.compile(r"^([ABCMX])([1-9][0-9]*(?:\.[0-9]+)?)$")


@dataclass(frozen=True)
class BaseClass:
    letter: str
    decade: Decimal
    decade_text: str
    definition: str


BASE_CLASSES: tuple[BaseClass, ...] = (
    BaseClass(
        "A",
        Decimal("1e-8"),
        "1e-8",
        "NOAA GOES soft X-ray flare class A: 0.1-0.8 nm peak flux 1e-8 to < 1e-7 W/m^2 (multiplier 1.0 to < 10).",
    ),
    BaseClass(
        "B",
        Decimal("1e-7"),
        "1e-7",
        "NOAA GOES soft X-ray flare class B: 0.1-0.8 nm peak flux 1e-7 to < 1e-6 W/m^2 (multiplier 1.0 to < 10).",
    ),
    BaseClass(
        "C",
        Decimal("1e-6"),
        "1e-6",
        "NOAA GOES soft X-ray flare class C: 0.1-0.8 nm peak flux 1e-6 to < 1e-5 W/m^2 (multiplier 1.0 to < 10).",
    ),
    BaseClass(
        "M",
        Decimal("1e-5"),
        "1e-5",
        "NOAA GOES soft X-ray flare class M: 0.1-0.8 nm peak flux 1e-5 to < 1e-4 W/m^2 "
        "(multiplier 1.0 to < 10; NOAA radio blackout R1 at M1, R2 at M5).",
    ),
    BaseClass(
        "X",
        Decimal("1e-4"),
        "1e-4",
        "NOAA GOES soft X-ray flare class X: 0.1-0.8 nm peak flux >= 1e-4 W/m^2 "
        "(open-ended multiplier >= 1.0; NOAA radio blackout R3 at X1, R4 at X10, R5 at X20).",
    ),
)
BY_LETTER: dict[str, BaseClass] = {item.letter: item for item in BASE_CLASSES}


def _format_flux(value: Decimal) -> str:
    normalized = value.normalize()
    mantissa, exp = f"{normalized:e}".split("e")
    if "." in mantissa:
        mantissa = mantissa.rstrip("0").rstrip(".")
    return f"{mantissa}e{int(exp)}"


def _r_scale(letter: str, multiplier: Decimal) -> str | None:
    if letter == "M":
        return "R2 (Moderate, >= M5)" if multiplier >= Decimal("5") else "R1 (Minor, >= M1)"
    if letter == "X":
        if multiplier >= Decimal("20"):
            return "R5 (Extreme, >= X20)"
        if multiplier >= Decimal("10"):
            return "R4 (Severe, >= X10)"
        return "R3 (Strong, >= X1)"
    return None


def _parse_local(local: str) -> tuple[BaseClass, Decimal | None]:
    """Validate a GOES flare class local ID against the NOAA SWPC decade scale."""
    if local in BY_LETTER:
        return BY_LETTER[local], None
    match = SCALED_RE.fullmatch(local)
    if match is None:
        raise QuarryError(
            "malformed",
            f"goes-class:{local}: expected A, B, C, M, or X with an optional multiplier >= 1 (e.g. M5.2, X17.2)",
        )
    letter, raw_mult = match.group(1), match.group(2)
    multiplier = Decimal(raw_mult)
    if letter != "X" and multiplier >= Decimal("10"):
        raise QuarryError(
            "malformed",
            f"goes-class:{local}: multiplier for class {letter} must be >= 1 and < 10",
        )
    return BY_LETTER[letter], multiplier


def _definition(local: str, base: BaseClass, multiplier: Decimal | None) -> str:
    if multiplier is None:
        return base.definition
    flux = _format_flux(multiplier * base.decade)
    r_note = _r_scale(base.letter, multiplier)
    suffix = f"; NOAA radio blackout {r_note}" if r_note else ""
    return (
        f"NOAA GOES soft X-ray flare class {local}: 0.1-0.8 nm peak flux {flux} W/m^2 "
        f"({local[1:]} x {base.decade_text} W/m^2{suffix})."
    )


def _try_parse_query_class(query: str) -> tuple[str, BaseClass, Decimal | None] | None:
    """Parse an exact GOES class label (bare `M5.2` or CURIE `goes-class:M5.2`), or None."""
    candidate = query
    if ":" in candidate:
        prefix, _, rest = candidate.partition(":")
        if prefix.casefold() != "goes-class" or not rest:
            return None
        candidate = rest
    try:
        base, multiplier = _parse_local(candidate)
    except QuarryError:
        return None
    return candidate, base, multiplier


class GoesClass:
    def __init__(self, resource: Resource):
        self.resource = resource
        self.route: Route = resource.routes[0]

    def _release(self) -> Release:
        return Release(self.resource.id, SCALE_VERSION, None)

    def release(self, client: Client) -> tuple[Release, list[str]]:
        return self._release(), []

    def show(self, client: Client, registry: Registry, curie: Curie) -> tuple[Term, Release, list[str]]:
        if registry.canonical(curie.prefix) != "goes-class":
            raise QuarryError("unsupported_id", f"{curie} is not owned by {self.resource.id}")
        canonical = parse(str(curie), registry)
        base, multiplier = _parse_local(canonical.local)
        term = Term(
            id=str(canonical),
            label=canonical.local,
            resource=self.resource.id,
            definition=_definition(canonical.local, base, multiplier),
        )
        return term, self._release(), []

    def ancestors(self, client: Client, registry: Registry,
                  curie: Curie) -> tuple[Term, list[Term], Release, list[str]]:
        raise QuarryError(
            "unsupported_role",
            f"{self.resource.id} is a vocabulary, not an ontology; it has no is_a ancestors",
        )

    def search(self, client: Client, registry: Registry, text: str, *, exact: bool,
               limit: int) -> tuple[TermCoverage, list[Term], Release, list[str]]:
        words = text.split()
        if not words:
            raise QuarryError("unsupported_filter", "a term search needs at least one word")
        if limit < 1:
            raise ValueError("limit is at least 1")
        query = " ".join(words)
        needle = query.casefold()
        words_cf = [w.casefold() for w in words]

        matched: list[Term] = []
        seen: set[str] = set()

        def add_term(local: str) -> None:
            canonical = str(parse(f"goes-class:{local}", registry))
            if canonical not in seen:
                seen.add(canonical)
                matched.append(Term(id=canonical, label=local, resource=self.resource.id))

        parsed = _try_parse_query_class(query)
        if parsed is not None:
            add_term(parsed[0])

        for item in BASE_CLASSES:
            curie_text = f"goes-class:{item.letter}"
            if exact:
                if item.letter.casefold() == needle or curie_text.casefold() == needle:
                    add_term(item.letter)
            else:
                haystack = f"{curie_text} {item.definition}".casefold()
                if all(w in haystack for w in words_cf):
                    add_term(item.letter)

        rows = matched[:limit]
        total = len(matched)
        complete = completeness(total=total, returned=len(rows), limit=limit, short_page=False)
        native = f"inline&q={query}&exact={'true' if exact else 'false'}"
        return TermCoverage(self.resource.id, native, exact, total, len(rows), complete), rows, self._release(), []


def make(resource: Resource) -> GoesClass:
    if not resource.routes:
        raise QuarryError("catalog_invalid", f"{resource.id}: needs a documentation route")
    return GoesClass(resource)
