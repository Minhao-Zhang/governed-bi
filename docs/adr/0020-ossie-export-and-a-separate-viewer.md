# 0020: Ossie as the export format, and a viewer as its own project

- **Status:** **Reversed in part** (2026-10-23). §1 is superseded by
  [0022](0022-ossie-is-the-storage-format.md): Ossie is the storage format. The viewer stays its
  own project, but the app now embeds its build ([0021](0021-views-around-the-chat.md)).
- **Deciders:** project owner (2026-10-19; reversed in part 2026-10-23).
- **Scope:** how the semantic layer leaves this repository and how people look at it.

## What was decided

On 2026-10-19 the corpus stayed in its per-asset YAML layout, to keep per-definition diffs and
keep a draft spec out from under the engine. An exporter wrote one Ossie `0.2.0.dev0` model per
database, with a report of what it could not map, and a test ran Ossie's `validate.py` on every
model. Joins Ossie cannot express, cardinality, provenance and governance went into a
`GOVERNED_BI` extension. Reading Ossie as input waited for the 0.2.0 release.

The layer was to be viewed in `ossie-viewer`, a separate static app with no backend. The viewer
inside the chat app was rejected: different readers, a different release cycle, and it could not
be opened without a running server. The chat page's Details would only link to it.

## What is true instead

- Ossie is the only format on disk, one document per database, and what Ossie does not define
  goes in the extension's JSON string (0022). The exporter is deleted once the corpus is converted.
- `ossie-viewer` is still its own repository and still opens any Ossie file with no backend. The
  app embeds its single-file build as one of its three views and serves the corpus files to it
  (0021). The viewer's release cycle is pinned by copying a build at a known commit.
- Reading models written by other tools is still not a goal.

## What was learned

- **An export that must be lossless is a storage format.** The export lost `physical_type` on every
  column, which the model reads through `inspect_schema`, and the test pinned the loss as
  acceptable. Writing a reader and writer good enough to fix that is the whole cost of storing
  Ossie; keeping the YAML on top only adds a format.
- **Per-definition files were not what made diffs reviewable.** A canonical writer with no line
  folding gives one changed line per changed value inside a large file, and a hunk header can name
  the dataset or field.
- **"A different reader" was true and still not a reason to keep the viewer out.** The owner is
  both readers. Embedding a static build keeps the projects separate without making the owner
  leave the app.
