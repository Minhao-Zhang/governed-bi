"""The the internal proxy: an internal OpenAI-compatible LLM gateway, as ``ChatOpenAI`` / ``OpenAIEmbeddings``.

The proxy fronts several vendors behind one ``{base}/v2/unified`` route that speaks the
OpenAI chat-completions and embeddings contract, so the rest of the stack reaches it through
the same clients the OpenAI provider uses. Only three things differ and this module supplies
all three: the ``base_url``, the auth flow, and a required ``extra_body`` trace envelope.

**No endpoint, secret or account id is committed here.** The API key *and* the base URL both
live in AWS Secrets Manager; this module knows only the environment variable that names the
secret, read at call time. See :data:`PROXY_SECRET_NAME_VAR`.

**Auth is per request, never frozen into the client.** The key is exchanged at
``POST {base}/generatetoken`` for a bearer token that expires after ~60 minutes. The serve
stack is built once at process start, so a token baked into ``default_headers`` at
construction would go stale after an hour and every later request would 401 with no recovery.
:func:`_build_auth` instead applies a token per request from a TTL-cached
:class:`ProxyTokenProvider` (refresh at ~50 min) and force-mints once on a 401.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable

# Ported from the proxy fork branch's llm/proxy_gateway.py. v2 has no ModelConfig, so the
# builders take plain kwargs; the token flow, extra_body and embedding sanitising are as they
# ran. Untracked on a server until 2026-08-07 — the 2026-08-07 BIRD run needs this file.

__all__ = [
    "PROXY_CA_BUNDLE_VAR",
    "PROXY_REGION_VAR",
    "PROXY_SECRET_NAME_VAR",
    "ProxyTokenProvider",
    "build_chat_model",
    "build_embeddings",
    "build_extra_body",
    "get_proxy_credentials",
    "mint_bearer_token",
    "shared_token_provider",
]

#: Environment variable holding the **name** of the Secrets Manager secret that carries the
#: proxy's API key and base URL. By name and not by value, like ``OPENAI_API_KEY_VAR``; unlike
#: it, this has no default, because a secret id is a deployment fact and the previous default
#: was an internal account slug sitting in source. Unset means the provider refuses.
PROXY_SECRET_NAME_VAR = "GOVERNED_BI_PROXY_SECRET"

#: AWS region for that lookup. Unset falls through to boto3's own resolution chain.
PROXY_REGION_VAR = "GOVERNED_BI_PROXY_REGION"

#: Path to a CA bundle for the proxy's TLS chain. Unset disables verification, which is what
#: the internal gateway's self-signed chain has required; set it to verify instead.
PROXY_CA_BUNDLE_VAR = "GOVERNED_BI_PROXY_CA_BUNDLE"

UNIFIED_SUFFIX = "/v2/unified"

#: Refresh well before the ~60 min server-side expiry; the 401 retry is the backstop, not the plan.
TOKEN_TTL_SECONDS = 50 * 60

#: Bedrock/Anthropic ConverseStream requires max_tokens and the OpenAI SDK omits it when unset,
#: so a bare proxy config would 400 on Claude without a default.
DEFAULT_PROXY_MAX_TOKENS = 4096

#: The only adaptive-thinking efforts the proxy accepts.
_THINKING_EFFORTS = ("low", "medium", "high")


def _require_boto3():
    """Import ``boto3`` lazily. It is **not** a declared dependency of this project."""
    try:
        import boto3  # noqa: PLC0415
    except ModuleNotFoundError as err:  # pragma: no cover - exercised only sans dep
        raise ModuleNotFoundError(
            "the internal proxy provider needs 'boto3' to read its secret from AWS Secrets Manager, and "
            "boto3 is not in this project's dependencies (pyproject.toml has no extras). "
            "Install it into the environment before running --provider proxy."
        ) from err
    return boto3


def _require_requests():
    """Import ``requests`` lazily (arrives transitively via langsmith)."""
    try:
        import requests  # noqa: PLC0415
    except ModuleNotFoundError as err:  # pragma: no cover
        raise ModuleNotFoundError("the internal proxy provider needs 'requests' to mint the bearer token.") from err
    return requests


# --------------------------------------------------------------------------- #
# Configuration, read from the environment at call time
# --------------------------------------------------------------------------- #


def _secret_name(explicit: str | None = None) -> str:
    """The secret's name, from the argument or :data:`PROXY_SECRET_NAME_VAR`. Fails closed."""
    name = (explicit or os.environ.get(PROXY_SECRET_NAME_VAR) or "").strip()
    if not name:
        raise RuntimeError(
            f"{PROXY_SECRET_NAME_VAR} is not set, so the internal proxy provider does not know which "
            "secret holds its API key and base URL. Set it to the secret's name (not its "
            "value), or use the OpenAI provider."
        )
    return name


