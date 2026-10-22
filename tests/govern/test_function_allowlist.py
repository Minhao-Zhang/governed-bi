"""Both directions, through the real canonical name. ``json_agg`` does not compare equal to its canonical spelling. See
ADR 0006."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import sqlglot
from sqlglot import expressions as exp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


GOLD = json.loads((Path(__file__).parent / "gold_functions.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def govern():
    import governed_bi.govern as module

    return module


def test_the_inventory_fixture_is_not_empty() -> None:
    """An empty committed inventory would turn both assertions green."""
    assert GOLD["statements"] > 6_000, GOLD["statements"]
    assert len(GOLD["function_calls_by_canonical_name"]) > 25
    assert GOLD["covering_sample"], "no statements to drive check() with"


def test_not_too_narrow_every_gold_function_is_permitted_or_recorded(govern) -> None:
    """Permitted, or recorded as refused on purpose. An unrecorded gold function can be added to make a query pass."""
    from governed_bi.govern.functions import INTENTIONALLY_ABSENT

    unexplained = sorted(
        name
        for name in GOLD["function_calls_by_canonical_name"]
        if name not in govern.PERMITTED_FUNCTIONS and name not in INTENTIONALLY_ABSENT
    )
    assert not unexplained, (
        f"{unexplained} appear in gold SQL, are not permitted, and have no recorded "
        "reason. Either permit them or record why not — silence here is a false refusal "
        "nobody chose."
    )
    for name, reason in INTENTIONALLY_ABSENT.items():
        assert len(reason) > 40, f"{name}'s reason is too short to be a reason"


def test_the_covering_sample_clears_the_first_three_layers(govern, check) -> None:
    """A result past the function layer is proof that layer passed. The samples are expected to fail later, on tables.
    """
    from governed_bi.govern.functions import INTENTIONALLY_ABSENT
    from governed_bi.govern.layers import Layer

    too_narrow: list[tuple[str, str, str]] = []
    chosen: list[str] = []
    for case in GOLD["covering_sample"]:
        result = check(case["sql"], retrieved_tables=frozenset(), default_schema=None)
        layer = result["failed_layer"]
        if layer is None or layer > Layer.FUNCTIONS:
            continue
        # The one acceptable early refusal: a function that is absent on purpose, with
        # its reason committed. Anything else is a false refusal nobody chose.
        named = result["detail"].split(" ", 1)[0]
        if result["reason_code"] == "r_function_not_permitted" and named in INTENTIONALLY_ABSENT:
            chosen.append(named)
            continue
        too_narrow.append((case["question_id"], result["reason_code"], result["detail"][:120]))
    assert not too_narrow, (
        f"{len(too_narrow)} of {len(GOLD['covering_sample'])} gold statements were "
        f"refused at or below the function layer for an unrecorded reason: {too_narrow}"
    )
    assert chosen, (
        "no sample statement hit an intentional absence, so the branch that "
        "distinguishes a chosen false refusal from an accidental one never executed"
    )


def test_not_too_wide_the_adversarial_set_is_disjoint_from_the_allowlist(govern) -> None:
    """Compared after parsing. The SQL spelling and the canonical name are different strings."""
    permitted: list[tuple[str, str]] = []
    for spelling in govern.ADVERSARIAL_SET:
        canonical = _canonical(govern, spelling)
        if canonical in govern.PERMITTED_FUNCTIONS:
            permitted.append((spelling, canonical))
    assert not permitted, (
        f"adversarial functions on the allowlist: {permitted}. Each of these either "
        "reads outside the database or emits a whole row with no Column nodes."
    )


@pytest.mark.parametrize("spelling", ["json_agg", "row_to_json", "to_jsonb", "array_agg"])
def test_the_canonical_name_of_a_whole_row_emitter_is_not_its_spelling(govern, spelling) -> None:
    """The reason the test above parses instead of comparing strings.

    If these ever became identical, a string-comparison disjointness test would start
    working — and until then it would silently compare two things that cannot be equal.
    """
    canonical = _canonical(govern, spelling)
    assert canonical not in govern.PERMITTED_FUNCTIONS


def test_every_adversarial_spelling_is_refused_by_check(govern, check) -> None:
    """The effect, not the set. A name absent from a set it is never compared against
    is not a refusal."""
    survivors = []
    for spelling in govern.ADVERSARIAL_SET:
        result = check(
            f"SELECT {spelling}('x') FROM customers",
            retrieved_tables=frozenset({"customers"}),
            allowed_columns=frozenset({"customers.id"}),
        )
        if result["passed"]:
            survivors.append(spelling)
    assert not survivors, survivors


def test_the_digest_moves_when_the_allowlist_moves(govern) -> None:
    """Widening the list changes the digest. A committed golden digest would move in the same commit. See ADR 0006."""
    from governed_bi.govern import functions

    before = functions.permitted_functions_digest()
    original = functions.PERMITTED_FUNCTIONS
    try:
        functions.PERMITTED_FUNCTIONS = original | {"pg_read_file"}
        assert functions.permitted_functions_digest() != before
    finally:
        functions.PERMITTED_FUNCTIONS = original
    assert functions.permitted_functions_digest() == before


def test_the_allowlist_is_positive_an_unknown_function_is_refused(govern, check) -> None:
    """``exp.Anonymous`` is the whole B1 family, and gold contains none of it.

    Measured, not assumed: zero of the 6,743 gold statements contain a function that
    parses as ``Anonymous``, which is what makes refusing the shape affordable.
    """
    assert _canonical(govern, "totally_invented_function") not in govern.PERMITTED_FUNCTIONS
    result = check(
        "SELECT totally_invented_function(1) FROM customers",
        retrieved_tables=frozenset({"customers"}),
    )
    assert result["reason_code"] == "r_function_not_permitted"


def test_schema_qualification_is_stripped_before_matching(govern, check) -> None:
    """``pg_catalog.setval`` and ``setval`` are one function; only one of them would be
    on a list somebody wrote by hand."""
    assert _canonical(govern, "pg_catalog.setval") == "setval"
    result = check(
        "SELECT pg_catalog.setval('s', 1) FROM customers", retrieved_tables=frozenset({"customers"})
    )
    assert result["passed"] is False


def _canonical(govern, spelling: str) -> str:
    """A parse error here would be mistaken for "not permitted" on the names this helper exists to check."""
    for args in ("'x'", "'x', 'y'"):
        try:
            tree = sqlglot.parse_one(f"SELECT {spelling}({args})", dialect="postgres")
        except Exception:  # noqa: BLE001 - arity, not a governance signal
            continue
        node = next(iter(tree.find_all(exp.Func)), None)
        if node is not None:
            return govern.canonical_function_name(node)
    raise AssertionError(f"{spelling!r} does not parse as a function call in any arity")
