"""The adversarial governance suite, as data (``adversarial.toml``).

open-work.md 3.11 recorded the gap this closes — until this suite existed it read "the layer
stack, the allowlist and the scope gate have no adversarial evaluation, so what governance buys
has no number", and it now reports the first number instead. This module loads the cases and resolves
the fictional world they read against; :mod:`.adversarial_run` runs them and reports the
numbers. Both are deliberately model-free, network-free and credential-free — the whole layer
stack is deterministic, so measuring it must be too.

Loading is where the suite is kept honest. A case with no ``why`` and no ``origin`` fails to
load, an attack that does not name the layer *and* the rule it expects fails to load, and a
layer that disagrees with ``layers.RULES`` about who owns that rule fails to load. Each of
those is a way a suite rots into a list nobody can tell a deliberate probe from a leftover in.

``bypass`` is closed on the same argument and was the last field that was not: every other case
field failed to load on a typo while ``B4`` mistyped as ``B44`` loaded, ran, and counted towards
nothing. It is checked against :data:`BYPASSES`, and the *coverage* claim — which of ADR 0006's
ten bypasses this file can aim a statement at — is the ``[bypass.*]`` tables rather than a
sentence, so :func:`_assert_bypass_coverage_is_honest` can hold it to the cases that exist.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..corpus.analyst import AnalystCorpus, for_analyst
from ..corpus.schema import (
    Asset,
    ColumnAsset,
    Governance,
    Reliability,
    ReliabilityStatus,
    TableAsset,
)
from ..ports import Grant, PredicateEnforcement, Reach, RowPredicate
from .layers import RULES, Layer
from .pipeline import spellings_for

__all__ = [
    "SUITE_FILE",
    "CASE_FAMILIES",
    "CASE_KINDS",
    "ENFORCERS",
    "BYPASSES",
    "AdversarialCase",
    "BypassClaim",
    "AdversarialWorld",
    "AdversarialSuite",
    "WorldFixture",
    "load_adversarial_suite",
    "build_world_fixture",
]

#: Beside this module, for ``register/arms.toml``'s reason: a claim that ships with the
#: library must travel with the distribution rather than be reached by climbing out of the
#: package, which resolves only from a source checkout.
SUITE_FILE = Path(__file__).resolve().parent / "adversarial.toml"

#: What a case is *about*. Closed, so a typo becomes a load failure instead of a family of one
#: that nobody notices is under-covered.
CASE_FAMILIES: frozenset[str] = frozenset(
    {
        "parse",
        "write",
        "function",
        "binding",
        "column",
        "table",
        "injection",
        "spelling",
        # ADR 0012. Its own family rather than folded into `table` / `column`, so the report's
        # family table shows how thin or thick the authorization half is instead of hiding it
        # inside the licensing counts -- which is the conflation the ADR exists to end.
        "authorization",
    }
)

CASE_KINDS: frozenset[str] = frozenset({"attack", "benign"})

#: Which entry point the expectation is read from. ``check`` is the layer stack alone;
#: ``pipeline`` is a step ``prepare()`` runs around it — the pre-NFKC encoding gate, or
#: canonicalisation — where ``check()`` on the raw string would pass. Both run for every case
#: regardless, and an attack must leave ``prepare()`` with nothing to execute either way.
ENFORCERS: frozenset[str] = frozenset({"check", "pipeline"})

#: ADR 0006's Context table, B1–B10. Closed for the reason every other case field is closed:
#: ``bypass`` was the one field taken raw, so ``B4`` mistyped as ``B44`` was a case that still
#: loaded, still ran, and silently stopped counting towards the bypass it was written for.
BYPASSES: frozenset[str] = frozenset(f"B{n}" for n in range(1, 11))


@dataclass(frozen=True, slots=True)
class BypassClaim:
    """What this suite claims about one of ADR 0006's ten bypasses.

    The claim used to live in a sentence at the top of ``adversarial.toml`` and in a report:
    *"B1/B2/B4/B5/B6 are statement-shaped and are here; B3/B7/B8/B9/B10 have no SQL surface."*
    Prose cannot be checked, and a suite that quietly covered five of ten while being called
    "the bypass suite" is the failure ADR 0006's own bypass list was written to stop. As data,
    :func:`_assert_bypass_coverage_is_honest` can hold it to the cases actually present.
    """

    id: str
    #: Whether the bypass can be expressed as a statement this suite can hand to ``check()``.
    #: ``True`` requires at least one attack aimed at it; ``False`` forbids one.
    sql_surface: bool
    why: str
    #: Where the bypass is covered instead, for ``sql_surface = false``. Free text, and read by
    #: a human — but a missing one fails to load, so "nowhere" has to be written down.
    pinned_by: str


@dataclass(frozen=True, slots=True)
class AdversarialCase:
    """One case. ``why`` and ``origin`` are mandatory — see :func:`_parse_case`."""

    id: str
    kind: str
    family: str
    sql: str
    why: str
    origin: str
    bypass: str | None = None
    expect_layer: Layer | None = None
    expect_rule: str | None = None
    enforced_by: str = "check"
    #: A refusal of a benign case that somebody chose to accept, with the reason. Counted in
    #: the false-refusal rate and reported; does not fail the gate. Empty means "must pass".
    known_false_refusal: str = ""

    @property
    def is_attack(self) -> bool:
        return self.kind == "attack"


@dataclass(frozen=True, slots=True)
class AdversarialWorld:
    """The fixed fictional lake every case reads against.

    One world, declared once. A per-case world would let a failing case be repaired by moving
    its fixture, which turns an acceptance criterion into a description of the code.
    """

    default_schema: str
    #: ``{schema}.{table} -> declared column spellings``, in declaration order.
    tables: Mapping[str, tuple[str, ...]]
    licensed: frozenset[str]
    excluded: frozenset[str]
    suspect: frozenset[str]
    #: ADR 0012. What the world's principal may read, independent of what retrieval licensed.
    #: The two sets overlap and neither contains the other, on purpose — see
    #: :func:`_parse_world`.
    authorized: frozenset[str] = frozenset()
    denied_columns: frozenset[str] = frozenset()
    row_predicates: tuple[RowPredicate, ...] = ()

    def grant(self) -> Grant:
        """The world's authorization, as the value an ``AccessPolicy`` would return."""
        return Grant(
            reach=Reach.listed,
            tables=self.authorized,
            denied_columns=self.denied_columns,
            row_predicates=self.row_predicates,
        )


