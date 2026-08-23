"""An edit to a ``body`` must land the value the caller asked for, in the style the file used.

**The defect this pins.** ``_render_block`` hard-coded the chomping indicator ``-`` on the
argument that "the original spans measured here all use ``>-``". Measured on the served corpus:
**1,750 files use ``body: >`` against 137 using ``body: >-``**, and inline columns are 473 to 37.
The claim was inverted, and it is the majority style of one of the two editable fields.

Three consequences, all reproduced by a reviewer on real corpus files:

1. A clip-style (``>``) block resolves to text **ending in a newline**. ``read_field`` returns it,
   the newline is invisible in a textarea, and ``_wrap`` splits on spaces only — so the ``\\n``
   rides inside a "word" and is emitted as an unindented continuation line. The file stops parsing.
2. Even on a well-formed edit, the rewritten block header flips ``>`` to ``>-`` and drops the
   trailing newline. On the file the docstring cites: a ``summary`` edit is 4 changed lines and the
   value matches; a ``body`` edit is **12 changed lines and the value does not**.
3. A changed value means ``lifecycle._content_is_there`` cannot match, so a patch that landed
   correctly reports ``superseded`` — the exact failure the module's own field restrictions exist
   to prevent.

So every test here is a **round trip**: read the field, change it the way a person would, apply,
re-read, and require the value back. That is the property, and it is the one nothing asserted —
the only fixture in ``test_an_edit_does_not_rewrite_the_file.py`` uses ``>-`` for every block, so
the hard-coded ``-`` was green against the 137-file minority.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("yaml")

from governed_bi.corpus.patch import apply_edit, read_field  # noqa: E402

ASSET = "sales.orders"

#: Every block style YAML offers, because the corpus uses more than one and the writer must not
#: normalise between them. `>` and `|` differ in whether newlines fold; `-`, none and `+` differ in
#: what happens to the trailing ones.
STYLES = [">", ">-", "|", "|-"]


def _file(tmp_path: Path, *, style: str, body: str = "Grain is one order.") -> Path:
    root = tmp_path / "corpus" / "sales" / "tables"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "tbl_sales_orders.yaml"
    path.write_text(
        f"""asset_type: table
id: {ASSET}
schema: sales
physical_name: orders
summary: orders holds one row per placed order in the sales schema.
body: {style}
  {body}
