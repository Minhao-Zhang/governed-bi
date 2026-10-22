"""Load the suite and its fictional world. See ADR 0006. A case that does not explain itself fails to load, and
``bypass`` is closed on :data:`BYPASSES` so a typo cannot count toward nothing."""

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
from .layers import RULES, Layer
from .pipeline import spellings_for
from .policy import BI_SCOPE_RULE_ID, GUARD_RULE_IDS

__all__ = [
    "SUITE_FILE",
    "CASE_FAMILIES",
    "CASE_KINDS",
    "ENFORCERS",
    "BYPASSES",
    "AdversarialCase",
    "GuardCase",
    "BypassClaim",
    "AdversarialWorld",
    "AdversarialSuite",
    "WorldFixture",
    "load_adversarial_suite",
    "build_world_fixture",
]

#: Beside this module, for ``registry/configs.toml``'s reason: a claim that ships with the
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
    }
)

CASE_KINDS: frozenset[str] = frozenset({"attack", "benign"})

#: Where the expectation is read. ``pipeline`` covers a ``prepare()`` step ``check()`` on
#: the raw string would pass. Both still run; an attack must leave ``prepare()`` with
#: nothing to execute.
ENFORCERS: frozenset[str] = frozenset({"check", "pipeline"})

#: ADR 0006's Context table, B1–B10. Closed for the reason every other case field is closed:
#: ``bypass`` was the one field taken raw, so ``B4`` mistyped as ``B44`` was a case that still
#: loaded, still ran, and silently stopped counting towards the bypass it was written for.
BYPASSES: frozenset[str] = frozenset(f"B{n}" for n in range(1, 11))


@dataclass(frozen=True, slots=True)
class BypassClaim:
    """Data, not a sentence in the suite file: prose cannot be checked against the cases."""

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
    #: the false-refusal rate and reported; does not fail the check. Empty means "must pass".
    known_false_refusal: str = ""

    @property
    def is_attack(self) -> bool:
        return self.kind == "attack"


@dataclass(frozen=True, slots=True)
class AdversarialWorld:
    """One world for every case. A per-case world would let a failing case be repaired by moving its fixture."""

    default_schema: str
    #: ``{schema}.{table} -> declared column spellings``, in declaration order.
    tables: Mapping[str, tuple[str, ...]]
    retrieved_tables: frozenset[str]
    excluded: frozenset[str]
    suspect: frozenset[str]


@dataclass(frozen=True, slots=True)
class GuardCase:
    """A question, not a statement. A separate table: folding it into statement cases would make every field optional.
    """

    id: str
    kind: str
    #: The raw text. Use :attr:`text` — it is what the rule sees.
    question: str
    why: str
    origin: str
    #: ``question`` repeated this many times, so ``g_length`` can be exercised without an
    #: 8,001-character payload in the file. The benign long case stays readable as prose.
    repeat: int = 1
    #: The ``GUARD_RULES`` id expected to fire. Required on an attack, forbidden on a benign
    #: case. There is no layer half to declare: ``guard()`` screens the input before any layer
    #: runs, so the rule id *is* the whole attribution.
    expect_rule: str | None = None
    #: As :attr:`AdversarialCase.known_false_refusal`: a benign refusal somebody accepted,
    #: with the reason. Counted and reported; does not fail the check.
    known_false_refusal: str = ""

    @property
    def is_attack(self) -> bool:
        return self.kind == "attack"

    @property
    def text(self) -> str:
        """The string :func:`~governed_bi.govern.guard.guard` sees, :attr:`question` times :attr:`repeat`."""
        return self.question * self.repeat


@dataclass(frozen=True, slots=True)
class AdversarialSuite:
    version: str
    world: AdversarialWorld
    cases: tuple[AdversarialCase, ...]
    #: One row per member of :data:`BYPASSES`, checked against the cases at load.
    bypasses: Mapping[str, BypassClaim]
    #: The prompt-injection half. Empty is refused at load for the same reason a suite with no
    #: benign controls is: ADR 0006 OQ3 held the five deterministic rules off until each had
    #: an adversarial number, and this is where that number comes from.
    guard_cases: tuple[GuardCase, ...] = ()