@dataclass(frozen=True, slots=True)
class AdversarialSuite:
    version: str
    world: AdversarialWorld
    cases: tuple[AdversarialCase, ...]
    #: One row per member of :data:`BYPASSES`, checked against the cases at load.
    bypasses: Mapping[str, BypassClaim]


@dataclass(frozen=True, slots=True)
class WorldFixture:
    """:class:`AdversarialWorld` resolved into the arguments the two entry points take."""

    corpus: AnalystCorpus
    licensed: frozenset[str]
    spellings: Mapping[str, str]
    ambiguous: frozenset[str]
    by_table: Mapping[str, Mapping[str, str]]
    default_schema: str
    #: The world's grant, carried here so :mod:`.adversarial_run` puts it on the policy it
    #: runs with. A caller-supplied policy cannot override it: the grant is part of the world
    #: the cases were written against, exactly like ``licensed``.
    grant: Grant = Grant()


# ── loading ───────────────────────────────────────────────────────────────────


def _require(body: Mapping[str, Any], key: str, where: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{where}: `{key}` is missing or empty. Every case states why it exists and where "
            "it came from; an unexplained case cannot be told from a leftover, so nobody "
            "deletes it and nobody trusts it."
        )
    return value


def _parse_world(body: Mapping[str, Any]) -> AdversarialWorld:
    tables = body.get("tables")
    if not isinstance(tables, Mapping) or not tables:
        raise ValueError("[world.tables] is missing or empty")
    resolved: dict[str, tuple[str, ...]] = {}
    for table_id, columns in tables.items():
        if len(table_id.split(".")) != 2:
            raise ValueError(f"[world.tables] key {table_id!r} is not schema.table")
        if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
            raise ValueError(f"[world.tables] {table_id!r} must be a list of column names")
        resolved[table_id] = tuple(columns)

    licensed = frozenset(str(x) for x in body.get("licensed", ()))
    unknown = sorted(licensed - set(resolved))
    if unknown:
        raise ValueError(f"[world].licensed names {unknown}, which no [world.tables] entry declares")
    if licensed == set(resolved):
        raise ValueError(
            "[world] licenses every table it declares, so the TABLES layer has nothing to "
            "refuse and a checker that licensed everything would score perfectly"
        )

    authorized = frozenset(str(x) for x in body.get("authorized", ()))
    unknown = sorted(authorized - set(resolved))
    if unknown:
        raise ValueError(
            f"[world].authorized names {unknown}, which no [world.tables] entry declares"
        )
    # The same argument the licence check makes, one rule over (ADR 0012 §3). A world whose
    # authorized set covers everything it licenses gives `r_table_not_authorized` nothing to
    # fire on, and an access layer that authorized everything would score perfectly.
    if not (licensed - authorized):
        raise ValueError(
            "[world] authorizes every table it licenses, so the authorization half of the "
            "TABLES layer has nothing to refuse. Declare a table that retrieval found and "
            "this principal may not read -- that is the case ADR 0012 exists for."
        )
    denied = frozenset(str(x) for x in body.get("denied_columns", ()))
    declared_columns = {
        f"{table}.{column}".lower()
        for table, columns in resolved.items()
        for column in columns
    }
    unknown = sorted(key for key in denied if key.lower() not in declared_columns)
    if unknown:
        raise ValueError(
            f"[world].denied_columns names {unknown}, which no [world.tables] entry declares"
        )

    predicates: list[RowPredicate] = []
    for table, spec in (body.get("row_predicate") or {}).items():
        if table not in resolved:
            raise ValueError(
                f"[world.row_predicate] names {table!r}, which no [world.tables] entry declares"
            )
        if not isinstance(spec, Mapping) or not isinstance(spec.get("expression"), str):
            raise ValueError(f"[world.row_predicate.{table}] needs a string `expression`")
        predicates.append(
            RowPredicate(
                table=str(table),
                expression=str(spec["expression"]),
                enforcement=PredicateEnforcement(
                    spec.get("enforcement", PredicateEnforcement.refuse.value)
                ),
            )
        )

    return AdversarialWorld(
        default_schema=str(body.get("default_schema") or ""),
        tables=resolved,
        licensed=licensed,
        excluded=frozenset(str(x) for x in body.get("excluded", ())),
        suspect=frozenset(str(x) for x in body.get("suspect", ())),
        authorized=authorized,
        denied_columns=denied,
        row_predicates=tuple(sorted(predicates, key=lambda p: p.table)),
    )


