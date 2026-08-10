"""The the internal proxy provider carries no credential in source, and is still an ``Embedder``.

Network-free, and narrow on purpose. This file exists because of one actual incident: the
2026-08-07 BIRD run went through a provider adapter that lived only as untracked files on a
server, and the first thing anyone does with such a file is paste it in with its endpoint and
its account id still in it. Two properties are asserted, both of which a paste would break:

1. **Nothing identifying is a default.** With the environment empty, every entry point refuses
   and names the variable it wants — it does not fall back to a committed secret id.
2. **It satisfies the port.** ``ProxyEmbedder`` is an ``Embedder`` whose ``model`` is
   provider-qualified, so ``embedding_knobs`` records it as a declared knob and a the internal proxy-served
   vector cannot land in an OpenAI-served run's cache entry.

There is no mocked round trip of the proxy API here; a fake that answers the way we imagine
the proxy does would assert our imagination.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = [needs("I")]

#: Every variable the adapter reads. Cleared per test so a developer's own shell cannot make
#: the refusal assertions pass or fail for the wrong reason.
PROXY_VARS = ("GOVERNED_BI_PROXY_SECRET", "GOVERNED_BI_PROXY_REGION", "GOVERNED_BI_PROXY_CA_BUNDLE")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    from governed_bi.model import proxy_gateway

    for name in PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
    proxy_gateway._reset_caches()
    yield monkeypatch
    proxy_gateway._reset_caches()


def test_the_variable_names_are_what_the_module_exports_and_reads(clean_env) -> None:
    """The names in :data:`PROXY_VARS` are the module's own, not this file's guesses."""
    from governed_bi.model import PROXY_CA_BUNDLE_VAR, PROXY_REGION_VAR, PROXY_SECRET_NAME_VAR

    assert (PROXY_SECRET_NAME_VAR, PROXY_REGION_VAR, PROXY_CA_BUNDLE_VAR) == PROXY_VARS


def test_no_endpoint_or_account_id_is_committed_in_the_adapter() -> None:
    """The paste check, over the source itself.

    A secret *name* is not a secret value, but it is an account-shaped identifier and it was
    the one thing hardcoded in the version that ran. Assert the two modules contain no URL and
    no ``key = "..."``-shaped literal; the credential and the endpoint both arrive from Secrets
    Manager, addressed by a variable read at call time.
    """
    import re

    from governed_bi.model import proxy_embedder, proxy_gateway

    for module in (proxy_gateway, proxy_embedder):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert not re.search(r"https?://", source), f"{module.__name__} carries a literal URL"
        # `api_key="internal-proxy"` is the SDK's non-empty-string requirement and is overwritten
        # by the auth flow before the request leaves, so it is exempt by value.
        for match in re.finditer(r"""(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*["']([^"']+)["']""", source):
            assert match.group(2) == "internal-proxy", f"{module.__name__} assigns a literal {match.group(1)}"


def test_an_unnamed_secret_refuses_and_says_which_variable(clean_env) -> None:
    """Fails closed, at call time, on every entry point — never with a committed default."""
    from governed_bi.model import PROXY_SECRET_NAME_VAR
    from governed_bi.model.proxy_gateway import (
        build_chat_model,
        build_embeddings,
        get_proxy_credentials,
        shared_token_provider,
    )

    for call in (
        get_proxy_credentials,
        shared_token_provider,
        lambda: build_embeddings(embedding_model="text-embedding-3-large"),
        lambda: build_chat_model(llm_model="claude-opus-4-8"),
    ):
        with pytest.raises(RuntimeError, match=PROXY_SECRET_NAME_VAR):
            call()

    # ...and the refusal is what stops the constructor, rather than a later network call.
    from governed_bi.model import ProxyEmbedder

    with pytest.raises(RuntimeError, match=PROXY_SECRET_NAME_VAR):
        ProxyEmbedder()


def test_the_credential_is_read_at_call_time_not_import_time(clean_env) -> None:
    """Setting the variable after import is enough. A module-level read would have frozen the
    empty value at import and made the provider unusable from a process that loads ``.env``
    itself — which is exactly what ``tools/run_datalake_eval.py`` does."""
    from governed_bi.model.proxy_gateway import get_proxy_credentials

    seen: dict[str, object] = {}

    def fake_session(region_name=None):
        seen["region"] = region_name
        raise AssertionError("unreachable: the boto3 call is stubbed out below")

    clean_env.setenv("GOVERNED_BI_PROXY_SECRET", "a-secret-name-supplied-by-the-environment")
    clean_env.setenv("GOVERNED_BI_PROXY_REGION", "somewhere-1")
    clean_env.setattr(
        "governed_bi.model.proxy_gateway._require_boto3",
        lambda: type("_Boto3", (), {"Session": staticmethod(fake_session)}),
    )

    with pytest.raises(AssertionError):
        get_proxy_credentials()
    assert seen["region"] == "somewhere-1"


def test_the_embedder_satisfies_the_port_and_records_a_provider_qualified_knob(clean_env) -> None:
    """``isinstance`` against the runtime-checkable Protocol, and the knob it contributes.

    The credential lookup is the only thing stubbed: an adapter that satisfied the port only
    when a live proxy answered would be untestable at exactly the moment it matters.
    """
    from governed_bi.model import ProxyEmbedder, embedding_knobs
    from governed_bi.ports import Embedder
    from governed_bi.register.knobs import comparability_keys, config_hash_keys

    clean_env.setenv("GOVERNED_BI_PROXY_SECRET", "a-secret-name-supplied-by-the-environment")
    clean_env.setattr(
        "governed_bi.model.proxy_gateway.get_proxy_credentials",
        lambda *a, **k: ("not-a-real-key", "https://proxy.invalid"),
    )

    embedder = ProxyEmbedder(embedding_dimensions=64)

    assert isinstance(embedder, Embedder)
    # Provider-qualified (ports.py:140): the same model name over two gateways is two
    # identities, and `embedding_model` is what keeps them out of one cache entry.
    assert embedder.model == "proxy:text-embedding-3-large"
    assert embedder.model != "openai:text-embedding-3-large"
    assert embedder.dimensions == 64  # the requested width, without a probe request

    knobs = embedding_knobs(embedder)
    assert knobs == {
        "embedding_model": "proxy:text-embedding-3-large",
        "embedding_dimensions": 64,
        # Not "openai". This knob had no writer at all, so every proxy-served arm recorded the
        # register default while `embedding_model` on the same row said `proxy:`.
        "embedding_provider": "proxy",
    }
    assert set(knobs) <= comparability_keys()
    assert set(knobs) <= config_hash_keys()

    # The port's blank rule holds here too, and it is `BaseEmbedder` that enforces it -- so it
    # refuses before the proxy's own empty-input substitution can turn a corpus defect into a
    # vector. No request is made, which is why this runs without a provider.
    with pytest.raises(ValueError):
        embedder.embed(["a customers table", "   "])
