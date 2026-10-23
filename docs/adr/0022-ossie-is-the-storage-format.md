# 0022: Ossie is the storage format

- **Status:** Accepted (2026-10-23).
- **Deciders:** project owner.
- **Scope:** how the semantic layer is stored on disk. Supersedes §1 of
  [0020](0020-ossie-export-and-a-separate-viewer.md). The field-by-field placement is section 3 of
  the [plan](../reviews/2026-10-workspace-and-ossie-storage.md#3-where-each-field-goes), and the
  steps are its Track S. An [independent assessment](../reviews/2026-10-ossie-storage-assessment.md)
  is the record behind both.

## Context

0020 kept the per-asset YAML and added an Ossie export. A lossless export needs a reader and writer
that cover every field; keeping the YAML as well means two formats. The assessment wrote all of
BIRD as 57 Ossie files and read back all 13,304 assets equal, after one fix. The spec is still the
draft `0.2.0.dev0`.

## Decision

1. **One Ossie document per database**, at `<corpus root>/<schema>/<schema>.yaml`. It is the only
   format the loader reads. The per-asset YAML store and the exporter are deleted.
2. **Only what the spec defines.** An Ossie field holds a value when it means the same thing.
   `ai_context` uses only `instructions`. Everything else, including audit, governance, terms,
   few-shots and joins that are not many-to-one, goes in the JSON string of a `GOVERNED_BI`
   extension on the entity it belongs to. A 0.2.0 that closes `ai_context` to extra keys then
   changes nothing.
3. **Readable diffs.** The writer is canonical: spec key order, literal blocks for text with
   newlines, no folding, and extension JSON indented with sorted keys, so one changed key is one
   changed line. A test requires write, read, write to give the same bytes.
4. **The engine does not change.** `corpus/ossie.py` turns files into the same dataclasses, and is
   the only module that knows Ossie. The code keeps its own names (table, column, join, few-shot),
   because they reach the prompt.
5. **Proof of each step**: the assets load equal field by field, and the offline snapshot of every
   question's context is unchanged. The corpus hash changes; runs are compared by context hash,
   so the P16 baseline stays comparable.
6. **The spec pin** is one constant in `corpus/ossie.py`. A spec change is a change to that module
   and a load-then-write of the corpus.

Fields nothing reads (`confidence`, join `cost`, few-shot `complexity` and `bound_terms`, term
`related_terms`, column `is_unique`, `sample_values` and `references`, few-shot `body`) and the
negative-example type are deleted first, so they need no place.

Reading Ossie models written by other tools is not a goal yet.

## Rejected

- **Keep the YAML and convert both ways.** The same reader and writer, plus a second format.
- **Extra `ai_context` keys as plain YAML**, which the assessment recommended. Easier to read, but
  the files would depend on a property the draft spec may remove.
- **Several files per database.** The spec has no includes, so no part would be a valid model.
- **Renaming the code to Ossie's words.** Its types are not Ossie's: a join can be many-to-many, a
  column is physical while a field may be computed.