""",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _round_trip(path: Path, *, field_path: str, becomes: str) -> str:
    """Apply an edit, write the result, and read the field back. Returns what landed."""
    was = read_field(path, asset_id=ASSET, field_path=field_path)
    after = apply_edit(
        path, asset_id=ASSET, field_path=field_path, was=was, becomes=becomes
    )
    path.write_text(after, encoding="utf-8", newline="\n")
    return read_field(path, asset_id=ASSET, field_path=field_path)


@pytest.mark.parametrize("style", [">", ">-", "|", "|-"])
def test_a_body_edit_lands_the_value_it_was_given(tmp_path: Path, style: str) -> None:
    """The round trip, for every block style the corpus uses.

    ``>`` is the one that was broken and it is the majority style; the others are here so a fix
    that special-cases one of them fails.
    """
    path = _file(tmp_path, style=style)
    becomes = "Grain is one order. Questions about a purchase read this table."

    landed = _round_trip(path, field_path="body", becomes=becomes)
    assert landed == becomes, (
        f"a {style!r} body edit landed {landed!r} instead of the value it was given"
    )


@pytest.mark.parametrize("style", [">", ">-", "|", "|-"])
def test_an_edit_that_keeps_the_trailing_shape_keeps_the_block_header(
    tmp_path: Path, style: str
) -> None:
    """**The header is derived from the value, and that is correct.**

    An earlier version of this test passed a ``becomes`` with no trailing newline into a clip-style
    (``>``) block and demanded the header not change. The writer emitted ``>-``, which is the
    indicator that makes the block resolve back to a value with no trailing newline -- so the header
    was telling the truth and the test was wrong.

    The property the module actually owes: an edit that **preserves the value's trailing shape**
    preserves the header. That is what a person editing prose does, and it is what keeps a one-word
    change a one-word diff. The header entering the diff means the trailing shape changed, which is
    a change worth showing.
    """
    path = _file(tmp_path, style=style)
    was = read_field(path, asset_id=ASSET, field_path="body")
    # Built from `was` so the trailing newlines are whatever the file's style implies.
    core = was.rstrip("\n")
    trailing = was[len(core) :]
    becomes = f"{core} And one more sentence.{trailing}"

    landed = _round_trip(path, field_path="body", becomes=becomes)
    assert landed == becomes

    after = path.read_text(encoding="utf-8")
    assert f"body: {style}\n" in after, (
        f"the file used {style!r} and the edit preserved the trailing shape, so the header should "
        f"be untouched. It now reads: "
        f"{[line for line in after.splitlines() if line.startswith('body:')]}"
    )


@pytest.mark.parametrize(
    ("style", "trailing", "expected_header"),
    [
        (">", "", ">-"),
        (">", "\n", ">"),
        (">-", "\n", ">"),
        ("|", "", "|-"),
        ("|-", "\n", "|"),
    ],
)
def test_the_header_says_what_the_value_actually_ends_with(
    tmp_path: Path, style: str, trailing: str, expected_header: str
) -> None:
    """The indicator is a statement about the value, so it must match the value.

    Hard-coding it to ``-`` -- which is what shipped -- made every clip-style block resolve to a
    different string than it held, on 1,750 files. Deriving it is what makes the round trip exact,
    and this is the table that pins the derivation.
    """
    path = _file(tmp_path, style=style)
    becomes = f"Grain is one order.{trailing}"

    assert _round_trip(path, field_path="body", becomes=becomes) == becomes
    headers = [
        line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("body:")
    ]
    assert headers == [f"body: {expected_header}"], (
        f"a value ending in {trailing!r} needs the {expected_header!r} indicator to read back "
        f"unchanged; the file says {headers}"
    )


def test_reading_a_clip_body_and_writing_it_straight_back_changes_nothing(
    tmp_path: Path,
) -> None:
    """The identity edit, which is the cheapest possible check and the one that fails first.

    ``read_field`` on a ``>`` block returns text ending in ``\\n``. Feeding that value straight
    back as ``becomes`` must be a no-op; before the fix it produced a file that did not parse.
    """
    path = _file(tmp_path, style=">")
    before = path.read_text(encoding="utf-8")

    was = read_field(path, asset_id=ASSET, field_path="body")
    after = apply_edit(path, asset_id=ASSET, field_path="body", was=was, becomes=was)

    import yaml

    yaml.safe_load(after)  # must not raise
    path.write_text(after, encoding="utf-8", newline="\n")
    assert read_field(path, asset_id=ASSET, field_path="body") == was
    assert after == before, "an identity edit rewrote the file"


def test_appending_a_sentence_to_a_clip_body_still_parses(tmp_path: Path) -> None:
    """What a person actually does, on the majority style, with the invisible newline in play.

    A steward copies the current text into a textarea, adds a sentence, and submits. If
    ``read_field`` handed them a trailing newline they cannot see, the value they submit carries it
    in the middle — and the file stops loading **after** the commit.
    """
    import yaml

    path = _file(tmp_path, style=">")
    was = read_field(path, asset_id=ASSET, field_path="body")
    becomes = was + "Questions about a purchase read this table."

    after = apply_edit(path, asset_id=ASSET, field_path="body", was=was, becomes=becomes)
    document = yaml.safe_load(after)
    assert isinstance(document, dict), "the edited file no longer parses as a mapping"
    assert document["body"].strip().endswith("read this table.")


def test_a_body_edit_touches_only_the_body(tmp_path: Path) -> None:
    """The module's whole reason to exist: a one-field edit is a small diff. A header flip makes it
    two lines larger than the change, on every one of the 1,750 files that use ``>``."""
    path = _file(tmp_path, style=">")
    before = path.read_text(encoding="utf-8").splitlines()
    was = read_field(path, asset_id=ASSET, field_path="body")
    core = was.rstrip("\n")
    _round_trip(
        path, field_path="body", becomes=f"{core} One added sentence.{was[len(core):]}"
    )
    after = path.read_text(encoding="utf-8").splitlines()

    changed = [
        index
        for index in range(max(len(before), len(after)))
        if (before[index] if index < len(before) else None)
        != (after[index] if index < len(after) else None)
    ]
    header = next(i for i, line in enumerate(before) if line.startswith("body:"))
    assert header not in changed, "the block header is in the diff and the caller edited the text"
    assert all(index > header for index in changed), (
        f"lines outside the body changed: {changed}, body header at {header}"
    )


def test_a_summary_edit_is_unaffected(tmp_path: Path) -> None:
    """The control. `summary` is a plain scalar and was always correct; a fix to the block writer
    must not touch it."""
    path = _file(tmp_path, style=">")
    becomes = "orders holds one row per placed order, one per purchase, in the sales schema."

    assert _round_trip(path, field_path="summary", becomes=becomes) == becomes
    assert "body: >\n" in path.read_text(encoding="utf-8")


def test_a_multi_line_body_keeps_its_paragraph_breaks(tmp_path: Path) -> None:
    """A folded block with a blank line means a paragraph break, which folds to ``\\n``. Losing it
    silently rewrites prose the model reads."""
    root = tmp_path / "corpus" / "sales" / "tables"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "tbl_sales_orders.yaml"
    path.write_text(
        f"""asset_type: table
id: {ASSET}
schema: sales
physical_name: orders
summary: orders holds one row per placed order in the sales schema.
body: >
  Grain is one order.

  A second paragraph about the same table.
""",
        encoding="utf-8",
        newline="\n",
    )

    was = read_field(path, asset_id=ASSET, field_path="body")
    assert "\n" in was.strip(), "the fixture does not actually carry a paragraph break"
    landed = _round_trip(path, field_path="body", becomes=was)
    assert landed == was, "a paragraph break did not survive an identity edit"

def _literal_file(tmp_path: Path, *, style: str = "|") -> Path:
    """A literal block with real newlines in it, which is the only shape that can catch a rewrap."""
    root = tmp_path / "corpus" / "sales" / "tables"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "tbl_sales_orders.yaml"
    path.write_text(
        f"""asset_type: table
