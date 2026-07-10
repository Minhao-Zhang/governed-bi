"""Build the serve stack for the API from configuration/environment.

One place that assembles everything a request needs — corpus (full + server
view), settings, identity, the SQLite path, and the model stack (SQL generator +
embedder + narrator) — driven by config, so the same API binary runs the three
profiles (local-dev / public-demo / internal) by configuration alone. The model
stack is live (LangChain, needs the ``agents`` extra + a key) when
``OPENAI_API_KEY`` is set, else the deterministic offline default (template SQL,
no narration), the same live-vs-offline split the engine uses elsewhere.

Near-term the data source is SQLite (the committed fixture). The connector seam
is where a Postgres/Redshift profile plugs in later; nothing else here changes.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import load_settings
from ..corpus import load_corpus
from ..gateway import Identity

if TYPE_CHECKING:
    from ..config import Settings
    from ..corpus import Corpus
    from ..llm import Embedder
    from ..server import AnswerNarrator
    from ..server.sqlgen import SqlGenerator

logger = logging.getLogger("governed_bi.api")


@dataclass(frozen=True)
class ServeStack:
    """Everything the API needs to answer + describe one deployment."""

    corpus_full: "Corpus"  # audit view (Facts + Inference + Audit, excluded shown)
    corpus_server: "Corpus"  # for_server() view (what SQL-gen may see)
    settings: "Settings"
    dialect: str
    sqlite_path: Path
    identity: Identity
    generator: "SqlGenerator | None"
    embedder: "Embedder | None"
    narrator: "AnswerNarrator | None"
    model_name: str | None
    has_live_model: bool


def _build_model_stack(settings) -> tuple[Any, Any, Any, str | None, bool]:
    """(generator, embedder, narrator, model_name, has_live_model).

    Live LangChain clients when a key + the ``agents`` extra are present; else the
    deterministic offline default (template generator, no embedder/narrator).
    """
    if os.environ.get("OPENAI_API_KEY"):
        try:
            from ..llm import LangChainChatClient, LangChainEmbedder
            from ..server import LlmAnswerNarrator, LlmSqlGenerator

            models = settings.models
            chat = LangChainChatClient.from_config(models)
            return (
                LlmSqlGenerator(chat, dialect="sqlite"),
                LangChainEmbedder.from_config(models),
                LlmAnswerNarrator(chat),
                models.llm_model,
                True,
            )
        except Exception:  # missing agents extra / bad config -> offline fallback
            # A key was set (live intended) but the stack failed to build; make the
            # silent downgrade to offline observable rather than a mystery.
            logger.warning(
                "OPENAI_API_KEY is set but the live model stack failed to build; "
                "falling back to the offline profile",
                exc_info=True,
            )
    return (None, None, None, None, False)


def build_stack() -> ServeStack:
    """Assemble the serve stack from environment + ``governed_bi.toml``.

    Env overrides (all optional): ``GOVERNED_BI_CORPUS`` (corpus root, default
    ``corpus``), ``GOVERNED_BI_DB`` (default ``beer_factory``),
    ``GOVERNED_BI_SQLITE`` (default ``data/bird/beer_factory.sqlite``).
    """
    root = Path(os.environ.get("GOVERNED_BI_CORPUS", "corpus"))
    db = os.environ.get("GOVERNED_BI_DB", "beer_factory")
    sqlite_path = Path(os.environ.get("GOVERNED_BI_SQLITE", "data/bird/beer_factory.sqlite"))

    settings = load_settings()
    corpus_full = load_corpus(root, db=db)
    generator, embedder, narrator, model_name, has_live = _build_model_stack(settings)

    return ServeStack(
        corpus_full=corpus_full,
        corpus_server=corpus_full.for_server(),
        settings=settings,
        dialect="sqlite",
        sqlite_path=sqlite_path,
        identity=Identity(user="demo", all_access=True),
        generator=generator,
        embedder=embedder,
        narrator=narrator,
        model_name=model_name,
        has_live_model=has_live,
    )