def _parse_case(body: Mapping[str, Any], index: int) -> AdversarialCase:
    where = f"[[case]] #{index}"
    case_id = _require(body, "id", where)
    where = f"case {case_id!r}"

    kind = _require(body, "kind", where)
    if kind not in CASE_KINDS:
        raise ValueError(f"{where}: kind {kind!r} is not one of {sorted(CASE_KINDS)}")
    family = _require(body, "family", where)
    if family not in CASE_FAMILIES:
        raise ValueError(f"{where}: family {family!r} is not one of {sorted(CASE_FAMILIES)}")
    enforced_by = str(body.get("enforced_by", "check"))
    if enforced_by not in ENFORCERS:
        raise ValueError(f"{where}: enforced_by {enforced_by!r} is not one of {sorted(ENFORCERS)}")

    rule = body.get("expect_rule")
    layer_name = body.get("expect_layer")
    if kind == "attack":
        if not isinstance(rule, str) or not isinstance(layer_name, str):
            raise ValueError(
                f"{where}: an attack declares expect_layer and expect_rule. 'It is refused' is "
                "not the claim — refusing for the wrong reason means the rule meant to catch it "
                "did not, and that is a bypass with a green tick on it."
            )
        if rule not in RULES:
            raise ValueError(f"{where}: expect_rule {rule!r} is not a rule in govern.layers.RULES")
        if RULES[rule] is not Layer[layer_name]:
            raise ValueError(
                f"{where}: expect_layer is {layer_name} and RULES says {rule!r} belongs to "
                f"{RULES[rule].name}. The pair is declared twice on purpose so a typo in either "
                "half fails to load rather than pinning the wrong attribution."
            )
    elif rule is not None or layer_name is not None:
        raise ValueError(
            f"{where}: a benign case must not declare an expected refusal; it is expected to be "
            "allowed, and declaring a layer for it would make a false refusal look intended"
        )

    bypass = body.get("bypass")
    if bypass is not None and bypass not in BYPASSES:
        raise ValueError(
            f"{where}: bypass {bypass!r} is not one of {sorted(BYPASSES, key=_bypass_order)}. "
            "It is ADR 0006's Context table and it is closed: an unrecognised id is a case that "
            "loads, runs, and counts towards nothing, which is how `B4` becomes `B44` invisibly."
        )

    return AdversarialCase(
        id=case_id,
        kind=kind,
        family=family,
        sql=_require(body, "sql", where),
        why=_require(body, "why", where),
        origin=_require(body, "origin", where),
        bypass=bypass,
        expect_layer=Layer[layer_name] if isinstance(layer_name, str) else None,
        expect_rule=rule if isinstance(rule, str) else None,
        enforced_by=enforced_by,
        known_false_refusal=str(body.get("known_false_refusal", "")),
    )


