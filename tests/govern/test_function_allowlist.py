"""The two CI assertions ADR 0006 §2 mandates, in **both** directions.

The ADR's first draft had only the narrowness test. A developer widening the list to
make a gold query pass would have satisfied it — including by adding ``json_agg``,
which is B2. So the disjointness test is the load-bearing half, and it is written to
survive the specific way this could go wrong:

**Both tests parse their inputs and canonicalise them through the real
:func:`canonical_function_name`.** A string comparison would be a re-implementation of
the thing under test, and here it would be a *wrong* one: ``json_agg`` canonicalises
to ``j_s_o_n_array_agg`` under the pinned sqlglot, so ``"json_agg" in
PERMITTED_FUNCTIONS`` is ``False`` whether or not ``json_agg`` is permitted. A test
asserting that would pass while the hole was open.

Narrowness is asserted **through ``check()``**, not against the allowlist constant.
The constant cannot tell you whether a real statement clears the function layer, and
"every name in the inventory is in the set" is close to asserting a module against its
own constant — L§7's fifth authoring rule.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import sqlglot
from sqlglot import expressions as exp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("B")

GOLD = json.loads((Path(__file__).parent / "gold_functions.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def govern():
    import governed_bi.govern as module

    return module


def test_the_inventory_fixture_is_not_empty() -> None:
    """A fixture that failed to load would make every test below vacuous.

    The gold corpus lives in a sibling repository, so the inventory is committed. An
    empty or truncated commit is the failure mode that turns two mandated CI
    assertions into two green ticks.
    """
    assert GOLD["statements"] > 6_000, GOLD["statements"]
    assert len(GOLD["function_calls_by_canonical_name"]) > 25
    assert GOLD["covering_sample"], "no statements to drive check() with"


def test_not_too_narrow_every_gold_function_is_permitted_or_recorded(govern) -> None:
    """Direction one: permitted, **or** on the intentionally-absent list with a reason.

    "Or recorded" is not a loophole — it is the only honest way to ship a positive
    allowlist. ``array_agg`` appears in gold and is refused on purpose (B2), and the
    reason has to be written down next to the refusal or the next person deletes it to
    make a query pass.
    """
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


def test_the_covering_sample_clears_the_first_three_layers(govern) -> None:
    """Narrowness as an **effect**: real gold statements clear PARSE/NO_WRITE/FUNCTIONS.

    Each sample statement drives the real ``check()``. They are expected to fail
    *later* — nothing licenses their tables — so the assertion is on the layer reached,
    which is exactly the proof the ordered enum exists to give: a verdict at BINDING or
    beyond is a proof that the function layer passed.
    """
    from governed_bi.govern.functions import INTENTIONALLY_ABSENT
    from governed_bi.govern.layers import Layer

    too_narrow: list[tuple[str, str, str]] = []
    chosen: list[str] = []
    for case in GOLD["covering_sample"]:
        verdict = govern.check(case["sql"], licensed=frozenset(), default_schema=None)
        layer = verdict["failed_layer"]
        if layer is None or layer > Layer.FUNCTIONS:
            continue
        # The one acceptable early refusal: a function that is absent on purpose, with
        # its reason committed. Anything else is a false refusal nobody chose.
        named = verdict["detail"].split(" ", 1)[0]
        if verdict["reason_code"] == "r_function_not_permitted" and named in INTENTIONALLY_ABSENT:
            chosen.append(named)
            continue
        too_narrow.append((case["question_id"], verdict["reason_code"], verdict["detail"][:120]))
    assert not too_narrow, (
        f"{len(too_narrow)} of {len(GOLD['covering_sample'])} gold statements were "
        f"refused at or below the function layer for an unrecorded reason: {too_narrow}"
    )
    assert chosen, (
        "no sample statement hit an intentional absence, so the branch that "
        "distinguishes a chosen false refusal from an accidental one never executed"
    )


def test_not_too_wide_the_adversarial_set_is_disjoint_from_the_allowlist(govern) -> None:
    """Direction two: ``PERMITTED_FUNCTIONS ∩ ADVERSARIAL_SET == ∅``.

    Compared after parsing each spelling, because the SQL spelling and the canonical
    name are different strings and the interesting failures are exactly where they
    differ.
    """
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


def test_every_adversarial_spelling_is_actually_refused_by_check(govern) -> None:
    """The effect, not the set. A name absent from a set it is never compared against
    is not a refusal."""
    survivors = []
    for spelling in govern.ADVERSARIAL_SET:
        verdict = govern.check(
            f"SELECT {spelling}('x') FROM customers",
            licensed=frozenset({"customers"}),
            allowed_columns=frozenset({"customers.id"}),
        )
        if verdict["passed"]:
            survivors.append(spelling)
    assert not survivors, survivors


def test_the_digest_moves_when_the_allowlist_moves(govern) -> None:
    """Content-hashed, per ADR 0006 §13 — so widening the list moves the config hash.

    Asserted as a property of the function over two inputs rather than against a
    committed digest literal: a golden digest would have to be updated in the same
    commit that widens the list, which is the review step this is meant to force.
    """
    from governed_bi.govern import functions

    before = functions.permitted_functions_digest()
    original = functions.PERMITTED_FUNCTIONS
    try:
        functions.PERMITTED_FUNCTIONS = original | {"pg_read_file"}
        assert functions.permitted_functions_digest() != before
    finally:
        functions.PERMITTED_FUNCTIONS = original
    assert functions.permitted_functions_digest() == before


def test_the_allowlist_is_positive_an_unknown_function_is_refused(govern) -> None:
    """``exp.Anonymous`` is the whole B1 family, and gold contains none of it.

    Measured, not assumed: zero of the 6,743 gold statements contain a function that
    parses as ``Anonymous``, which is what makes refusing the shape affordable.
    """
    assert _canonical(govern, "totally_invented_function") not in govern.PERMITTED_FUNCTIONS
    verdict = govern.check(
        "SELECT totally_invented_function(1) FROM customers",
        licensed=frozenset({"customers"}),
    )
    assert verdict["reason_code"] == "r_function_not_permitted"


def test_schema_qualification_is_stripped_before_matching(govern) -> None:
    """``pg_catalog.setval`` and ``setval`` are one function; only one of them would be
    on a list somebody wrote by hand."""
    assert _canonical(govern, "pg_catalog.setval") == "setval"
    verdict = govern.check(
        "SELECT pg_catalog.setval('s', 1) FROM customers", licensed=frozenset({"customers"})
    )
    assert verdict["passed"] is False


def _canonical(govern, spelling: str) -> str:
    """Parse ``spelling`` as a call and canonicalise it through the real function.

    Tries one argument then two, because arity is fixed per function and a
    ``ParseError`` here would be mistaken for "not permitted" — a test that passes for
    the wrong reason on exactly the names it exists to check.
    """
    for args in ("'x'", "'x', 'y'"):
        try:
            tree = sqlglot.parse_one(f"SELECT {spelling}({args})", dialect="postgres")
        except Exception:  # noqa: BLE001 - arity, not a governance signal
            continue
        node = next(iter(tree.find_all(exp.Func)), None)
        if node is not None:
            return govern.canonical_function_name(node)
    raise AssertionError(f"{spelling!r} does not parse as a function call in any arity")
