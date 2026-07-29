"""Simulated SME for the eval ladder (the curated_sme arm).

An eval-only :class:`~governed_bi.curator.clarifications.Responder` briefed with
domain meaning from BIRD database_description CSVs + train question/evidence.
Never receives gold SQL or held-out test questions.
"""

from __future__ import annotations

import csv
import difflib
import io
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, Sequence

from .. import prompts

if TYPE_CHECKING:
    from ..config import Settings
    from ..eval.dataset import EvalItem
    from ..llm import ChatClient

logger = logging.getLogger("governed_bi.curator")

#: Case-**sensitive** on purpose. The guard is looking for gold SQL, and all 30,492
#: gold statements in the obfuscated BIRD splits spell the keyword ``SELECT``. Matching
#: case-insensitively also matched English prose: european_football_2's
#: ``Player_Attributes.csv`` says "implies that the player will select the attack
#: actions he will join in", so the moment the dev-tree descriptions became reachable
#: that schema started failing the leakage assert and being dropped from the pool —
#: after its baseline, seeded and curated corpora had already been built and paid for.
_SELECT_LEAK_RE = re.compile(r"\bSELECT\b")

#: The sanitiser's copy, deliberately case-**in**sensitive. It runs over *model
#: output*, where a lowercase ``select * from ...`` is ordinary and must still be
#: stripped before it can reach provenance. Only the leakage guard above may fold
#: case away, because only it runs over English prose. One shared pattern served
#: both and could not be right for both.
_SELECT_SQL_RE = re.compile(r"\bSELECT\b", re.IGNORECASE)
_SQL_FENCE_RE = re.compile(r"```(?:sql)?\s*.*?```", re.IGNORECASE | re.DOTALL)

#: A leftover fence marker on its own line, once :data:`_SQL_FENCE_RE` has taken the
#: block it opened — or when the model never closed it, in which case the marker has
#: no pair and would otherwise survive into the answer as ``` ```sql ```.
_FENCE_LINE_RE = re.compile(r"^\s*`{3,}\s*\w*\s*$")

