"""Every scope-probe row names the variant, model and exact prompt that produced it.

Two artifacts of the same variant name once held 14 and 2 refusals of the same 200 questions,
because the variant's text was revised between them and no row said which text ran.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parents[2] / "tools"


def _probe_module() -> Any:
    spec = importlib.util.spec_from_file_location("bi_scope_probe", TOOLS / "bi_scope_probe.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["bi_scope_probe"] = module
    spec.loader.exec_module(module)
    return module


class _Yes:
    async def ainvoke(self, messages: list[Any], config: Any = None, **kwargs: Any) -> Any:
        return type("Reply", (), {"text": "YES", "usage_metadata": None, "response_metadata": {}})()


def test_each_row_carries_the_stamp(tmp_path: Path) -> None:
    probe = _probe_module()
    out = tmp_path / "rows.jsonl"
    stamp = {"variant": "v2", "model": "m", "provider": "openai", "effort": None, "prompt_sha256": "abc"}
    questions = [{"question_id": "q1", "question": "how many?"}, {"question_id": "q2", "question": "list them"}]

    asyncio.run(probe._probe(questions, _Yes(), out, 2, {"bi_scope": "v2"}, "- s: a schema", stamp))

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert all({k: row[k] for k in stamp} == stamp for row in rows)
    assert {row["outcome"] for row in rows} == {"clear"}