def _region(explicit: str | None = None) -> str:
    """The AWS region, or ``""`` to let boto3 resolve it from its own config chain."""
    return (explicit or os.environ.get(PROXY_REGION_VAR) or "").strip()


def _tls_verify(explicit: Any = None) -> Any:
    """A CA bundle path from :data:`PROXY_CA_BUNDLE_VAR`, else ``False`` (no verification)."""
    if explicit is not None:
        return explicit
    return (os.environ.get(PROXY_CA_BUNDLE_VAR) or "").strip() or False


# --------------------------------------------------------------------------- #
# Credentials (Secrets Manager) — cached per (region, secret)
# --------------------------------------------------------------------------- #

_creds_cache: dict[str, tuple[str, str]] = {}
_creds_lock = threading.Lock()


def get_proxy_credentials(secret_name: str | None = None, region: str | None = None) -> tuple[str, str]:
    """Return ``(api_key, base_url)`` from AWS Secrets Manager, cached per ``(region, secret)``.

    ``base_url`` is the proxy *root*, serving both ``/generatetoken`` and ``/v2/unified/...``;
    the suffix is stripped if the secret already carries it. Neither value is ever logged.
    """
    name = _secret_name(secret_name)
    where = _region(region)
    cache_key = f"{where}:{name}"
    cached = _creds_cache.get(cache_key)
    if cached is not None:
        return cached

    import json  # noqa: PLC0415

    boto3 = _require_boto3()
    session = boto3.Session(region_name=where or None)
    secret = json.loads(session.client("secretsmanager").get_secret_value(SecretId=name)["SecretString"])
    api_key = secret.get("key") or secret.get("api_key")
    base_url = (
        secret.get("base_url")
        or secret.get("cloud_api_gateway_proxy_base_url_jwt")
        or secret.get("cloud_api_gateway_proxy_base_url_oidc")
    )
    if not api_key or not base_url:
        # Field names only. The values are the credential.
        raise RuntimeError(f"secret {name!r} has no key/base_url. Fields present: {sorted(secret.keys())}")
    base_url = base_url.rstrip("/")
    if base_url.endswith(UNIFIED_SUFFIX):
        base_url = base_url[: -len(UNIFIED_SUFFIX)]

    with _creds_lock:
        _creds_cache[cache_key] = (api_key, base_url)
    return api_key, base_url