#: The *continuation* lines of a query whose first line matched :data:`_SELECT_SQL_RE`.
#: An unfenced statement wraps across lines, and only its first line names the
#: keyword, so matching the keyword alone would leave ``FROM t`` / ``WHERE x = 1``
#: standing in the answer. Consulted **only** while already inside a query run, which
#: is what makes it safe to list words as ordinary as ``AND``, ``OR`` and ``ON``: a
#: prose line that happens to open with one of them is only ever dropped if the line
#: immediately above it was SQL.
_SQL_CONT_RE = re.compile(
    r"""^\s*(?:
        [);,]+\s*$                                  # a lone closing paren / terminator
      | --                                          # a SQL comment
      | (?:FROM|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET|UNION|INTERSECT
         |EXCEPT|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS|NATURAL|ON|AND|OR)\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

#: The fixed rules block inside the otherwise data-assembled SME brief. Only this
#: block is a prompt variant; the rest of the brief is BIRD column descriptions
#: and train evidence, which the registry has no business versioning.
_SME_SYSTEM_RULES = prompts.get("sme_rules").text


def build_sme_brief(
    db_description_dir: Path | str,
    train_items: Sequence["EvalItem"],
    *,
    max_train_questions: int = 40,
    system_rules: str | None = None,
    rename_map: Mapping[str, str] | None = None,
) -> str:
    """Build the Simulated SME system brief (no gold SQL, no test items).

    Reads every ``*.csv`` under ``db_description_dir`` (BIRD layout:
    ``original_column_name,column_name,column_description,data_format,value_description``)
    and appends a sample of train questions + evidence for domain flavour.

    ``rename_map`` is the db's ``schema_rename_map.json`` entry (BIRD identifier ->
    physical identifier). It is what makes the brief *usable* on the obfuscated
    databases, and omitting it is the bug it exists to fix: the description CSVs
    are the untouched originals, so on the 55 of 69 schemas that carry a real
    rename the SME would talk about ``PurchaseDate`` while the agent is choosing
    between ``kaufdatum``, ``bewertungsdatum`` and ``transaktionsdatum``. Every
    statement stays semantically true and none of it can land on a column, which
    is worse than useless — it reads as a working SME arm. Pass the map (empty is
    fine for identity-rename schemas); ``None`` means "these CSVs are already
    keyed to the physical schema".

    Note also that the *address* is taken from ``original_column_name``, not
    ``column_name``: in BIRD the latter is a friendly label ("customer id"), not an
    identifier, and it is what the rename map is keyed by.

    ``system_rules`` injects a registered ``sme_rules`` variant; ``None`` keeps
    ``v1``. Callers that stamp a prompt set must pass the resolved text, or the
    stamp names a variant the brief did not contain.
    """
    desc_dir = Path(db_description_dir)
    sections: list[str] = [
        "You are a subject-matter expert for this database. Answer curator "
        "clarification questions with concise, practical descriptions of what "
        "tables and columns mean and whether they are reliable for analysis.",
        "",
        system_rules or _SME_SYSTEM_RULES,
        "",
        "## Database column descriptions",
    ]

    # Exact first, then case-insensitive: BIRD ships a handful of description CSVs
    # whose stem differs from the schema's table only in case (``FootNotes`` vs
    # ``footnotes``), and Postgres folds unquoted identifiers to lower anyway.
    lowered = {k.lower(): v for k, v in (rename_map or {}).items()}
    # A map is only authoritative if it has entries; an absent manifest loads as {}.
    translating = bool(rename_map)
    dropped: list[str] = []

    def _physical(name: str) -> str | None:
        """``name`` in the physical schema's spelling, or ``None`` if it has none."""
        if not translating:
            return name
        return rename_map.get(name) or lowered.get(name.lower())

    def _physical_table(stem: str) -> str | None:
        """A CSV filename resolved to its physical table, or ``None``.

        Three of the 569 description CSVs are filed under a name that is not the
        table: app_store ships ``googleplaystore.csv`` and
        ``googleplaystore_user_reviews.csv`` for tables ``playstore`` and
        ``user_reviews``, and student_loan's ``filed_for_bankruptcy.csv`` is the
        misspelt table ``filed_for_bankrupcy``. Left unresolved, app_store's brief
        headed *both* of that database's tables with a name that exists nowhere —
        the same "describes something the agent cannot select" failure the column
        translation above exists to prevent. Falls back to the longest map key that
        is a suffix of the filename, then to the closest key by edit distance; both
        are tight enough that all 566 already-exact stems are untouched.
        """
        exact = _physical(stem)
        if exact is not None:
            return exact
        suffixes = sorted(
            (k for k in rename_map if k and stem.lower().endswith(k.lower())),
            key=len,
            reverse=True,
        )
        if suffixes:
            return rename_map[suffixes[0]]
        close = difflib.get_close_matches(stem, list(rename_map), n=1, cutoff=0.8)
        return rename_map[close[0]] if close else None

    csv_paths = sorted(desc_dir.glob("*.csv")) if desc_dir.is_dir() else []
    if not csv_paths:
        logger.warning(
            "SME brief has no column descriptions: no *.csv under %s. The SME will "
            "answer clarifications without its primary domain knowledge, which "
            "confounds the curated_sme lift being measured.",
            desc_dir,
        )
        sections.append("(no description CSVs found)")
    for path in csv_paths:
        table = _physical_table(path.stem)
        if table is None and translating:
            logger.warning(
                "SME brief: description file %s could not be resolved to a table in "
                "the physical schema; its heading names something the agent cannot "
                "query",
                path.name,
            )
        sections.append(f"### {table or path.stem}")
        # BIRD ships some description CSVs that are not UTF-8 — 11 files across 5 of the
        # 69 schemas (donor, hockey, public_review_platform, software_company,
        # works_cycles). ``UnicodeDecodeError`` is a ``ValueError``, so the ``except
        # OSError`` below never caught it and this function raised. The raise lands in
        # the SME build, which runs *after* baseline, seeded and curated are already
        # built and paid for, so the schema was dropped from the scored pool having cost
        # a full curator pass — and at ``--build-workers 1`` its YAML stayed in the
        # shared arm roots, competing as a router candidate for every other schema's
        # questions.
        #
        # Decoded with replacement instead of refused: a description with a few mangled
        # characters is worth far more than losing the schema, and the fallback is
        # recorded in the brief so a reader can see the text was degraded rather than
        # wondering why a column reads oddly.
        #
        # ``utf-8-sig``, not ``utf-8``: 83 of the 597 description CSVs start with a
        # BOM, which lands inside the first header name — ``﻿original_column_name``
        # — so ``row.get("original_column_name")`` returned ``None`` for every row of
        # those files. That silently emptied whole tables out of the brief (frpm in
        # california_schools contributed nothing at all) with no error anywhere.
        try:
            text = path.read_text(encoding="utf-8-sig", newline="")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8-sig", errors="replace", newline="")
            sections.append(
                f"(note: {path.name} is not valid UTF-8; decoded with replacement "
                "characters)"
            )
        except OSError as err:
            sections.append(f"(failed to read {path.name}: {err})")
            continue
        try:
            with io.StringIO(text, newline="") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    # ``original_column_name`` is the identifier; ``column_name`` is
                    # a human label ("customer id" for ``CustomerID``) and is empty
                    # on plenty of rows. Addressing the label meant the SME named
                    # something that is not a column even on the identity-rename
                    # schemas, and it is not what ``rename_map`` is keyed by.
                    raw = (
                        row.get("original_column_name")
                        or row.get("column_name")
                        or ""
                    ).strip()
                    if not raw:
                        continue
                    col = _physical(raw)
                    if col is None:
                        # No map entry means the column is not in the obfuscated
                        # schema — BIRD's CSVs describe the full upstream dataset
                        # while the DB is often a subset (retail_world ships the
                        # complete Northwind docs for a cut-down W3Schools table
                        # set). Emitting it would have the SME confidently describe
                        # a column the agent cannot select.
                        dropped.append(raw)
                        continue
                    desc = (row.get("column_description") or "").strip()
                    values = (row.get("value_description") or "").strip()
                    fmt = (row.get("data_format") or "").strip()
                    bits = [b for b in (desc, fmt, values) if b]
                    if col and bits:
                        sections.append(f"- {col}: {' | '.join(bits)}")
                    elif col:
                        sections.append(f"- {col}")
        except OSError as err:
            sections.append(f"(failed to read {path.name}: {err})")

    if dropped:
        logger.info(
            "SME brief: dropped %d described column(s) with no entry in the rename "
            "map (not present in the obfuscated schema): %s",
            len(dropped),
            ", ".join(sorted(set(dropped))[:20]),
        )

    # ALL unique evidence hints — never capped. In BIRD the ``evidence`` field is
    # the key domain hint (e.g. "higher CSS ranking value = higher prospect"), so
    # dropping any (the old 40-question cap did) starves the SME of exactly what it
    # needs to answer. Deduped to stay compact.
    seen_ev: set[str] = set()
    evidences: list[str] = []
    for item in train_items:
        ev = (item.evidence or "").strip()
        if ev and ev not in seen_ev:
            seen_ev.add(ev)
            evidences.append(ev)
    if evidences:
        sections.append("")
        sections.append("## Domain hints (evidence attached to analyst questions)")
        sections.extend(f"- {ev}" for ev in evidences)

    sections.append("")
    sections.append("## Example analyst questions (train only; for domain context)")
    for item in train_items[:max_train_questions]:
        sections.append(f"- {item.question}")

    return "\n".join(sections)


