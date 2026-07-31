"""Retrieval indexes are built per *corpus*, not per *question*.

``retrieve`` used to call ``build_index`` and ``build_embedding_index`` on every
call. The BM25 rebuild is merely wasteful; the embedding rebuild is a live network
round-trip that re-embeds every asset in the routed corpus — text that is identical
for every question landing on that schema. Only the question embedding is genuinely
per-call.

These tests count calls rather than asserting a cache object exists, because the
claim being made is about cost. A cache that is present but never hit would satisfy
any structural assertion and save nothing.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from governed_bi.config import DataSourceConfig, Environment, Settings
from governed_bi.corpus import Corpus
from governed_bi.corpus.schemas import Column, LogicalType, TableAsset
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.retrieval import RetrievalIndexCache, corpus_index_key, retrieve


def _col(name: str) -> Column:
    return Column(
        physical_name=name,
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=True,
        is_unique=False,
    )


def _table(schema: str, name: str) -> TableAsset:
    return TableAsset(
        id=f"tbl_{schema}_{name}",
        schema=schema,
        physical_name=name,
        columns=[_col("id"), _col("amount")],
    )


class _CountingEmbedder:
    """Counts asset-batch embeds separately from question embeds."""

    def __init__(self) -> None:
        self.n_batch = 0
        self.n_docs = 0
        self.n_query = 0

    def embed(self, docs):
        self.n_batch += 1
        self.n_docs += len(docs)
        return [[float(len(d)), 1.0] for d in docs]

    def embed_one(self, text):
        self.n_query += 1
        return [float(len(text)), 1.0]


# --------------------------------------------------------------------------- #
# The cache itself
# --------------------------------------------------------------------------- #


def test_repeated_questions_over_one_corpus_embed_the_assets_once():
    corpus = Corpus(assets=[_table("s", "orders"), _table("s", "customers")]).for_analyst()
    embedder = _CountingEmbedder()
    cache = RetrievalIndexCache()

    for q in ("total amount", "how many customers", "amount by customer"):
        retrieve(corpus, q, embedder=embedder, index_cache=cache)

    assert embedder.n_batch == 1, "assets must be embedded once, not once per question"
    assert embedder.n_query == 3, "the question embedding is genuinely per-call"


def test_without_the_cache_every_question_re_embeds_every_asset():
    """The regression this guards against, stated as an executable contrast."""
    corpus = Corpus(assets=[_table("s", "orders")]).for_analyst()
    embedder = _CountingEmbedder()

    for q in ("a", "b", "c"):
        retrieve(corpus, q, embedder=embedder)  # no index_cache

    assert embedder.n_batch == 3


def test_two_different_corpora_do_not_share_an_index():
    """Keyed on content. Sharing across corpora would serve one schema's assets to
    another schema's question — a correctness failure, not a performance one."""
    cache = RetrievalIndexCache()
    embedder = _CountingEmbedder()
    a = Corpus(assets=[_table("s_a", "orders")]).for_analyst()
    b = Corpus(assets=[_table("s_b", "invoices")]).for_analyst()

    ra = retrieve(a, "orders", embedder=embedder, index_cache=cache)
    rb = retrieve(b, "invoices", embedder=embedder, index_cache=cache)

    assert embedder.n_batch == 2
    assert ra.table_ids == ["tbl_s_a_orders"]
    assert rb.table_ids == ["tbl_s_b_invoices"]


def test_the_key_is_content_not_object_identity():
    """The caller rebuilds the retrieval corpus per question, so an identity-keyed
    cache would miss every time."""
    one = Corpus(assets=[_table("s", "orders")]).for_analyst()
    two = Corpus(assets=[_table("s", "orders")]).for_analyst()
    assert one is not two
    assert corpus_index_key(one) == corpus_index_key(two)


def test_cached_and_uncached_retrieval_agree():
    """The cache must not change what retrieval returns — only how often it pays."""
    corpus = Corpus(
        assets=[_table("s", "orders"), _table("s", "customers"), _table("s", "items")]
    ).for_analyst()
    question = "total order amount per customer"

    plain = retrieve(corpus, question, embedder=_CountingEmbedder())
    cached_cache = RetrievalIndexCache()
    warm = retrieve(corpus, question, embedder=_CountingEmbedder(), index_cache=cached_cache)
    again = retrieve(corpus, question, embedder=_CountingEmbedder(), index_cache=cached_cache)

    assert plain.table_ids == warm.table_ids == again.table_ids
    assert plain.scores == warm.scores == again.scores


