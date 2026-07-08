"""Eval / telemetry service: the shared scoreboard (Architecture §8; D3/D4).

Near-term eval = the BIRD-Obfuscation dataset (verified ground truth). Headline
metric = **execution accuracy (EX)** vs gold; no hand-grading of semantic layers.

Three arms, all scored on EX (run on the ``rename_decoy`` instance; ``base`` as
sanity reference — only the corpus differs across arms, the physical DB is one):

1. no semantic layer
2. curator-built layer
3. gold layer (deterministic de-obfuscation oracle)

Moat = the share of the obfuscation-induced accuracy drop the curator recovers;
Arm 3 = the recoverable *reference line* (not a strict ceiling — Arm 2 can beat
it on skill-sensitive questions, since gold has no skills). Arm 2 vs 3 = curator
quality.

The **curator reads ``train_final.jsonl`` only**; grading is on held-out
``test_final.jsonl`` (disjoint seeded split = structural leakage prevention).

- ``ex``          — execution-accuracy scoring vs gold SQL.
- ``arms``        — the three-arm harness + free behavioral signals.
- ``gold``        — Arm 3: the deterministic de-obfuscation oracle.
- ``refuse_gate`` — refusal precision/recall on a held-out unanswerable set.
"""