def assert_brief_no_leakage(
    brief: str,
    *,
    gold_sqls: Sequence[str] = (),
    test_questions: Sequence[str] = (),
) -> None:
    """Raise ``AssertionError`` if the brief contains gold SQL or test questions.

    Used by unit tests and the experiment runner leakage invariants.
    """
    if _SELECT_LEAK_RE.search(brief):
        raise AssertionError("SME brief must not contain SELECT (gold SQL leakage)")
    for sql in gold_sqls:
        snippet = sql.strip()
        if len(snippet) >= 12 and snippet in brief:
            raise AssertionError("SME brief contains a gold SQL substring")
    for q in test_questions:
        q = q.strip()
        if len(q) >= 12 and q in brief:
            raise AssertionError(f"SME brief contains test question text: {q[:60]!r}")


def _sanitize_sme_answer(text: str) -> str:
    """Strip SQL out of an SME answer so invented SQL cannot enter provenance.

    Removes queries from *anywhere* in the text and keeps every surviving line of
    prose. The earlier version kept only the lines *before* the first
    keyword-bearing line and discarded the rest, so an answer that opened with its
    query sanitised to the empty string and fell through to the canned fallback
    below — 11 of 381 measured answers, concentrated on the decoy-confirmation
    questions the curator most needed answered. Leading with the query it had just
    probed with is exactly what a model told to check the data first does; the
    prose underneath was always the answer, and dropping it was never the intent.

    Fenced blocks go first, then each keyword-bearing line and the wrapped
    continuation lines belonging to it (see :data:`_SQL_CONT_RE`). Every line
    carrying the keyword is dropped unconditionally, so the answer-purity
    guarantee is unchanged: no query text can reach provenance. The fallback fires
    only when nothing at all survives.
    """
    cleaned = _SQL_FENCE_RE.sub("", text)
    kept: list[str] = []
    in_sql = False
    for line in cleaned.splitlines():
        if _SELECT_SQL_RE.search(line):
            in_sql = True
        elif in_sql and not _SQL_CONT_RE.match(line):
            # Prose resumed (or a blank line ended the statement): keep this line.
            in_sql = False
        if in_sql:
            if line.rstrip().endswith(";"):
                in_sql = False  # statement terminated here; the next line is prose
            continue
        if not _FENCE_LINE_RE.match(line):
            kept.append(line)
    # Removing a block from the middle leaves a hole; collapse it rather than
    # handing the curator an answer with a paragraph-sized gap in it.
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return cleaned or (
        "Unsure — declining to invent a definition; treat this column cautiously."
    )


