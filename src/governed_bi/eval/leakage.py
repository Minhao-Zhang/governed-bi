"""Which scored questions the curator could have answered from memory.

``_assert_train_test_disjoint`` checks that no *question id* appears in both splits.
That is the coarse form of leakage and it is genuinely absent. The fine form is not:
a test question whose gold SQL is structurally the same statement as some train
question's, in the same schema. Measured over the obfuscated BIRD test split, **115 of 1200 questions (9.6%)**
have such a twin (RETIRED figure, for the record: 246 of 2030 = 12.1%, from the
pre-refilter pool — the dataset is filtered upstream, so a pool figure quoted from
an older split describes a different experiment), and the rate is far from uniform — one
schema is at 46%.

That matters because of what the ladder's arms read. ``seeded`` derives its seed
directly from train gold SQL and ``curated`` runs an agent over train. On a question
whose answer already exists in train, verbatim modulo literals, an EX gain is
consistent with recall and with generalisation, and EX alone cannot tell them apart.
Nothing downstream could see it: the id-level check passes, the SME brief's leakage
assertion is about a different artifact, and the delta lands in the headline.

So the flag is stamped per row and EX is reported both ways. The defensible headline
is the twin-free stratum; the twin stratum is worth reporting beside it, because a
pipeline that recalls well is not useless — it is just not the claim.

Deliberately NOT a gate. Twins are a property of the benchmark, not a fault in the
run, and refusing to score them would discard an eighth of the split and change the
denominator every published BIRD number uses.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .bird_loader import load_bird_items
from .sql_diff import is_frozen_constant

#: Field carrying the obfuscated gold SQL. The un-obfuscated ``sql_sqlite`` would
#: match across schemas that were renamed, inflating the twin rate with pairs the
#: curator never saw in this form.
_GOLD_FIELD = "sql_rename"

# ``''`` is SQL's escape for a quote inside a literal, so a naive ``'[^']*'``
# splits ``'O''Gallagher'`` into TWO literals and the canonical form stops matching
# the same statement written with an unescaped name. 203 golds across the two splits
# contain ``''``. Understates the rate, which is the direction that flatters us.
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
_WHITESPACE = re.compile(r"\s+")


def canonical_sql(sql: str | None) -> str:
    """A structure-only form of a statement, for twin detection.

    Literals are blanked and whitespace collapsed; identifiers, clauses and shape are
    kept. Two statements share a canonical form when they differ only in the constants
    plugged into them — which is exactly the case where having seen one tells you how
    to write the other.

    Deliberately *not* the AST normaliser in ``sql_diff``. That one exists to explain
    why an answer was wrong and is tolerant of orderings that change results; this one
    only has to answer "is this the same query with different numbers in it", and a
    textual canonical form is both cheaper and easier to defend when the twin rate is
    quoted alongside a headline.
    """
    if not sql:
        return ""
    text = _STRING_LITERAL.sub("'?'", str(sql).lower())
    text = _NUMBER.sub("?", text)
    # ``.strip()`` twice, around the semicolon: a statement written ``... ;`` left a
    # trailing space once the terminator came off, so it never matched the same
    # statement written ``...;``. That silently UNDERSTATED the twin rate, which is
    # the direction that quietly flatters the result.
    return _WHITESPACE.sub(" ", text).strip().rstrip(";").strip()


#: Gold that is a literal ``VALUES(...)`` constant. Blank the constant and every such
#: statement collapses onto ONE canonical form, so they all "twin" each other — 64 of
#: an unfiltered 246, a quarter of the total. They are also already excluded from
#: ``ex_gradeable``, so they can never reach ``ex_no_twin`` or ``ex_twin``: counting
#: them made the quoted rate and the stratified metric describe different populations,
#: and put three schemas in ``worst_dbs`` that carry no risk at all (one of them at a
#: headline-grabbing 46% that is 14% once they are removed). Detection is
#: ``sql_diff.is_frozen_constant``.


def is_gradeable_gold(sql: str | None) -> bool:
    """Can a generator ever match this gold through a result hash?

    Thin wrapper: non-empty SQL that is not a frozen ``VALUES(...)`` constant.
    Detection lives only in ``sql_diff.is_frozen_constant`` (one regex). Empty /
    ``None`` gold is not gradeable even though it is not frozen.
    """
    return bool(sql) and not is_frozen_constant(sql)


def is_gradeable_eval_row(
    row: Mapping[str, Any], *, gold_sql: Mapping[str, str] | None = None
) -> bool:
    """Whether a scored row belongs in the shared ``ex_gradeable`` denominator.

    Excludes frozen-``VALUES`` gold **and** order-sensitive gold. Used by both
    ``run_datalake._summarise_rows`` and ``analysis.gradeable_report`` so the same
    field name cannot mean two different populations across ``summary.json`` and
    ``analysis.json``.

    Prefer per-row stamps (``gold_frozen``, ``gold_order_sensitive``). Fall back to
    detecting frozen gold from ``gold_sql`` for archived rows that predate the stamp;
    order-sensitivity has no SQL-only detector here, so an unstamped row is treated
    as not order-sensitive rather than guessed.
    """
    if row.get("gold_frozen") is not None:
        frozen = bool(row["gold_frozen"])
    elif gold_sql is not None:
        frozen = is_frozen_constant(gold_sql.get(str(row.get("question_id"))))
    else:
        frozen = False
    if frozen:
        return False
    if row.get("gold_order_sensitive"):
        return False
    return True


def train_twin_question_ids(
    dataset_dir: Path | str, db_id: str, *, split: str = "test"
) -> set[str]:
    """Question ids in ``split`` whose gold SQL has a structural twin in ``train``.

    Empty when the schema has no train questions — that is "nothing to have leaked
    from", not "checked and clean", and the caller distinguishes them by also reading
    the train count.
    """
    train = load_bird_items(dataset_dir, db_id, split="train", gold_sql_field=_GOLD_FIELD)
    train_forms = {
        canonical_sql(item.sql) for item in train if is_gradeable_gold(item.sql)
    }
    train_forms.discard("")
    if not train_forms:
        return set()
    scored = load_bird_items(dataset_dir, db_id, split=split, gold_sql_field=_GOLD_FIELD)
    return {
        str(item.question_id)
        for item in scored
        if item.question_id
        and is_gradeable_gold(item.sql)
        and canonical_sql(item.sql) in train_forms
    }


#: Filename the obfuscation repo ships its own EX exclusions in.
_EXCLUSIONS_FILE = "order_sensitive_qids.json"


def ungradeable_question_ids(dataset_dir: Path | str) -> dict[str, set[str]]:
    """Question ids the dataset itself says not to score, keyed by reason.

    ``order_sensitive_qids.json`` is shipped by the obfuscation repo and its own note
    reads: *"order_sensitive: gold has LIMIT-without-total-order or float aggregate;
    returns a different-but-valid result on the decoy instances ... exec_failed:
    pre-existing degenerate BIRD gold (>200k rows / 60s timeout). Exclude both from
    cross-variant EX."*

    Nothing in this repo read it. 25 of the 2030 test questions (1.23%) are on the
    ``order_sensitive`` list, and a different-but-valid result hashes differently, so
    each was scored **wrong** for every arm. That is uniform across arms and therefore
    harmless to a delta — but it silently depresses every absolute EX, including the
    one quoted beside the ``oracle_sql`` ceiling, which is the comparison the whole
    ladder is read against.

    Returns ``{}`` when the file is absent: an older data checkout has nothing to say
    here, and inventing an empty exclusion set would be the same as reading one.
    """
    path = Path(dataset_dir) / _EXCLUSIONS_FILE
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: {str(q) for q in value}
        for key, value in raw.items()
        if isinstance(value, list)
    }


def twin_report(
    dataset_dir: Path | str,
    db_ids: list[str],
    *,
    split: str = "test",
    only_ids: set[str] | None = None,
) -> dict[str, object]:
    """Per-schema and pooled twin counts, plus the id set the driver stamps rows from.

    ``twin_ids`` is returned rather than recomputed per arm: the canonical form of
    every train statement in the pool is a few thousand regex substitutions, and doing
    it once per arm would put it on the critical path of a five-pass run for no reason.
    """
    twin_ids: set[str] = set()
    by_db: dict[str, dict[str, int]] = {}
    n_scored = 0
    dbs_without_train: list[str] = []
    for db in db_ids:
        train = load_bird_items(
            dataset_dir, db, split="train", gold_sql_field=_GOLD_FIELD
        )
        scored = [
            it
            for it in load_bird_items(
                dataset_dir, db, split=split, gold_sql_field=_GOLD_FIELD
            )
            if is_gradeable_gold(it.sql)
            and (only_ids is None or str(it.question_id) in only_ids)
        ]
        if not train:
            dbs_without_train.append(db)
        db_twins = train_twin_question_ids(dataset_dir, db, split=split)
        if only_ids is not None:
            db_twins &= only_ids
        twin_ids |= db_twins
        n_scored += len(scored)
        by_db[db] = {"n": len(scored), "n_twin": len(db_twins)}
    return {
        "n_scored": n_scored,
        "n_twin": len(twin_ids),
        "twin_rate": (len(twin_ids) / n_scored) if n_scored else None,
        # Named so a reader can go straight to the schemas that carry the risk; the
        # pooled rate hides a 46% schema inside a 12% average.
        "worst_dbs": [
            db
            for db, c in sorted(
                by_db.items(),
                key=lambda kv: -(kv[1]["n_twin"] / kv[1]["n"] if kv[1]["n"] else 0),
            )[:10]
            if c["n_twin"]
        ],
        "by_db": by_db,
        # A schema with no train questions cannot have leaked, and must not be read as
        # having been checked and found clean.
        "dbs_without_train": dbs_without_train,
        "twin_ids": twin_ids,
    }