def mint_bearer_token(api_key: str, base_url: str, *, verify: Any = None) -> str:
    """Exchange the API key for a bearer token at ``POST {base}/generatetoken``."""
    requests = _require_requests()
    response = requests.post(
        url=f"{base_url.rstrip('/')}/generatetoken",
        json={"key": api_key},
        headers={"accept": "application/json", "Content-Type": "application/json"},
        verify=_tls_verify(verify),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


# --------------------------------------------------------------------------- #
# Token provider — TTL cache with double-checked locking
# --------------------------------------------------------------------------- #


class ProxyTokenProvider:
    """Thread-safe, TTL-cached minter for the internal proxy bearer tokens.

    ``token()`` returns a cached token while it is younger than ``ttl_seconds``;
    ``token(force=True)`` always mints, which is the 401 retry path. Cache hits are lock-free,
    so a proactive refresh never serialises the steady-state request path. ``minter`` and
    ``clock`` are injectable so TTL expiry and re-mints are testable without a network.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        ttl_seconds: float = TOKEN_TTL_SECONDS,
        minter: Callable[[str, str], str] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self._ttl = ttl_seconds
        self._minter = minter or mint_bearer_token
        self._clock = clock
        self._token: str | None = None
        self._minted_at = 0.0
        self._lock = threading.Lock()

    def _fresh(self) -> bool:
        return self._token is not None and (self._clock() - self._minted_at) < self._ttl

    def token(self, *, force: bool = False) -> str:
        if not force and self._fresh():
            return self._token  # type: ignore[return-value]
        with self._lock:
            # Re-check: another thread may have refreshed while we waited.
            if not force and self._fresh():
                return self._token  # type: ignore[return-value]
            self._token = self._minter(self.api_key, self.base_url)
            self._minted_at = self._clock()
            return self._token


#: Process-global, so the chat client and the embedder mint one token between them and a
#: rebuilt stack reuses a still-valid one.
_provider_cache: dict[str, ProxyTokenProvider] = {}
_provider_lock = threading.Lock()


def shared_token_provider(
    secret_name: str | None = None,
    region: str | None = None,
    *,
    ttl_seconds: float = TOKEN_TTL_SECONDS,
) -> ProxyTokenProvider:
    """The shared :class:`ProxyTokenProvider` for ``(region, secret)``, built once."""
    cache_key = f"{_region(region)}:{_secret_name(secret_name)}"
    prov = _provider_cache.get(cache_key)
    if prov is not None:
        return prov
    with _provider_lock:
        prov = _provider_cache.get(cache_key)
        if prov is None:
            api_key, base_url = get_proxy_credentials(secret_name, region)
            prov = ProxyTokenProvider(api_key, base_url, ttl_seconds=ttl_seconds)
            _provider_cache[cache_key] = prov
        return prov


def _reset_caches() -> None:
    """Drop cached creds and providers. For tests only; never called at runtime."""
    with _creds_lock:
        _creds_cache.clear()
    with _provider_lock:
        _provider_cache.clear()


# --------------------------------------------------------------------------- #
# httpx auth flow — a valid token per request, re-mint on 401
# --------------------------------------------------------------------------- #


def _build_auth(provider: ProxyTokenProvider):
    """A per-request ``httpx.Auth`` authenticating from ``provider``.

    The OpenAI SDK derives an ``Authorization`` header from its (dummy) api_key; this flow
    runs in httpx's send path and overwrites it, so the dummy never reaches the proxy.
    """
    import asyncio  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    class _ProxyAuth(httpx.Auth):
        def sync_auth_flow(self, request):
            request.headers["Authorization"] = f"Bearer {provider.token()}"
            response = yield request
            if response.status_code == 401:
                request.headers["Authorization"] = f"Bearer {provider.token(force=True)}"
                yield request

        async def async_auth_flow(self, request):
            # Offload the rare mint to a thread: it does blocking socket I/O and the serve
            # path runs under LangGraph's event loop. Cache hits return inline.
            token = await asyncio.to_thread(provider.token)
            request.headers["Authorization"] = f"Bearer {token}"
            response = yield request
            if response.status_code == 401:
                token = await asyncio.to_thread(lambda: provider.token(force=True))
                request.headers["Authorization"] = f"Bearer {token}"
                yield request

    return _ProxyAuth()


# --------------------------------------------------------------------------- #
# extra_body — the proxy's required trace envelope (+ optional Claude thinking)
# --------------------------------------------------------------------------- #


def _thinking_effort(reasoning_effort: str | None) -> str | None:
    """Map ``llm_reasoning_effort`` onto the three efforts the internal proxy accepts.

    ``xhigh``/``max`` clamp to ``high``; ``none``/empty disables thinking. Faithful for Claude
    on the proxy; GPT models there should set effort ``none``.
    """
    if not reasoning_effort:
        return None
    effort = reasoning_effort.strip().lower()
    if effort in ("none", "off", ""):
        return None
    if effort in _THINKING_EFFORTS:
        return effort
    if effort in ("xhigh", "max"):
        return "high"
    return None


def build_extra_body(session_id: str, reasoning_effort: str | None) -> dict[str, Any]:
    """The literal top-level ``extra_body`` the proxy requires in the request JSON.

    The OpenAI client spreads its ``extra_body`` kwarg into the *root* of the body, so landing
    a literal ``extra_body`` key there means nesting it one level.
    """
    inner: dict[str, Any] = {"trace_data": {"session_id": session_id}}
    effort = _thinking_effort(reasoning_effort)
    if effort is not None:
        inner["additionalModelRequestFields"] = {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
        }
    return {"extra_body": inner}


# --------------------------------------------------------------------------- #
# Client builders
# --------------------------------------------------------------------------- #


def _http_clients(auth, *, verify: Any = None, timeout_s: float | None = None):
    """Sync + async httpx clients carrying the internal proxy auth flow. Both, because the serve path
    drives the model async and eval calls are sync.

    Two settings here are load-bearing rather than hygiene, both against the same failure —
    a turn that is neither answered nor failed:

    * **An explicit read timeout.** With a custom ``http_client``, ``ChatOpenAI``'s own
      ``timeout`` kwarg no longer governs the socket, so the client sits at httpx's defaults.
      The the internal proxy dev backend accepts a request and then sends no bytes; without a read timeout
      the harness's retries never engage.
    * **No keepalive.** The dev proxy silently drops idle connections while httpx's pool reuses
      one, then blocks forever waiting for a reply on a socket the server already closed — a
      hang no read timeout catches, because no byte arrives to start a read cycle. A long-lived
      worker hits this within minutes; a fresh single-shot client never does.
    """
    import httpx  # noqa: PLC0415

    resolved_verify = _tls_verify(verify)
    if resolved_verify is False:
        # Quiet urllib3's warning if requests (used for /generatetoken) shares the process.
        try:
            import urllib3  # noqa: PLC0415

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:  # pragma: no cover - urllib3 always present via requests
            pass
    # connect is quick or it never happens; read is the phase that hangs on this proxy.
    timeout = None if timeout_s is None else httpx.Timeout(timeout_s, connect=min(30.0, timeout_s))
    limits = httpx.Limits(max_keepalive_connections=0, keepalive_expiry=0.0)
    return (
        httpx.Client(auth=auth, verify=resolved_verify, timeout=timeout, limits=limits),
        httpx.AsyncClient(auth=auth, verify=resolved_verify, timeout=timeout, limits=limits),
    )


def build_chat_model(
    *,
    llm_model: str,
    reasoning_effort: str | None = None,
    max_output_tokens: int | None = None,
    request_timeout_s: float | None = 900.0,
    max_retries: int = 3,
    proxy_secret_name: str | None = None,
    proxy_region: str | None = None,
    proxy_verify: Any = None,
    session_id: str | None = None,
) -> Any:
    """A ``ChatOpenAI`` bound to the internal proxy.

    Returned raw so ``create_agent`` can still ``bind_tools`` on it. ``temperature`` is omitted
    because the default proxy model rejects sampling params.
    """
    from langchain_openai import ChatOpenAI  # noqa: PLC0415 (lazy: needs langchain-openai)

    provider = shared_token_provider(proxy_secret_name, proxy_region)
    auth = _build_auth(provider)
    http_client, http_async_client = _http_clients(auth, verify=proxy_verify, timeout_s=request_timeout_s)

    kwargs: dict[str, Any] = {
        "base_url": f"{provider.base_url}{UNIFIED_SUFFIX}",
        "model": llm_model,
        # The SDK requires a non-empty key; the auth flow overwrites the header derived from
        # it, so this value never reaches the proxy. Not a credential.
        "api_key": "internal-proxy",
        "temperature": None,
        "max_tokens": max_output_tokens or DEFAULT_PROXY_MAX_TOKENS,
        "http_client": http_client,
        "http_async_client": http_async_client,
        "extra_body": build_extra_body(session_id or "governed-bi", reasoning_effort),
        "max_retries": max_retries,
    }
    if request_timeout_s is not None:
        kwargs["timeout"] = request_timeout_s
    return ChatOpenAI(**kwargs)


#: Stand-in for an empty input. The proxy's embeddings route 400s on empty or whitespace-only
#: strings where the raw OpenAI API tolerates them. ``BaseEmbedder.embed`` refuses blanks
#: before they get here, so this only guards a direct caller of :func:`build_embeddings`;
#: substituting keeps the 1:1 input->vector correspondence callers index positionally.
_EMPTY_INPUT_PLACEHOLDER = "."

#: The proxy hard-rejects any embedding input over 8192 tokens, and
#: ``check_embedding_ctx_length`` is off below so the client does not auto-chunk. A corpus doc
#: can far exceed this — a BLOB-bearing table card renders ~215k tokens of sample values, which
#: killed a whole index build. Clipped on the ACTUAL token count, not chars: base64 text runs
#: ~2.4 chars/token, so a char budget under-counts and still overflows.
_EMBED_MAX_TOKENS = 8_000
_EMBED_ENCODING = "cl100k_base"  # text-embedding-3-* tokenizer


def _clip_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate to at most ``max_tokens`` tokens, falling back to a conservative char cap
    (2 chars/token, below the densest content observed) when tiktoken is unavailable."""
    try:
        import tiktoken  # noqa: PLC0415 (lazy; optional)

        enc = tiktoken.get_encoding(_EMBED_ENCODING)
        toks = enc.encode(text)
        if len(toks) <= max_tokens:
            return text
        return enc.decode(toks[:max_tokens])
    except Exception:
        return text[: max_tokens * 2]


def _sanitize_embedding_inputs(texts: list[str]) -> list[str]:
    out: list[str] = []
    for t in texts:
        if not (isinstance(t, str) and t.strip()):
            out.append(_EMPTY_INPUT_PLACEHOLDER)
        else:
            out.append(_clip_to_tokens(t, _EMBED_MAX_TOKENS))
    return out


def build_embeddings(
    *,
    embedding_model: str,
    embedding_dimensions: int | None = None,
    proxy_secret_name: str | None = None,
    proxy_region: str | None = None,
    proxy_verify: Any = None,
    session_id: str | None = None,
    request_timeout_s: float | None = 120.0,
) -> Any:
    """A LangChain ``OpenAIEmbeddings`` bound to the internal proxy.

    The embeddings route needs the same trace envelope, which rides through ``model_kwargs``.
    Over-long inputs are clipped because the proxy 400s on them; see ``_EMBED_MAX_TOKENS``.
    """
    from langchain_openai import OpenAIEmbeddings  # noqa: PLC0415 (lazy)

    class _ProxyOpenAIEmbeddings(OpenAIEmbeddings):
        """``OpenAIEmbeddings`` that sanitises inputs the proxy would reject."""

        def embed_documents(self, texts, *args, **kwargs):  # type: ignore[override]
            return super().embed_documents(_sanitize_embedding_inputs(list(texts)), *args, **kwargs)

        def embed_query(self, text, *args, **kwargs):  # type: ignore[override]
            return super().embed_query(_sanitize_embedding_inputs([text])[0], *args, **kwargs)

        async def aembed_documents(self, texts, *args, **kwargs):  # type: ignore[override]
            return await super().aembed_documents(_sanitize_embedding_inputs(list(texts)), *args, **kwargs)

        async def aembed_query(self, text, *args, **kwargs):  # type: ignore[override]
            return await super().aembed_query(_sanitize_embedding_inputs([text])[0], *args, **kwargs)

    provider = shared_token_provider(proxy_secret_name, proxy_region)
    auth = _build_auth(provider)
    http_client, http_async_client = _http_clients(auth, verify=proxy_verify, timeout_s=request_timeout_s)

    kwargs: dict[str, Any] = {
        "base_url": f"{provider.base_url}{UNIFIED_SUFFIX}",
        "model": embedding_model,
        "api_key": "internal-proxy",
        "http_client": http_client,
        "http_async_client": http_async_client,
        "model_kwargs": {
            "extra_body": {"extra_body": {"trace_data": {"session_id": session_id or "governed-bi"}}}
        },
        "check_embedding_ctx_length": False,
    }
    if embedding_dimensions:
        kwargs["dimensions"] = embedding_dimensions
    return _ProxyOpenAIEmbeddings(**kwargs)