# --------------------------------------------------------------------------- #
# ...and that the serve graph actually passes one in
# --------------------------------------------------------------------------- #


def test_the_serve_graph_reuses_indexes_across_turns_on_one_graph(monkeypatch):
    """The wiring, not just the primitive: a cache the graph never passes saves nothing.

    Driven through the real rails graph on a single-schema corpus so ``assemble`` runs
    for real. The agent core then fails on the dummy model and degrades to a refusal,
    which is fine — retrieval has already happened by then.
    """
    from governed_bi.analyst.agent import build_serve_rails

    corpus = Corpus(assets=[_table("s", "orders"), _table("s", "customers")]).for_analyst()
    settings = replace(
        Settings.for_env(Environment.dev),
        datasource=DataSourceConfig(kind="postgres", dsn="host=x", schema="s"),
    )
    embedder = _CountingEmbedder()

    conn = SqliteConnector(":memory:")
    try:
        graph = build_serve_rails(
            corpus=corpus,
            gateway=Gateway(conn),
            settings=settings,
            identity=Identity(user="dev", all_access=True),
            model=None,
            embedder=embedder,
            session_id="s",
        )
        for q in ("total amount", "how many customers", "amount by customer"):
            graph.invoke({"question": q, "session_id": "s"})
    finally:
        conn.close()

    assert embedder.n_query == 3, "each question is still embedded"
    assert embedder.n_batch == 1, (
        "assets were re-embedded per question: the graph is not passing its "
        "RetrievalIndexCache into retrieve()"
    )


def test_routed_corpus_reuse_survives_a_multi_schema_graph(monkeypatch):
    """Two questions routed to the same schema must not rebuild that schema's index.

    The retrieval corpus is derived from the routed schema set, so without memoising
    it the graph mints a fresh ``Corpus`` per question and the content key is the only
    thing keeping the index cache warm.
    """
    import governed_bi.analyst.agent as agent_mod
    from governed_bi.analyst.agent import build_serve_rails
    from governed_bi.retrieval import SchemaPick

    corpus = Corpus(
        assets=[_table("s_a", "orders"), _table("s_b", "invoices")]
    ).for_analyst()
    settings = replace(
        Settings.for_env(Environment.dev),
        datasource=DataSourceConfig(kind="postgres", dsn="host=x"),  # span all
        schema_route_llm_pick=True,
    )
    embedder = _CountingEmbedder()

    monkeypatch.setattr(agent_mod, "pick_schema", lambda *a, **k: SchemaPick("s_a"))
    monkeypatch.setattr(
        agent_mod,
        "shortlist_schemas",
        lambda *a, **k: ["s_a", "s_b"],
    )

    real_retrieve = agent_mod.retrieve
    seen: list[int] = []

    def _spy(corpus_arg, question, **kw):
        seen.append(len(corpus_arg.assets))
        return real_retrieve(corpus_arg, question, **kw)

    monkeypatch.setattr(agent_mod, "retrieve", _spy)

    conn = SqliteConnector(":memory:")
    try:
        graph = build_serve_rails(
            corpus=corpus,
            gateway=Gateway(conn),
            settings=settings,
            identity=Identity(user="dev", all_access=True),
            model=object(),
            embedder=embedder,
            session_id="s",
        )
        for q in ("orders total", "orders by month", "orders count"):
            graph.invoke({"question": q, "session_id": "s"})
    finally:
        conn.close()

    assert len(seen) == 3, "retrieval ran for every question"
    # Schema-document vectors for the router are embedded once at graph build; the
    # routed corpus's assets must add exactly one more batch, not one per question.
    assert embedder.n_batch == 2, (
        f"expected one router batch + one routed-corpus batch, got {embedder.n_batch}"
    )


# --------------------------------------------------------------------------- #
# The router's schema documents were the single largest non-model CPU cost on the
# serve path: `schema_documents` runs `Corpus.for_analyst()`, which deep-copies
# every asset via pydantic, and the BM25 branch called it per question. Measured at
# 55% of the serve path's CPU across 94 questions — and `deepcopy` is pure Python,
# so it held the GIL and capped what raising `--workers` could buy.
# --------------------------------------------------------------------------- #


