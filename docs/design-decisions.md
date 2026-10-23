# Design decisions

The binding decisions are the records in [adr/](adr/). [The documentation index](README.md) has one line for each. Four of them describe the app as it is now:

- [0017](adr/0017-a-served-chat-app-over-a-layer-the-user-brings.md): a served chat app over a semantic layer the user brings. Who may read which rows is a database role.
- [0018](adr/0018-every-answer-states-its-assumptions.md): every answer states its assumptions, and the server checks them against the SQL.
- [0019](adr/0019-one-plain-chat-page.md): one chat page.
- [0020](adr/0020-ossie-export-and-a-separate-viewer.md): the corpus stays in its YAML files, and Ossie is an export. The viewer is another repository.

An earlier record still holds where a later one has not replaced it. A reversed record says what was decided, what is true now, and what was learned.

Settings that can change an answer are declared in [`src/governed_bi/registry/settings.py`](../src/governed_bi/registry/settings.py). The environment variables that override them, and the secrets that belong in `.env`, are in [usage](usage.md). The page shows no trust score. ADR 0007 forbids one.