id: {ASSET}
schema: sales
physical_name: orders
summary: orders holds one row per placed order in the sales schema.
body: {style}
  Grain is one order.
  One row per purchase.
  Joined to customers on customer_id.
""",
        encoding="utf-8",
        newline="\n",
    )
    return path


def test_a_literal_block_keeps_every_newline_it_had(tmp_path: Path) -> None:
    """A literal block is literal. Re-wrapping one joins lines the author separated, and the
    fixtures above cannot see it because a one-line block wraps to itself.

    This is what ``|`` means and what ``>`` does not: the newlines are the value.
    """
    path = _literal_file(tmp_path)
    was = read_field(path, asset_id=ASSET, field_path="body")
    assert was.count("\n") >= 3, f"the fixture has no newlines to lose: {was!r}"

    landed = _round_trip(path, field_path="body", becomes=was)
    assert landed == was, "an identity edit on a literal block folded its newlines away"
    assert landed.count("\n") == was.count("\n")


def test_an_edit_to_a_literal_block_adds_a_line_and_keeps_the_rest(tmp_path: Path) -> None:
    path = _literal_file(tmp_path)
    was = read_field(path, asset_id=ASSET, field_path="body")
    becomes = was.rstrip("\n") + "\nRefunds are a separate table.\n"

    landed = _round_trip(path, field_path="body", becomes=becomes)
    assert landed == becomes
    assert landed.splitlines()[:3] == was.splitlines()[:3], "the untouched lines moved"


# ── values that cannot be written faithfully ─────────────────────────────────
#
# The three channels a `becomes` can carry a YAML-breaking payload on, all of them invisible in a
# textarea. Before `apply_edit` checked its own output, a tab and a trailing colon produced a file
# that did not parse, and an interior newline in a plain scalar was quoted into a DIFFERENT string
# -- and `tools/export_bundle.py` shipped all three with exit 0, because it never parsed the text
# it wrote either.


@pytest.mark.parametrize(
    ("becomes", "why"),
    [
        ("orders holds one row per placed order:", "a trailing colon opens a mapping"),
        ("orders holds one row\tper placed order in sales", "a tab cannot start a YAML token"),
        ("orders holds one row per placed order\x08 in sales", "a control character"),
    ],
    ids=["trailing-colon", "tab", "control-char"],
)
def test_a_value_that_cannot_be_written_is_refused_rather_than_written(
    tmp_path: Path, becomes: str, why: str
) -> None:
    """Refused, and the file left alone. The alternative is what shipped: a bundle an engineer
    applies, after which the corpus stops loading."""
    from governed_bi.corpus.patch import UnwritableValue

    path = _file(tmp_path, style=">")
    before = path.read_bytes()
    was = read_field(path, asset_id=ASSET, field_path="summary")

    with pytest.raises(UnwritableValue):
        apply_edit(path, asset_id=ASSET, field_path="summary", was=was, becomes=becomes)
    assert path.read_bytes() == before, f"the file was written despite {why}"


def test_a_newline_in_a_plain_scalar_is_refused_because_it_would_land_as_a_space(
    tmp_path: Path,
) -> None:
    """The quietest of the three and the one the review found first.

    A plain scalar carrying ``\n`` is written as a quoted single line, which **parses** and resolves
    to the same text with the newline turned into a space. So the patch lands a different value than
    it was given, ``lifecycle._content_is_there`` cannot match it, and a change that shipped
    correctly reports ``superseded``. The UI made this worse by rendering ``+0 -0 words`` and telling
    the steward the field already held the replacement.
    """
    import yaml

    from governed_bi.corpus.patch import UnwritableValue

    path = _file(tmp_path, style=">")
    was = read_field(path, asset_id=ASSET, field_path="summary")
    becomes = "orders holds one row\nper placed order in the sales schema."

    with pytest.raises(UnwritableValue) as refusal:
        apply_edit(path, asset_id=ASSET, field_path="summary", was=was, becomes=becomes)
    assert "not the" in str(refusal.value), "the refusal should name the value that would land"

    # And the reason it is worth refusing rather than accepting: the file WOULD have parsed.
    quoted = yaml.safe_load('summary: "orders holds one row\nper placed order"')
    assert "\n" not in quoted["summary"], "the premise of this test no longer holds"


def test_an_interior_newline_is_writable_in_a_folded_block(tmp_path: Path) -> None:
    """The control that keeps the refusal narrow. A folded block CAN carry a paragraph break, so
    the same value that is unwritable in a plain scalar is fine in a ``body``. Refusing both would
    be refusing the field the design exists to edit."""
    path = _file(tmp_path, style=">")
    was = read_field(path, asset_id=ASSET, field_path="body")
    becomes = f"{was.rstrip(chr(10))}\nA second paragraph.{was[len(was.rstrip(chr(10))):]}"

    assert _round_trip(path, field_path="body", becomes=becomes) == becomes