@dataclass(frozen=True, slots=True)
class WorldFixture:
    """World resolved into the corpus, retrieved set, and spelling maps the two entry points take."""

    corpus: AnalystCorpus
    retrieved_tables: frozenset[str]
    spellings: Mapping[str, str]
    ambiguous: frozenset[str]
    by_table: Mapping[str, Mapping[str, str]]
    default_schema: str


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

    retrieved_tables = frozenset(str(x) for x in body.get("retrieved_tables", ()))
    unknown = sorted(retrieved_tables - set(resolved))
    if unknown:
        raise ValueError(
            f"[world].retrieved_tables names {unknown}, which no [world.tables] entry declares"
        )
    if retrieved_tables == set(resolved):
        raise ValueError(
            "[world] lists every table it declares as retrieved, so the TABLES layer has nothing to "
            "refuse and a checker that retrieved every table would score perfectly"
        )

    return AdversarialWorld(
        default_schema=str(body.get("default_schema") or ""),
        tables=resolved,
        retrieved_tables=retrieved_tables,
        excluded=frozenset(str(x) for x in body.get("excluded", ())),
        suspect=frozenset(str(x) for x in body.get("suspect", ())),
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


def _parse_guard_case(body: Mapping[str, Any], index: int) -> GuardCase:
    """One ``[[guard_case]]``. Same bar as :func:`_parse_case`: it must explain itself."""
    where = f"[[guard_case]] #{index}"
    case_id = _require(body, "id", where)
    where = f"guard case {case_id!r}"

    kind = _require(body, "kind", where)
    if kind not in CASE_KINDS:
        raise ValueError(f"{where}: kind {kind!r} is not one of {sorted(CASE_KINDS)}")

    rule = body.get("expect_rule")
    if kind == "attack":
        if not isinstance(rule, str):
            raise ValueError(
                f"{where}: an attack declares expect_rule. 'It is refused' is not the claim — "
                "`g_length` catching a payload written to test `g_instruction_override` means "
                "the rule meant to catch it did not, and a suite that only asked 'was it "
                "blocked' would report that as working."
            )
        if rule not in GUARD_RULE_IDS:
            raise ValueError(
                f"{where}: expect_rule {rule!r} is not one of {sorted(GUARD_RULE_IDS)}. "
                f"{BI_SCOPE_RULE_ID!r} is deliberately excluded: it is a model call, and this "
                "suite runs with no model, no network and no I/O."
            )
    elif rule is not None:
        raise ValueError(
            f"{where}: a benign case must not declare an expected refusal; it is expected to "
            "be allowed, and naming a rule for it would make a false refusal look intended"
        )

    repeat = body.get("repeat", 1)
    if not isinstance(repeat, int) or isinstance(repeat, bool) or repeat < 1:
        raise ValueError(
            f"{where}: repeat must be a positive integer, got {repeat!r}. It is the one piece "
            "of machinery in a data file and it exists only so `g_length` can be exercised "
            "without an 8,001-character literal; a zero or a float is a typo, not a payload."
        )

    return GuardCase(
        id=case_id,
        kind=kind,
        question=_require(body, "question", where),
        why=_require(body, "why", where),
        origin=_require(body, "origin", where),
        expect_rule=rule if isinstance(rule, str) else None,
        known_false_refusal=str(body.get("known_false_refusal", "")),
        repeat=repeat,
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


def _assert_bypass_coverage_is(
    cases: Sequence[AdversarialCase], bypasses: Mapping[str, BypassClaim]
) -> None:
    """Both directions: a statement surface needs an attack, and an attack cannot name a bypass with none."""
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
    _assert_bypass_coverage_is(cases, bypasses)

    raw_guard = data.get("guard_case")
    if not isinstance(raw_guard, list) or not raw_guard:
        raise ValueError(
            f"{SUITE_FILE.name}: no [[guard_case]] tables — the prompt-injection half is gone. "
            "The five deterministic rules in govern/guard.py ship enabled on the served "
            "surface, and ADR 0006 OQ3 says a rule ships only with an adversarial number "
            "behind it. This is where that number comes from."
        )
    guard_cases = tuple(_parse_guard_case(body, i) for i, body in enumerate(raw_guard, 1))
    # One id space across both halves: they share a report, and two rows keyed the same is a
    # rate with a row missing from it.
    for guard_case in guard_cases:
        if guard_case.id in seen:
            raise ValueError(f"duplicate case id {guard_case.id!r}; ids key the report")
        seen.add(guard_case.id)

    return AdversarialSuite(
        version=str(data.get("version") or ""),
        world=_parse_world(world),
        cases=cases,
        bypasses=bypasses,
        guard_cases=guard_cases,
    )


def load_adversarial_suite(path: Path | None = None) -> AdversarialSuite:
    """Parse the suite. Raises on any case that does not explain itself."""
    source = path or SUITE_FILE
    return _parse_suite(tomllib.loads(source.read_text(encoding="utf-8")))


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
    """Real table and column assets. A key-only corpus has no columns, so canonicalisation is untested."""
    corpus = for_analyst(_world_assets(world))
    spellings, ambiguous, by_table = spellings_for(corpus, world.retrieved_tables)
    return WorldFixture(
        corpus=corpus,
        retrieved_tables=world.retrieved_tables,
        spellings=spellings,
        ambiguous=ambiguous,
        by_table=by_table,
        default_schema=world.default_schema,
    )


def _assert_the_suite_has_both_halves(cases: Sequence[AdversarialCase]) -> None:
    """Refusing every statement scores a perfect block rate on attacks alone. Asserted at import, not only under pytest.
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


def _assert_the_guard_half_has_both_halves_and_covers_every_rule(
    cases: Sequence[GuardCase],
) -> None:
    """Controls, plus an attack per rule. These five ship enabled, so a set-level rate is not enough. See ADR 0006."""
    attacks = [c for c in cases if c.is_attack]
    benign = [c for c in cases if not c.is_attack]
    if not attacks or not benign:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"the prompt-injection half has {len(attacks)} attacks and {len(benign)} benign "
            "controls; a block rate with no control measures nothing"
        )
    uncovered = sorted(GUARD_RULE_IDS - {c.expect_rule for c in attacks if c.expect_rule})
    if uncovered:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"no attack is aimed at {uncovered}. Those rules are enabled on the served "
            "surface with nothing measuring them, which is the state OQ3 held them out of."
        )


_SUITE = load_adversarial_suite()
_assert_the_suite_has_both_halves(_SUITE.cases)
_assert_the_guard_half_has_both_halves_and_covers_every_rule(_SUITE.guard_cases)
del _SUITE