def build_sme_agent(model, *, gateway, brief: str, checkpointer=None):
    """A read-only deep-agent SME.

    The SME answers from the brief (domain descriptions + all evidence) and may
    **probe the live DB read-only** (`run_probe_query`) to verify a claim before
    answering — the same way a real SME would sanity-check against the data. It
    holds no write tools: it cannot touch the corpus.
    """
    from deepagents import create_deep_agent

    from .deep_agent import _CURATOR_IDENTITY, _render_rows

    def run_probe_query(sql: str) -> str:
        """Run a read-only SELECT to check the actual data before answering.
        Returns rows (truncated) or an error string. Never mutates data."""
        try:
            result = gateway.execute(sql, _CURATOR_IDENTITY)
        except Exception as err:  # noqa: BLE001 — surface as a tool result
            return f"error: {err}"
        return _render_rows(result)

    kwargs = {
        "model": model,
        "tools": [run_probe_query],
        "system_prompt": brief,
    }
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    return create_deep_agent(**kwargs)


def _last_message_text(result) -> str:
    msgs = result.get("messages") if isinstance(result, dict) else None
    if not msgs:
        return ""
    last = msgs[-1]
    content = getattr(last, "content", None)
    if content is None and isinstance(last, dict):
        content = last.get("content")
    if isinstance(content, list):  # reasoning models return content as typed parts
        # Keep only text parts. A reasoning part (`{'type': 'reasoning',
        # 'encrypted_content': ...}`) has no "text" key; stringifying it whole
        # would leak the model's encrypted chain-of-thought into the authored
        # rule (matches _message_text in llm/langchain_client.py).
        parts = [
            p["text"]
            for p in content
            if isinstance(p, dict) and isinstance(p.get("text"), str)
        ]
        parts += [p for p in content if isinstance(p, str)]
        content = " ".join(parts)
    return content or ""


