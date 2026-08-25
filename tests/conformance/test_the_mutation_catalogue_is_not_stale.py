"""Every declared mutation still names things that exist, checked on every push.

``tools/mutate.py`` is the repository's answer to "a habit does not survive the person who has
it": each entry in ``tools/mutation_catalogue*.py`` is a defect somebody re-introduced by hand
once, written down so it is re-checked mechanically. But an entry is *prose about other files* —
a path, a source-text anchor, a replacement, and a pytest selection — and every one of those four
is a reference that a refactor somewhere else can break without touching this catalogue.

**Three of the seventy entries were broken when this file was written (2026-08-25), and all three
were reporting success.** ``a4-handler-not-registered`` named
``test_the_handler_is_actually_registered_for_run_creation``, renamed to
``test_both_handlers_are_actually_registered`` when the second action was added.
``d5-rival-mcnemar-returns`` and ``d11-singleton-scan-vacuous`` both named a node id in
``tests/conformance/test_register_closure.py`` — a file that still exists, which is why only
resolving the node id finds them; ``77d5f9f`` moved that test into
``test_the_lint_gates_fire_on_a_synthetic_violation.py`` and left both entries behind. Pytest
exits ``4`` for a selection it cannot resolve,
and the runner reads any non-zero exit as the suite noticing, so each of the three printed
``caught`` on every nightly having run nothing. That is this repository's open-work §3.10 shape
exactly: declared machinery with nothing on the other end, and no test failing because of it.

**Why here and not in the nightly.** The nightly is where the mutations *run*; it is the wrong
place to discover that this morning's rename made an entry vacuous, because it is on a schedule,
it cannot fire from a feature branch, and it spends a pytest selection per entry. Staleness is a
text question — no file is written, no test is run — so it is asked here, in milliseconds, on
every push. This file does not run a single mutation and is not a substitute for the nightly: it
proves the catalogue *can* prove something, not that it does.

**With positive controls, for D13.** A sweep that only asserts ``not offenders`` passes when the
predicate is broken, which is the finding that put controls in
``tests/govern/test_adversarial_suite.py``. Each way an entry can go vacuous gets a test that
plants one and checks it is reported, and one more checks the runner *refuses* such an entry
rather than applying it — so the checker is wired to the thing it protects, not merely present.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import mutate  # noqa: E402 - a sibling script, loaded the way `python tools/mutate.py` loads it

#: A live entry, borrowed as the base for every planted defect below. Taking a real one rather
#: than inventing one means the controls are mutations of something the sweep above passes, so a
#: control that fails tells you the checker is wrong and not that the fixture drifted.
LIVE = mutate.MUTATIONS[0]

#: A live entry whose selection is a **node id** rather than a whole file or directory. Needed on
#: its own because ``LIVE`` names ``tests/govern``, a directory, and the renamed-test control has
#: to plant a node id in something a node id can be in.
LIVE_NODE_ID = next(m for m in mutate.MUTATIONS if any("::" in s for s in m.tests))


def test_every_declared_mutation_still_names_things_that_exist() -> None:
    """The sweep. Reported per entry, because "3 stale" does not tell you which three."""
    stale = {m.id: mutate.why_it_proves_nothing(m) for m in mutate.MUTATIONS}
    broken = {mid: why for mid, why in stale.items() if why}
    assert not broken, "\n".join(f"{mid}: {why}" for mid, why in sorted(broken.items()))


def test_the_catalogue_is_not_empty() -> None:
    """The sweep above passes vacuously on an empty tuple, which is how an import that silently
    returns nothing would look identical to a clean catalogue."""
    assert len(mutate.MUTATIONS) >= 60, f"only {len(mutate.MUTATIONS)} entries; the catalogue grows"


def test_every_mutation_id_is_unique() -> None:
    """``--only`` selects by **substring** over ids, so two entries sharing one would run both
    and a reader asking for one would silently get two answers under one name."""
    ids = [m.id for m in mutate.MUTATIONS]
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    assert not duplicated, f"{duplicated} are declared more than once"


@pytest.mark.parametrize(
    ("what", "field", "value"),
    [
        ("a target that moved", "path", "src/governed_bi/no_such_module.py"),
        ("an anchor no longer in the file", "anchor", "zzz_this_text_is_in_no_file_zzz"),
        ("a named test file that moved", "tests", ("tests/no_such_file.py",)),
        (
            "a named test that was renamed",
            "tests",
            (f"{LIVE_NODE_ID.tests[0].split('::')[0]}::test_this_name_was_never_defined",),
        ),
        ("a node id given for a directory", "tests", ("tests/govern::test_anything",)),
        ("no named tests at all", "tests", ()),
    ],
)
def test_a_planted_defect_is_reported(what: str, field: str, value: object) -> None:
    """One control per way an entry goes vacuous. ``what`` is only there to name the failure."""
    planted = dataclasses.replace(LIVE, **{field: value})
    assert mutate.why_it_proves_nothing(planted), f"{what} was not reported"


def test_a_replacement_identical_to_the_anchor_is_reported() -> None:
    """Its own test rather than a row above, because the planted value has to be *derived* from
    the entry: a no-op mutation writes the original bytes back, so the suite passes and the runner
    would report the tests as bad when the entry is."""
    planted = dataclasses.replace(LIVE, replacement=LIVE.anchor)
    assert mutate.why_it_proves_nothing(planted)


def test_a_node_id_with_a_parametrisation_suffix_still_resolves() -> None:
    """The negative half: a checker that fired on everything would pass every test above and be
    useless. ``[param]`` suffixes are real in this catalogue — ``d5`` and ``d11`` both carry one —
    so stripping them has to work, or fixing those two would have traded one false pass for a
    false failure."""
    parametrised = [
        m for m in mutate.MUTATIONS if any("[" in s for s in m.tests)
    ]
    assert parametrised, "no entry carries a parametrised node id; this test now proves nothing"
    for m in parametrised:
        assert not mutate.why_it_proves_nothing(m), m.id


def test_the_runner_refuses_a_vacuous_entry_instead_of_running_it() -> None:
    """The wire. A checker nothing calls is a preference.

    ``_apply`` must report a stale entry as a **survivor** without touching the tree: the
    reference it names has moved, so whether the defect would still be caught is unknown, and
    ``caught`` is the one answer that must not be printed. Safe to call because the entry cannot
    pass the guard, so no file is written and no pytest subprocess starts.
    """
    planted = dataclasses.replace(LIVE, anchor="zzz_this_text_is_in_no_file_zzz")
    survived, detail = mutate._apply(planted)  # noqa: SLF001 - the guard is what is under test
    assert survived, "a stale entry reported as caught is a defect re-introduced against nothing"
    assert "anchor appears 0 times" in detail, detail
