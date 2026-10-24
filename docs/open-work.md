# Open work

Items that are finished are deleted from this page. What remains is work the current tree has not done.

The baseline is in [measurement](measurement.md). One row of run B, `train_4799`, is still crashed, because the resume guard refused the retry after `.claude/launch.json` changed.

The layer viewer, `ossie-viewer`, is its own repository and stands at V3. Its V4 is to open the Ossie examples and all 57 BIRD models without errors, with the largest drawn quickly enough to use. The app then embeds its build as the semantic layer view (W4 of the [plan](reviews/2026-10-workspace-and-ossie-storage.md)).

The lake printer reports accuracy, coverage, and disagreement only. The assumption figures ([`eval/report.py`](../src/governed_bi/eval/report.py)) and the cost per question (from the token totals on each row) are computed separately, as they were for the baseline.

Details lists the SQL, the checks, the retrieved tables, the model, and the time. [ADR 0019](adr/0019-one-plain-chat-page.md) also asks which part of the question each piece of the SQL came from, and the page has no such section.
