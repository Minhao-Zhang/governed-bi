# Open work

Items that are finished are deleted from this page. What remains is work the current tree has not done.

The baseline has not been measured. P16 runs the current model setup twice, with crashed rows resumed, and records execution accuracy, coverage, run-to-run disagreement, cost per question, and the assumption figures, for the dev half, the holdout half, and the total. Until that run is written into [measurement](measurement.md), those figures are PLACEHOLDER (P16).

The layer viewer is a separate project, `ossie-viewer`, with no backend ([ADR 0020](adr/0020-ossie-export-and-a-separate-viewer.md)). Its repository is separate from this one.

- V0. An empty static web app whose lint, types, tests, and build pass in CI, and whose build opens from disk with no server.
- V1. A file from a picker, a URL, or a drop is checked against the Ossie schema, the version is shown, and datasets and relationships are drawn as a graph.
- V2. Each dataset's fields, the metrics and what they reference, the AI context, and each extension by vendor name.
- V3. A URL can open on one dataset, field, or metric, and an unknown name says it is not in the file.
- V4. The Ossie examples and the exported BIRD layer open without errors, and the largest model draws quickly enough to use. After that, the chat page's Details can link to the viewer.

The lake printer reports accuracy, coverage, and disagreement only. The assumption figures ([`eval/report.py`](../src/governed_bi/eval/report.py)) and the cost per question (from the token totals on each row) are computed elsewhere, and P16 has to put both in the result.

Details lists the SQL, the checks, the retrieved tables, the model, and the time. [ADR 0019](adr/0019-one-plain-chat-page.md) also asks which part of the question each piece of the SQL came from, and the page has no such section.