def _multi_schema_corpus(n_schemas: int = 4, per_schema: int = 6) -> Corpus:
    return Corpus(
        assets=[
            _table(f"s{s}", f"t{t}")
            for s in range(n_schemas)
            for t in range(per_schema)
        ]
    ).for_analyst()


def test_the_router_deep_copies_the_corpus_once_not_once_per_question(monkeypatch):
    """Counts the actual `for_analyst` calls, because the claim is about cost. A cache
    that is present but not consulted satisfies any structural assertion."""
    from governed_bi.retrieval import shortlist_schemas

    corpus = _multi_schema_corpus()
    calls = {"n": 0}
    real = Corpus.for_analyst

    def _counting(self):
        calls["n"] += 1
        return real(self)

    monkeypatch.setattr(Corpus, "for_analyst", _counting)

    cache = RetrievalIndexCache()
    for q in ("orders total", "how many t1", "sum over t2", "count rows"):
        shortlist_schemas(corpus, q, top_k=3, index_cache=cache)

    assert calls["n"] == 1, (
        f"the corpus was deep-copied {calls['n']} times for 4 questions; the router's "
        "schema documents are a pure function of the corpus"
    )


def test_without_the_cache_the_router_pays_per_question(monkeypatch):
    """The regression, as an executable contrast."""
    from governed_bi.retrieval import shortlist_schemas

    corpus = _multi_schema_corpus()
    calls = {"n": 0}
    real = Corpus.for_analyst
    monkeypatch.setattr(
        Corpus, "for_analyst", lambda self: (calls.__setitem__("n", calls["n"] + 1), real(self))[1]
    )

    for q in ("a", "b", "c"):
        shortlist_schemas(corpus, q, top_k=3)  # no index_cache

    assert calls["n"] == 3


def test_the_cache_does_not_change_which_schemas_are_shortlisted():
    """It is a routing decision. Getting this wrong changes EX, not just latency."""
    from governed_bi.retrieval import shortlist_schemas

    corpus = _multi_schema_corpus()
    cache = RetrievalIndexCache()
    for q in ("t1 rows", "s2 totals", "count of t4", "unrelated words here"):
        assert shortlist_schemas(corpus, q, top_k=3) == shortlist_schemas(
            corpus, q, top_k=3, index_cache=cache
        ), q


def test_schema_docs_are_keyed_by_corpus_content():
    """Two different corpora must not share a schema index — that would route one
    schema's question against another's documents."""
    cache = RetrievalIndexCache()
    a = Corpus(assets=[_table("alpha", "orders")]).for_analyst()
    b = Corpus(assets=[_table("beta", "invoices")]).for_analyst()
    assert cache.schema_docs(a) != cache.schema_docs(b)
    assert set(cache.schema_docs(a)) == {"alpha"}
    assert set(cache.schema_docs(b)) == {"beta"}


def test_the_schema_bm25_index_is_built_once_per_corpus():
    corpus = _multi_schema_corpus()
    cache = RetrievalIndexCache()
    assert cache.schema_bm25(corpus) is cache.schema_bm25(corpus)


def test_the_serve_graph_passes_its_cache_to_the_router(monkeypatch):
    """Wiring, not just the primitive."""
    import inspect

    from governed_bi.analyst import agent as agent_mod

    src = inspect.getsource(agent_mod._route_schemas)
    call = src.split("shortlist_schemas(", 1)[1].split(")", 1)[0]
    assert "index_cache=rt.index_cache" in call, f"router call omits the cache: {call!r}"