class SimulatedSme:
    """SME :class:`Responder` briefed with :func:`build_sme_brief`.

    With a live LangChain model + a gateway it runs as a **read-only deep agent**
    that can probe the DB to verify its answers; otherwise (offline / no gateway)
    it falls back to a single-shot completion. Never receives write tools.
    """

    def __init__(
        self,
        chat: "ChatClient",
        brief: str,
        *,
        gateway=None,
        settings: "Settings | None" = None,
    ) -> None:
        self.chat = chat
        self.brief = brief
        # The caller's Settings, when it has them. Re-deriving config here instead
        # would stamp this producer's records with whatever the TOML says while the
        # brief it was handed came from the caller's resolved prompt set — a corpus
        # recorded under a prompt it was not built with, which is the attribution
        # failure the stamp exists to prevent.
        self._settings = settings
        self._agent = None
        model = getattr(chat, "model", None)  # LangChainChatClient exposes .model
        if gateway is not None and model is not None:
            try:
                from ..analyst.run_log import make_durable_checkpointer

                settings = self._resolved_settings()
                # Deep agents get no server injection — hand an explicit saver.
                # SME runs single-shot per answer() with a fresh thread_id (never
                # resumes across answers), so an in-process memory saver satisfies
                # the within-answer interrupt support without an unbounded on-disk
                # sme_checkpoints.sqlite that would only ever grow.
                cp = make_durable_checkpointer(settings, kind="memory")
                self._agent = build_sme_agent(
                    model, gateway=gateway, brief=brief, checkpointer=cp
                )
            except Exception:  # noqa: BLE001 — degrade to single-shot, never crash curation
                self._agent = None

    def _resolved_settings(self) -> "Settings | None":
        """The caller's Settings, or a freshly loaded one as a last resort."""
        if self._settings is not None:
            return self._settings
        try:
            from ..config import load_settings

            return load_settings(apply_local=False)
        except Exception:
            return None

    def answer(self, question: str) -> str:
        import time

        from ..analyst.run_log import emit_run_record, new_run_id
        from ..obs import tracing_callbacks
        from ..provenance import Producer

        # Deliberately says the same thing as the ``sme_rules`` block in the brief,
        # in fewer words. The two used to disagree — the brief banned queries
        # outright while this message invited them — and the model resolved the
        # contradiction by refusing to answer at all.
        user = (
            "Answer the following curator clarification. Run read-only probe "
            "queries first if checking the data helps; keep the SQL in the tool "
            "call and write the answer itself as plain prose with no SQL in "
            "it.\n\n"
            f"Clarification: {question}"
        )
        t0 = time.perf_counter()
        rid = new_run_id()
        error = None
        raw = ""
        usage_list: list = []
        try:
            if self._agent is not None:
                cbs = tracing_callbacks(with_usage=True)
                usage_cb = next(
                    (c for c in cbs if type(c).__name__ == "UsageMetadataCallbackHandler"),
                    None,
                )
                result = self._agent.invoke(
                    {"messages": [{"role": "user", "content": user}]},
                    config={
                        "recursion_limit": 40,
                        "callbacks": cbs,
                        "configurable": {"thread_id": rid},
                    },
                )
                raw = _last_message_text(result)
                if usage_cb is not None:
                    from ..analyst.run_log import usage_callback_entries

                    usage_list = usage_callback_entries(usage_cb, source="sme")
            else:
                raw = self.chat.complete(self.brief, user)
        except Exception as err:  # noqa: BLE001 — always emit a record
            error = f"{type(err).__name__}: {err}"
            raw = ""
        answer = _sanitize_sme_answer(raw)
        try:
            settings = self._resolved_settings()
            if settings is None:
                return answer
            emit_run_record(
                settings=settings,
                producer=Producer.sme,
                run_id=rid,
                thread_id=rid,
                outcome="error" if error else "ok",
                error=error,
                answer_text=answer if settings.log_full_content else None,
                token_usage=usage_list,
                t0=t0,
            )
        except Exception:
            pass
        return answer
