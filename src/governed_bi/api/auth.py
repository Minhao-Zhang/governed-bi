"""Authentication and the state-write denial, for the self-hosted deployment.

Wired by ``langgraph.json``'s ``auth.path``. Without it ``LANGGRAPH_AUTH_TYPE`` defaults to
``"noop"`` and ``NoopAuthBackend`` returns ``UnauthenticatedUser()`` for every request, which is
what the 2026-08-10 audit found: **~82 routes with no authentication**, including every custom
route in ``routes.py`` and every platform route under ``/threads``, ``/runs``, ``/store``,
``/assistants``, ``/mcp`` and ``/a2a``.

Two things happen here, and they answer different findings.

**A1 — a shared key, checked in constant time.** This is a single-operator local deployment
(``langgraph dev`` on localhost), so the threat is not a multi-tenant one: it is that any web page
the operator visits can drive the engine and read the audit log, because the server's CORS default
is ``allow_origins=["*"]`` with ``allow_credentials=True``. Narrowing CORS (``langgraph.json``
``http.cors``) closes the browser half; this closes the rest — another process on the machine, a
container on the same bridge, anything that can reach the port.

A key in the UI's bundle is not a secret from the person using the browser, and is not meant to
be. It is meant to stop a caller who cannot read that bundle, which is exactly the cross-origin
attacker the wildcard CORS default invited.

**A2/A3 — ``threads.update`` is denied outright.** ``POST /threads/{id}/state`` forwards a
client-supplied ``as_node`` (``langgraph_api/api/threads.py:322``) straight into
``graph.aupdate_state``, and ``as_node`` is what bypasses ``input_schema``. Measured on this
graph: ``as_node=None`` is filtered as designed, ``as_node="accept"`` writes ``ServeState``
directly — which widens ``ToolBounds.licensed`` (a resume then executes SQL against it),
overwrites ``identity``, and forges ``corpus_content_hash``, the field every quotability gate
reads as the treatment identity. Authenticating the caller does not fix that; the write channel
should not exist. Nothing in this system updates thread state over HTTP: ``serve/`` writes state
from inside the graph, and the UI only creates threads and streams runs.

The route cannot be disabled on its own — ``http.disable_threads`` would take ``/threads`` with
it, and ``useStream`` needs that — so the denial is here, on the ``"update"`` action that
``langgraph_runtime_inmem.ops.Threads.State.post`` actually consults.

**What this does not do.** It is not multi-user authorization: one key means one principal, so
``resume_authorised``'s per-caller check (``govern/bounds.py``) is still the only thing that
distinguishes two callers, and on the streamed transport it is still not reached
(audit A5 — ``serve/accept.py``'s node passes no ``identity``). That is a separate fix.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Mapping

from langgraph_sdk import Auth

from governed_bi.govern.access import LOCAL_PRINCIPAL
from governed_bi.ports import Principal

__all__ = [
    "API_KEY_HEADER",
    "API_KEY_VAR",
    "api_key_refusal",
    "auth",
    "authenticated_principal",
]

#: Env var holding the shared key. Unset means every request is refused — see
#: :func:`_authenticate`. Named rather than inlined so the error text can quote it.
API_KEY_VAR = "GOVERNED_BI_API_KEY"

#: ``x-api-key`` because that is what the LangGraph SDK's own ``apiKey`` option sends, so the UI
#: needs one option rather than a custom header on every call, and ``useStream`` carries it for
#: free. The SDK also accepts ``Authorization: Bearer``; both are read below so a curl against
#: the running server does not have to know which one the UI chose.
API_KEY_HEADER = "x-api-key"

auth = Auth()


#: Refusal text for an unset variable. One string, because the custom-route middleware and the
#: platform handler must not disagree about what "no key configured" means.
_UNSET_DETAIL = (
    f"{API_KEY_VAR} is not set, so this server refuses every request. Set it to a value the "
    "client also sends (x-api-key, or Authorization: Bearer). It is the only thing standing "
    "between the engine and any process that can reach this port — the server's own CORS "
    "default is allow_origins=* with credentials."
)

_WRONG_DETAIL = f"missing or wrong {API_KEY_HEADER}"


def authenticated_principal() -> Principal:
    """The one :class:`~governed_bi.ports.Principal` this server executes turns for.

    **A function of nothing, because authentication here is a function of nothing.**
    :func:`_authenticate` compares one shared key; a shared key cannot distinguish two
    callers, so every request that gets past it is the same subject. Returning
    :data:`~governed_bi.govern.access.LOCAL_PRINCIPAL` rather than deriving a principal
    from the key is the same refusal ``_authenticate`` already makes about ``identity``.

    It exists so the composition root has **one** place to change. ``govern/access.py`` used
    to record that ``LOCAL_PRINCIPAL`` was "not imported by ``api/`` today" and now records
    that it is — this function being the importer — because the literal written twice is the
    drift this repository keeps auditing for. ``_authenticate`` returns ``LOCAL_PRINCIPAL.id``
    instead of its own copy of the string, so the identity the resume guard compares and the
    principal the access policy is asked about cannot come apart.

    A fork that authenticates people replaces this with something taking the request's
    claims. The moment it can return two different principals, audit findings A5, A6 and B1
    go live — the streamed transport passes no identity, ``/chat/resume`` validates by
    thread, and ``POST /threads/search`` returns the rendered context block. That trigger is
    recorded in ``docs/enterprise-fork.md`` and is deliberately not fixed here.
    """
    return LOCAL_PRINCIPAL


def _presented(headers: dict[bytes, bytes], authorization: str | None) -> str | None:
    """The key the caller presented, from either spelling."""
    raw = headers.get(API_KEY_HEADER.encode())
    if raw:
        return raw.decode("utf-8", "replace").strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def api_key_refusal(headers: Mapping[str, str]) -> str | None:
    """``None`` if this caller may proceed, else the refusal detail.

    Exists so ``routes.py``'s middleware — which the platform will not wrap, see the note there —
    decides with the same code as :func:`_authenticate` rather than with a second comparison that
    can drift. Takes ``str`` headers because that is what Starlette hands a middleware, while the
    ``Auth`` handler is given ``bytes``.
    """
    lowered = {k.lower().encode(): v.encode() for k, v in headers.items()}
    authorization = headers.get("authorization") or headers.get("Authorization")
    expected = (os.environ.get(API_KEY_VAR) or "").strip()
    if not expected:
        return _UNSET_DETAIL
    presented = _presented(lowered, authorization)
    if not presented or not hmac.compare_digest(presented, expected):
        return _WRONG_DETAIL
    return None


@auth.authenticate
async def _authenticate(
    headers: dict[bytes, bytes],
    authorization: str | None,
) -> Auth.types.MinimalUserDict:
    """Refuse unless the caller presents the configured key.

    **Fail closed on an unset variable, by refusing requests rather than by refusing to start.**
    Raising at import would leave the operator with a server that will not boot and an exception
    from a file they did not know was in the request path; refusing here names the variable in
    the 401 and is diagnosable from the client that hit it. Either way the answer to "no key
    configured" is *no*, never *everyone* — which is what the default backend answered.

    ``hmac.compare_digest`` rather than ``==``: the comparison is against a secret, and a
    short-circuiting compare on a local socket is still a timing oracle. Cheap to do correctly.
    """
    refusal = api_key_refusal(
        {k.decode("latin-1"): v.decode("latin-1") for k, v in headers.items()}
        | ({"authorization": authorization} if authorization else {})
    )
    if refusal is not None:
        raise Auth.exceptions.HTTPException(status_code=401, detail=refusal)
    # One key, one principal. `identity` is what `govern/bounds.resume_authorised` gates on, and
    # it is deliberately NOT derived from this: a single shared key cannot distinguish two
    # callers, so claiming it as an identity would make that check look enforced when it is not.
    # Spelled through `authenticated_principal()` so the string the resume guard compares and the
    # principal `api/graph_app.py` asks the access policy about are one value (ADR 0012 §8.1).
    return {"identity": authenticated_principal().id, "permissions": []}


@auth.on.threads.update
async def _no_state_writes(ctx: Auth.types.AuthContext, value: dict) -> None:
    """Deny ``POST /threads/{id}/state`` and ``PATCH /threads/{id}``.

    The action name is what ``ops.Threads.State.post`` passes to ``handle_event`` ("update"), so
    this is the hook that sits in front of the write. It also covers thread-metadata patches,
    which nothing in this system performs.

    A denial rather than a filter: a filter would restrict *which* threads may be written, and
    the finding is not about ownership. `ServeState` is the engine's own record — `identity`,
    `licensed`, `knobs_resolved`, `corpus_content_hash` — and a client that can set those can
    forge an audit record and widen the tool bounds the layer stack enforces against. There is no
    value of "which thread" that makes that acceptable.
    """
    raise Auth.exceptions.HTTPException(
        status_code=403,
        detail=(
            "thread state is not writable over HTTP. It carries the tool bounds the layer "
            "stack enforces against and the corpus hash every quotability gate reads; a "
            "client-supplied as_node bypasses the graph's input schema entirely (audit A2/A3). "
            "State is written from inside the graph."
        ),
    )

def _command_of(value: object) -> dict | None:
    """The ``command`` a run-creation event carries, from **where the runtime puts it**.

    ``None`` means there is none to inspect. Anything unexpected **raises**, which is the whole
    lesson of this function: the first version read ``value["command"]`` and returned early when it
    found nothing, so it allowed every request. ``langgraph_api`` nests it —
    ``models/run.py`` hands ``command`` to ``Runs.put`` inside the third positional argument and
    ``langgraph_runtime_inmem/ops.py`` wraps that whole dict as ``RunsCreate(kwargs=kwargs)``. The
    real value is ``{"thread_id": …, "assistant_id": …, "kwargs": {"input": …, "command": {…}}}``.
    Verified end to end: the exact payload the audit row claimed was refused was accepted, and the
    forged ``licensed`` reached the stored run. The gRPC/Postgres runtime nests it identically.

    **A security check that fails open on a shape it did not expect is how that happened**, so a
    ``command`` present under a type this cannot read is a refusal rather than a pass. That covers
    the case ``models/run.py`` creates when request encryption is on: ``command`` is then ciphertext
    rather than a dict, and returning early there would silently reopen this.

    Both locations are read, newest first, so a future version moving the key back to the top level
    does not reopen it either. What no reading of a payload can protect is the key being *renamed*;
    the end-to-end test is what covers that.
    """
    if not isinstance(value, Mapping):
        raise Auth.exceptions.HTTPException(
            status_code=403,
            detail=(
                f"run creation received {type(value).__name__}, which this authorisation hook "
                "cannot inspect for a state-writing command. Refused rather than allowed: a check "
                "that fails open on an unexpected shape is not a check (audit A4)."
            ),
        )
    for holder in (value.get("kwargs"), value):
        if not isinstance(holder, Mapping) or "command" not in holder:
            continue
        command = holder["command"]
        if command is None:
            return None
        if not isinstance(command, Mapping):
            raise Auth.exceptions.HTTPException(
                status_code=403,
                detail=(
                    f"run creation carries a command of type {type(command).__name__}, which this "
                    "hook cannot read — request encryption is the likely cause. Refused rather "
                    "than allowed, because `command.update` writes the tool bounds the layer stack "
                    "enforces against (audit A4)."
                ),
            )
        return dict(command)
    return None


#: Keys of a run-creation ``command`` that write state instead of resuming one.
#:
#: ``resume`` is the whole point of the paused-turn protocol and must stay: ``ask_user`` interrupts
#: and the client answers. ``update`` and ``goto`` are the two that write, and LangGraph applies
#: ``update`` through ``map_command``, which — unlike ``map_input`` — emits a write for **every** key
#: it is handed with no reference to the graph's input schema.
_STATE_WRITING_COMMANDS = ("update", "goto")


@auth.on.threads.create_run
async def _no_state_writes_on_a_new_run(ctx: Auth.types.AuthContext, value: dict) -> None:
    """Deny ``POST /threads/{id}/runs`` when it carries a state-writing ``command`` (audit A4).

    **The same defect as A2/A3, through a door that closing them left open.** Those closed
    ``POST /threads/{id}/state`` and the ``as_node`` write behind it; this is run creation, which
    dispatches ``("threads", "create_run")`` — an action no handler covered, so
    ``langgraph_api``'s default allowed it. The payload
    ``{"command": {"update": {"licensed": ["public.salaries"], "corpus_content_hash": "forged"}}}``
    therefore reached ``map_command``, which writes every key it is given.

    Both halves of that example matter and neither is a nuisance: ``licensed`` is the tool bound the
    layer stack enforces against, so widening it and then resuming executes SQL against tables the
    corpus never licensed — the model does not need a database handle if the caller can hand it one.
    And ``corpus_content_hash`` is the treatment identity every quotability gate reads, so a forged
    one stamps an audit record with a corpus the run never served.

    A denial and not a filter, for the reason the state hook gives: there is no value of "which
    thread" that makes forging the engine's own record acceptable. ``resume`` passes, because
    answering a clarification is what this endpoint is for.
    """
    command = _command_of(value)
    if command is None:
        return
    offending = sorted(k for k in _STATE_WRITING_COMMANDS if command.get(k) is not None)
    if not offending:
        return
    raise Auth.exceptions.HTTPException(
        status_code=403,
        detail=(
            f"command.{{{', '.join(offending)}}} is not accepted on run creation. `update` and "
            "`goto` write thread state, which carries the tool bounds the layer stack enforces "
            "against and the corpus hash every quotability gate reads (audit A4, the same defect "
            "as A2/A3 through a different route). `command.resume` is accepted — answering a "
            "paused turn is what this endpoint is for. State is written from inside the graph."
        ),
    )