def test_corpus_deep_copies_do_not_grow_with_the_number_of_questions():
    """A budget, not a benchmark.

    The router used to run `Corpus.for_analyst()` per question, deep-copying every
    asset. That was 55% of the serve path's non-model CPU and, because `deepcopy` is
    pure Python and holds the GIL, it capped what `--workers` could buy — a
    concurrency knob throttled by the thing it was meant to parallelise.

    The property is that copies scale with *corpora* (distinct routed schema sets),
    not with *questions*. Asserted by comparing two run lengths rather than by a
    timing, so it cannot flake on a slow machine and cannot be satisfied by a cache
    that merely exists.
    """
    from governed_bi.analyst.agent import build_serve_rails

    def _copies_for(n_questions: int) -> int:
        corpus = _multi_schema_corpus(n_schemas=3, per_schema=4)
        settings = replace(
            Settings.for_env(Environment.dev),
            datasource=DataSourceConfig(kind="postgres", dsn="host=x"),  # span all
            run_log_kind="off",
        )
        seen = {"n": 0}
        real = Corpus.for_analyst
        conn = SqliteConnector(":memory:")
        try:
            graph = build_serve_rails(
                corpus=corpus,
                gateway=Gateway(conn),
                settings=settings,
                identity=Identity(user="dev", all_access=True),
                model=None,
                embedder=None,
                session_id="s",
            )
            # Count only what the QUESTIONS cost, not graph construction.
            Corpus.for_analyst = lambda self: (  # type: ignore[method-assign]
                seen.__setitem__("n", seen["n"] + 1),
                real(self),
            )[1]
            try:
                for i in range(n_questions):
                    graph.invoke({"question": f"totals for t{i % 4}", "session_id": "s"})
            finally:
                Corpus.for_analyst = real  # type: ignore[method-assign]
        finally:
            conn.close()
        return seen["n"]

    few, many = _copies_for(4), _copies_for(24)
    assert many == few, (
        f"{few} copies for 4 questions but {many} for 24 — corpus deep-copying scales "
        "with questions again, and it is GIL-bound"
    )
    # Bounded by the schema count, not merely constant at some large number.
    assert many <= 3, f"{many} copies for a 3-schema corpus"


# --------------------------------------------------------------------------- #
# The agent's own search tool shares the graph's index.
#
# `search_corpus` called `retrieve` with no `index_cache`, so every invocation rebuilt the
# index — BM25, and with an embedder a re-embed of every asset in the POOLED corpus. The
# rails already build this cache for `assemble`; the tools simply never received it. On a
# 3-schema corpus that was 4.8x on BM25 alone, and the embedding term scales with asset
# count, so it is the largest per-call cost in the agent loop at 69 schemas.
# --------------------------------------------------------------------------- #


def _search_tool(corpus, embedder, cache):
    """The agent's own `search_corpus`, built the way `build_agent_core` builds it."""
    from governed_bi.analyst.tools import make_tools

    conn = SqliteConnector(":memory:")
    try:
        tools = make_tools(
            corpus,
            Gateway(conn),
            Identity(user="dev", all_access=True),
            embedder=embedder,
            index_cache=cache,
        )
        return next(t for t in tools if t.name == "search_corpus"), conn
    except Exception:
        conn.close()
        raise


def test_the_agent_search_tool_reuses_the_index_across_calls():
    corpus = _multi_schema_corpus(n_schemas=4, per_schema=6)
    embedder = _CountingEmbedder()
    search, conn = _search_tool(corpus, embedder, RetrievalIndexCache())
    try:
        for i in range(4):
            search.invoke({"query": f"question {i} about revenue"})
    finally:
        conn.close()

    assert embedder.n_batch == 1, (
        f"the asset index was embedded {embedder.n_batch} times across 4 searches; the "
        "shared cache should build it once"
    )


def test_without_the_cache_the_agent_search_tool_rebuilds_every_call():
    """The control, so the test above cannot pass for an unrelated reason."""
    corpus = _multi_schema_corpus(n_schemas=4, per_schema=6)
    embedder = _CountingEmbedder()
    search, conn = _search_tool(corpus, embedder, None)
    try:
        for i in range(4):
            search.invoke({"query": f"question {i} about revenue"})
    finally:
        conn.close()

    assert embedder.n_batch == 4, (
        f"expected one index build per call without a cache; got {embedder.n_batch}"
    )


