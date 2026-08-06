"""``amazon.titan-embed-text-v2:0`` through the Bedrock Runtime SDK directly.

**Not through ``langchain-aws``'s ``BedrockEmbeddings``.** Same reasoning as
:mod:`.openai_embedder`'s Decision #2: this port needs ``model``/``dimensions`` as facts a
cache key can read, and LangChain's ``Embeddings`` interface has neither. ``boto3`` is
already a transitive dependency (langchain-aws pulls it for the chat model); this module
adds no new package.

**One request per input, always.** Titan's ``InvokeModel`` API takes exactly one
``inputText`` per call — there is no batch endpoint — which is the "one request per
document for Bedrock" cost shape ``ports.py:140`` already documents. ``batch_size`` is
fixed at 1 so a caller sizing its own request volume off this attribute gets the true
answer rather than inheriting OpenAI's 256.

**Bedrock does not report back which model served a request.** Titan has no silent-alias
hazard the way an OpenAI model name can resolve to a dated snapshot — the request names an
exact versioned model id (``amazon.titan-embed-text-v2:0``, not a floating alias) and the
response carries no model field to disagree with it. So unlike :class:`.OpenAIEmbedder`,
``model`` is settled at construction, not learned from a probe.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from governed_bi.ports import Vector

from .embedder import BaseEmbedder

__all__ = ["BEDROCK_EMBEDDING_MODEL", "BEDROCK_EMBEDDING_DIMENSIONS", "BedrockEmbedder"]

#: The Bedrock foundation model id. Versioned, not an alias — see the module docstring.
BEDROCK_EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"

#: Titan v2's native width. It also serves 256 and 512 via `dimensions` in the request
#: body, but 1024 is what `governed_bi.local.toml`'s prior Bedrock deployment used, and an
#: unrequested narrower width here would be a comparability change nobody asked for.
BEDROCK_EMBEDDING_DIMENSIONS = 1024


class BedrockEmbedder(BaseEmbedder):
    """Titan v2 embeddings as an :class:`~governed_bi.ports.Embedder`.

    ``region``/``profile`` are forwarded to ``boto3.client`` unchanged; ``None`` keeps
    boto3's own resolution order (env vars, ``~/.aws/config``), which is what every other
    AWS-backed piece of this project already relies on rather than re-deciding here.
    """

    batch_size = 1

    def __init__(
        self,
        *,
        model: str = BEDROCK_EMBEDDING_MODEL,
        dimensions: int = BEDROCK_EMBEDDING_DIMENSIONS,
        region: str | None = None,
        client: Any | None = None,
    ) -> None:
        if int(dimensions) not in (256, 512, 1024):
            raise ValueError(f"Titan v2 serves 256/512/1024, got dimensions={dimensions!r}")
        self._model = str(model)
        self._dimensions = int(dimensions)
        self._region = region
        self._client = client

    @property
    def model(self) -> str:
        """``bedrock:<model id>``. Provider-qualified, per ``ports.py:140``."""
        return f"bedrock:{self._model}"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _bedrock_client(self) -> Any:
        if self._client is None:
            import boto3

            kwargs: dict[str, Any] = {}
            if self._region:
                kwargs["region_name"] = self._region
            self._client = boto3.client("bedrock-runtime", **kwargs)
        return self._client

    def _embed_one(self, text: str) -> Vector:
        body = json.dumps({"inputText": text, "dimensions": self._dimensions, "normalize": True})
        response = self._bedrock_client().invoke_model(
            modelId=self._model, body=body, contentType="application/json", accept="application/json",
        )
        payload = json.loads(response["body"].read())
        vector = payload.get("embedding")
        if not isinstance(vector, list):
            raise ValueError(f"Titan response has no 'embedding' list: {sorted(payload)!r}")
        if len(vector) != self._dimensions:
            raise ValueError(
                f"requested dimensions={self._dimensions} but Titan returned {len(vector)}; "
                "a dimensions request the provider ignored must not pass as an honoured one"
            )
        return [float(v) for v in vector]

    def _embed_batch(self, texts: Sequence[str]) -> list[Vector]:
        # batch_size == 1, so this is always exactly one text -- the loop is here (rather
        # than asserting len(texts) == 1) only so BaseEmbedder.embed's chunking contract
        # stays satisfiable if a future caller ever overrides batch_size upward by mistake.
        return [self._embed_one(text) for text in texts]
