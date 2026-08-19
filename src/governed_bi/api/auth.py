"""The state-write denial. **No credential is required to reach this engine.**

Wired by ``langgraph.json``'s ``auth.path``, which stays wired even though nothing here
authenticates: the ``@auth.on`` handlers below are the reason the block exists. Without
``auth.path`` ``LANGGRAPH_AUTH_TYPE`` defaults to ``"noop"``, and a noop backend runs no
``@auth.on`` handler either — the state-write denials would go with it.

Two things happen here, and only one of them is about who is calling.

**A1/A7 were closed by a shared key, and the key was deliberately removed (2026-08-13).** The
2026-08-10 audit found ~82 routes with no authentication, and the fix was a shared
``GOVERNED_BI_API_KEY`` compared here in constant time and again in ``routes.py``'s middleware.
It worked, and it made the product unusable: LangGraph Studio's bootstrap fetches carry no custom
headers, so its probe arrived with no ``x-api-key`` and the whole surface answered 401 through a
middleware that runs outside CORS, presenting as an opaque network error rather than a refusal.
This is a single-operator local dev engine bound to ``127.0.0.1``, the operator is the only
principal, and the maintainer chose reachability over transport auth: **reaching the port is now
sufficient.**

What that re-exposes is A1 and A7, verbatim and on purpose. Anything that can reach the port —
another process on the machine, a container on the same bridge, a page whose origin
``langgraph.json``'s ``http.cors`` allow-list happens to permit — can drive the engine, spend
model budget, and read ``/audit/turns`` and ``/audit/turns/{id}/trace``, which return every
thread's SQL, the full turn records and an absolute path to the log directory. The binding to
loopback is the whole of the remaining control. A fork that exposes this port to a network has to
put the credential back, and ``docs/enterprise-fork.md`` is where that trigger is recorded.

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

**What this does not do.** It is not authorization of any kind beyond the two denials above.
There is one principal and no way to tell two callers apart, so ``resume_authorised``'s
per-caller check (``govern/bounds.py``) is still the only thing that distinguishes them. That was
true while the key existed too: a shared key cannot name a caller either.

**And it is not where the resume gate lives, because it cannot be.** ``@auth.on.threads.create_run``
below sees the resuming *request* — ``kwargs.command.resume`` and ``thread_id`` are both in the
``RunsCreate`` it is handed, and :func:`_command_of` already reads the first. It cannot see the
*thread*: ``Auth.types.AuthContext`` carries ``permissions``, ``user``, ``resource`` and
``action``, and the ``identity`` the paused turn was checkpointed with is graph state, reachable
only through a runtime connection and a compiled graph that no auth handler is given. Refusing
``command.resume`` outright is not available either — that is the paused-turn protocol. So B9 is
enforced inside the graph, at the line ``serve/tools.py``'s ``ask_user`` resumes on; what closes
audit A5's write half is ``serve/accept.py`` recording the authenticated caller as the turn's
``identity``, and it reads it from the ``configurable`` slot :func:`_authenticate`'s return value
lands in. ``serve/resume.py``'s module docstring is the long form.
"""

from __future__ import annotations

from collections.abc import Mapping

from langgraph_sdk import Auth

from governed_bi.govern.access import LOCAL_PRINCIPAL
from governed_bi.ports import Principal

__all__ = [
    "auth",
    "authenticated_principal",
]

auth = Auth()


def authenticated_principal() -> Principal:
    """The one :class:`~governed_bi.ports.Principal` this server executes turns for.

    **A function of nothing, because authentication here is a function of nothing.**
    :func:`_authenticate` admits every caller, so every request is the same subject — as it
    was when a shared key guarded the door, since a shared key cannot distinguish two callers
    either. The single-principal model did not change when the key went; it just stopped
    being proven by anything.

    It exists so the composition root has **one** place to change. ``govern/access.py`` used
    to record that ``LOCAL_PRINCIPAL`` was "not imported by ``api/`` today" and now records
    that it is — this function being the importer — because the literal written twice is the
    drift this repository keeps auditing for. ``_authenticate`` returns ``LOCAL_PRINCIPAL.id``
    instead of its own copy of the string, so the identity the resume guard compares and the
    principal the access policy is asked about cannot come apart.

    A fork that authenticates people replaces this with something taking the request's
    claims. The moment it can return two different principals, audit findings A5, A6 and B1
    go live — the deleted ``/chat/resume`` validated by
    thread, and ``POST /threads/search`` returns the rendered context block. That trigger is
    recorded in ``docs/enterprise-fork.md`` and is deliberately not fixed here.
    """
    return LOCAL_PRINCIPAL


@auth.authenticate
async def _authenticate() -> Auth.types.MinimalUserDict:
    """Admit every caller. **Reaching the port is the whole of the check.**

    It takes no parameters because it reads nothing: no ``headers``, no ``authorization``, no
    ``request``. A signature that accepted them would suggest something inspects them. Until
    2026-08-13 this compared ``GOVERNED_BI_API_KEY`` in constant time and raised 401 otherwise,
    closing audit A1 — see the module docstring for why the key was removed and what that
    re-exposes.

    **It cannot simply be deleted, and this is why ``langgraph.json`` keeps its ``auth`` block.**
    ``langgraph_api/auth/custom.py`` raises at startup when a loaded ``Auth`` object has no
    ``@auth.authenticate`` handler, and dropping ``auth.path`` instead would fall back to the noop
    backend, which runs no ``@auth.on`` handler either — the ``threads.update`` and
    ``threads.create_run`` denials below would go with it. Those are not authentication and do not
    depend on knowing the caller: they refuse a *payload*, from anyone.

    ``identity`` is spelled through :func:`authenticated_principal` rather than as a literal, so
    the string ``govern/bounds.resume_authorised`` compares and the principal
    ``api/graph_app.py`` asks the access policy about are one value (ADR 0012 §8.1). It is not
    evidence of anything about the caller — it was not when a shared key produced it either.

    **This return value is now load-bearing twice.** ``langgraph_api/models/run.py`` copies it into
    every run's ``configurable["langgraph_auth_user_id"]``, which is the only slot
    ``serve/resume.py`` will read a caller from: ``serve/accept.py`` stores it as the turn's
    ``identity`` and ``ask_user`` compares the resuming run's copy against it. A fork returning two
    different principals gets the B9 gate for free, on the streamed transport, without touching
    ``serve/``.
    """
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