def _bypass_order(name: str) -> tuple[int, str]:
    """``B10`` after ``B9``. Sorting these as strings is how a report reads B10 before B2."""
    return (int(name[1:]), name) if name[1:].isdigit() else (10**6, name)


def _parse_bypasses(body: Any) -> dict[str, BypassClaim]:
    """The ``[bypass.*]`` tables: one row per member of :data:`BYPASSES`, no more and no fewer."""
    if not isinstance(body, Mapping) or not body:
        raise ValueError(
            f"{SUITE_FILE.name}: no [bypass.*] tables. Every bypass in ADR 0006's list declares "
            "whether it has an SQL surface this suite can aim a statement at; a suite that names "
            "some of the ten and is described as covering them is the claim nobody can check."
        )
    unknown = sorted(set(body) - BYPASSES, key=_bypass_order)
    if unknown:
        raise ValueError(f"[bypass] declares {unknown}, which ADR 0006's B1-B10 does not name")
    absent = sorted(BYPASSES - set(body), key=_bypass_order)
    if absent:
        raise ValueError(
            f"[bypass] says nothing about {absent}. Silence is what made the coverage claim "
            "unfalsifiable; declare `sql_surface` and where it is pinned instead."
        )

    out: dict[str, BypassClaim] = {}
    for name in sorted(body, key=_bypass_order):
        row = body[name]
        where = f"[bypass.{name}]"
        if not isinstance(row, Mapping):
            raise ValueError(f"{where} is not a table")
        surface = row.get("sql_surface")
        if not isinstance(surface, bool):
            raise ValueError(f"{where}: `sql_surface` must be a boolean, got {surface!r}")
        out[name] = BypassClaim(
            id=name,
            sql_surface=surface,
            why=_require(row, "why", where),
            pinned_by=_require(row, "pinned_by", where),
        )
    return out


def _assert_bypass_coverage_is_honest(
    cases: Sequence[AdversarialCase], bypasses: Mapping[str, BypassClaim]
) -> None:
    """The declaration and the cases must agree, in both directions.

    Either half alone is decorative. Without the first, a bypass can claim a statement surface
    and have no statement aimed at it — the coverage gap the prose version hid. Without the
    second, an attack can be filed under a bypass the suite says it cannot express, which makes
    the ``sql_surface = false`` rows meaningless and the "pinned elsewhere" pointer a lie.
    """
    aimed = {case.bypass for case in cases if case.bypass and case.is_attack}
    filed = {case.bypass for case in cases if case.bypass}

    unaimed = sorted(
        (name for name, claim in bypasses.items() if claim.sql_surface and name not in aimed),
        key=_bypass_order,
    )
    if unaimed:
        raise ValueError(
            f"{unaimed} declare `sql_surface = true` and no attack case names them. Either write "
            "the statement or say the bypass has no SQL surface and where it is pinned instead."
        )

    surfaceless = sorted(
        (name for name in filed if name in bypasses and not bypasses[name].sql_surface),
        key=_bypass_order,
    )
    if surfaceless:
        raise ValueError(
            f"{surfaceless} declare `sql_surface = false` and a case names them anyway. The "
            "statement exists, so the declaration is wrong -- and `pinned_by` is now pointing "
            "readers away from a case that is right here."
        )