def test_build_agent_core_forwards_its_cache_to_the_tools():
    """The middle hop, behaviourally. `build_agent_core` is called per question, so if it
    dropped the cache on the way to `make_tools` the sharing would be silently lost while
    every structural check still passed."""
    from governed_bi.analyst import agent as agent_mod
    from governed_bi.analyst.agent import build_agent_core

    corpus = _multi_schema_corpus(n_schemas=4, per_schema=6)
    embedder = _CountingEmbedder()
    cache = RetrievalIndexCache()
    conn = SqliteConnector(":memory:")
    try:
        # Spy on the factory `build_agent_core` calls, so this asserts the hop rather
        # than re-doing it: whatever cache it forwards is what the tools get.
        seen: dict = {}
        #  does `from .tools import make_tools`, so it holds its own reference —
        # patching the tools module would miss the call entirely and the spy would report
        # a forward that never happened.
        real_make_tools = agent_mod.make_tools

        def _spy(*args, **kwargs):
            seen["index_cache"] = kwargs.get("index_cache")
            return real_make_tools(*args, **kwargs)

        agent_mod.make_tools = _spy
        try:
            build_agent_core(
                corpus,
                Gateway(conn),
                Identity(user="dev", all_access=True),
                None,
                settings=Settings.for_env(Environment.dev),
                dialect="sqlite",
                default_schema=None,
                embedder=embedder,
                index_cache=cache,
            )
        finally:
            agent_mod.make_tools = real_make_tools

        assert seen.get("index_cache") is cache, (
            "build_agent_core did not forward its index cache to make_tools, so every "
            "search_corpus call rebuilds the index"
        )
        search = next(
            t
            for t in real_make_tools(
                corpus,
                Gateway(conn),
                Identity(user="dev", all_access=True),
                embedder=embedder,
                index_cache=seen["index_cache"],
            )
            if t.name == "search_corpus"
        )
        for i in range(4):
            search.invoke({"query": f"q{i} revenue"})
    finally:
        conn.close()

    assert embedder.n_batch == 1, (
        f"index built {embedder.n_batch} times across 4 searches; build_agent_core is not "
        "forwarding its cache to the tools"
    )


def test_the_serve_graph_hands_its_cache_to_the_agent_core():
    """The outer hop. Scoped to the `build_agent_core(` call rather than searched over the
    whole function: `index_cache=_index_cache` appears at three sites in
    `build_serve_rails` — the shortlist, the retrieve, and this one — so an unscoped
    substring check stayed green when the one that matters was deleted.
    """
    import inspect

    from governed_bi.analyst.agent import _build_serve_rails

    src = inspect.getsource(_build_serve_rails)
    call_start = src.index("build_agent_core(")
    depth, end = 0, call_start
    for i, ch in enumerate(src[call_start:], call_start):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    call = src[call_start:end]
    # The graph's own cache by name, not merely *an* `index_cache=` argument. Checking
    # only for the keyword let `index_cache=None` through — a complete revert of the perf
    # fix on the real serve path — which is the same class of hole as the unscoped
    # substring check this replaced.
    assert "index_cache=_index_cache" in call, (
        "the rails no longer pass their shared index to build_agent_core, so search_corpus "
        f"rebuilds its index on every call. Call site was: {call!r}"
    )


# --------------------------------------------------------------------------- #
# AUDIT R6: the serve rails are rebuilt per question, so a graph-scoped cache
# was thrown away every turn — every question re-embedded the whole corpus.
# --------------------------------------------------------------------------- #


def test_build_serve_rails_reuses_a_caller_supplied_cache():
    from governed_bi.analyst.agent import build_serve_rails
    from governed_bi.config import Environment, Settings
    from governed_bi.corpus import load_corpus
    from governed_bi.gateway import Gateway, Identity, SqliteConnector
    from governed_bi.retrieval import RetrievalIndexCache

    root = Path(__file__).resolve().parents[1]
    corpus = load_corpus(root / "corpus", schema="beer_factory").for_analyst()
    settings = replace(
        Settings.for_env(Environment.dev),
        datasource=DataSourceConfig(kind="sqlite", corpus_pin="beer_factory"),
    )
    connector = SqliteConnector(root / "data" / "bird" / "beer_factory.sqlite")
    try:
        gateway = Gateway(connector)
        shared = RetrievalIndexCache()
        # Two "turns": two graph builds, one cache.
        for _ in range(2):
            build_serve_rails(
                corpus=corpus,
                gateway=gateway,
                settings=settings,
                identity=Identity(user="dev", all_access=True),
                model=None,
                index_cache=shared,
            )
    finally:
        connector.close()
    # Nothing retrieved yet; the fix is about object identity — the graph must adopt
    # the caller's cache rather than minting its own per build.
    assert shared.hits == 0 and shared.misses == 0

    idx_a = shared.bm25(corpus)
    idx_b = shared.bm25(corpus)
    assert idx_a is idx_b
    assert shared.hits >= 1, "a second lookup of the same corpus must hit"


def test_the_serve_stack_owns_one_cache():
    from governed_bi.api.stack import ServeStack
    from governed_bi.retrieval import RetrievalIndexCache

    made = ServeStack.__dataclass_fields__["index_cache"].default_factory()
    assert isinstance(made, RetrievalIndexCache)
    # Per-stack, not shared process-wide: two stacks must not cross-contaminate.
    assert made is not ServeStack.__dataclass_fields__["index_cache"].default_factory()
