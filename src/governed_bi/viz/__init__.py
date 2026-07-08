"""Viz: the audit + edit cockpit (D6 human gate, operationalized).

A simple interactive local app (not a static site) that lets a human audit the
AI-built corpus, correct it, then save to disk and open a PR. Editing the git
YAML/MD **is** editing the source of truth (D9), so this is a git-editing
front-end, not a derived store.

Editability = the tier model: Facts read-only · Inference editable · Audit
system-written (a human edit appends a ``source: human`` provenance entry) ·
Governance human-only (where ``governance.excluded`` is set). Editing flips
``draft → certified``; the audit trail becomes three-party (proposer → adversary
→ human).

Save → PR: dev/BIRD commits directly (developer is reviewer); prod/enterprise goes
through owner + PR + CI. See ``docs/viz.md``.
"""