def _parse_suite(data: Mapping[str, Any]) -> AdversarialSuite:
    raw_cases = data.get("case")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError(f"{SUITE_FILE.name}: no [[case]] tables")
    cases = tuple(_parse_case(body, i) for i, body in enumerate(raw_cases, 1))
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise ValueError(f"duplicate case id {case.id!r}; ids key the report")
        seen.add(case.id)
    world = data.get("world")
    if not isinstance(world, Mapping):
        raise ValueError(f"{SUITE_FILE.name}: no [world] table")
    bypasses = _parse_bypasses(data.get("bypass"))
    _assert_bypass_coverage_is_honest(cases, bypasses)
    return AdversarialSuite(
        version=str(data.get("version") or ""),
        world=_parse_world(world),
        cases=cases,
        bypasses=bypasses,
    )


def load_adversarial_suite(path: Path | None = None) -> AdversarialSuite:
    """Parse the suite. Raises on any case that does not explain itself."""
    source = path or SUITE_FILE
    return _parse_suite(tomllib.loads(source.read_text(encoding="utf-8")))


# ── the world, resolved ───────────────────────────────────────────────────────


def _world_assets(world: AdversarialWorld) -> list[Asset]:
    assets: list[Asset] = []
    for table_id, columns in world.tables.items():
        schema, name = table_id.split(".")
        assets.append(
            TableAsset(
                id=table_id,
                schema=schema,
                physical_name=name,
                summary=f"{name} in {schema}",
                columns=tuple(f"{table_id}.{column}" for column in columns),
            )
        )
        for column in columns:
            key = f"{table_id}.{column}".lower()
            assets.append(
                ColumnAsset(
                    id=f"{table_id}.{column}",
                    schema=schema,
                    parent_table=name,
                    physical_name=column,
                    summary=f"{column} of {name}",
                    governance=(
                        Governance(excluded=True, reason="declared by the suite world", by="human")
                        if key in world.excluded
                        else Governance()
                    ),
                    reliability=(
                        Reliability(status=ReliabilityStatus.suspect)
                        if key in world.suspect
                        else Reliability()
                    ),
                )
            )
    return assets


def build_world_fixture(world: AdversarialWorld) -> WorldFixture:
    """Resolve the world into a corpus, a licence and the spelling maps.

    Built through :func:`~governed_bi.corpus.analyst.for_analyst` over real ``TableAsset`` /
    ``ColumnAsset`` objects rather than through ``analyst_corpus_from_keys``, which makes no
    table assets: ``spellings_for`` reads ``TableAsset.columns``, so a key-only corpus would
    hand ``prepare()`` an empty spelling map and leave canonicalisation — where both of
    open-work.md 3.2a's defects live — untested.
    """
    corpus = for_analyst(_world_assets(world))
    spellings, ambiguous, by_table = spellings_for(corpus, world.licensed)
    return WorldFixture(
        corpus=corpus,
        licensed=world.licensed,
        spellings=spellings,
        ambiguous=ambiguous,
        by_table=by_table,
        default_schema=world.default_schema,
        grant=world.grant(),
    )


def _assert_the_suite_has_both_halves(cases: Sequence[AdversarialCase]) -> None:
    """Import-time guard: a suite with no benign half measures nothing.

    ``def check(...): return {"passed": False}`` scores a perfect bypass rate on attacks alone,
    so the benign half is not a nicety — it is the other side of the only trade the layer stack
    makes. Asserted here rather than only in a test because a driver run outside pytest must
    not report a bypass rate of zero over a suite that lost its controls.
    """
    attacks = sum(1 for c in cases if c.is_attack)
    benign = len(cases) - attacks
    if not attacks or not benign:  # pragma: no cover - import-time guard
        raise AssertionError(f"the suite has {attacks} attacks and {benign} benign controls")
    if benign * 2 < attacks:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"{benign} benign controls against {attacks} attacks. The false-refusal rate is the "
            "companion metric the positive allowlist is only honest with (ADR 0006 §2), and a "
            "denominator this thin cannot carry it."
        )


_assert_the_suite_has_both_halves(load_adversarial_suite().cases)
