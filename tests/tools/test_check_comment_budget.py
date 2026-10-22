"""The comment budget check fails long comments, long docstrings, and history words."""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]


def _tool():
    path = REPO / "tools" / "check_comment_budget.py"
    spec = importlib.util.spec_from_file_location("check_comment_budget", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(root: pathlib.Path, rel: str, text: str) -> None:
    path = root.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _comments(count: int) -> str:
    return "".join(f"# line {number}\n" for number in range(1, count + 1))


def _function_doc(count: int) -> str:
    parts = ["def sample() -> None:", '    """one']
    parts.extend(f"    {number}" for number in range(2, count))
    parts.append(f'    {count}"""')
    return "\n".join(parts) + "\n"


def _module_doc(count: int) -> str:
    parts = ['"""one']
    parts.extend(str(number) for number in range(2, count))
    parts.append(f'{count}"""')
    return "\n".join(parts) + "\n"


def test_a_four_line_comment_block_exits_1_and_a_three_line_block_exits_0(
    tmp_path: pathlib.Path, capsys
) -> None:
    tool = _tool()
    _write(tmp_path, "src/governed_bi/sample.py", _comments(4))
    assert tool.main(["--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "src/governed_bi/sample.py:1: comment-run: 4 lines" in out
    assert "comment-budget: 1" in out
    _write(tmp_path, "src/governed_bi/sample.py", _comments(3))
    assert tool.main(["--root", str(tmp_path)]) == 0
    assert "comment-budget: 0" in capsys.readouterr().out


def test_a_blank_line_ends_a_comment_run(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "src/governed_bi/sample.py", _comments(3) + "\n" + _comments(3))
    assert _tool().main(["--root", str(tmp_path)]) == 0


def test_a_trailing_comment_does_not_extend_a_run(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "src/governed_bi/sample.py", _comments(3) + 'value = 1  # line\n')
    assert _tool().main(["--root", str(tmp_path)]) == 0


def test_a_shebang_does_not_count_toward_a_run(tmp_path: pathlib.Path, capsys) -> None:
    tool = _tool()
    _write(tmp_path, "src/governed_bi/sample.py", "#!/usr/bin/env python\n" + _comments(3))
    assert tool.main(["--root", str(tmp_path)]) == 0
    _write(tmp_path, "src/governed_bi/sample.py", "#!/usr/bin/env python\n" + _comments(4))
    assert tool.main(["--root", str(tmp_path)]) == 1
    assert "src/governed_bi/sample.py:2: comment-run: 4 lines" in capsys.readouterr().out


def test_a_six_line_function_docstring_exits_1_and_a_five_line_one_exits_0(
    tmp_path: pathlib.Path, capsys
) -> None:
    tool = _tool()
    _write(tmp_path, "src/governed_bi/sample.py", _function_doc(6))
    assert tool.main(["--root", str(tmp_path)]) == 1
    assert "src/governed_bi/sample.py:2: docstring: function 6 lines" in capsys.readouterr().out
    _write(tmp_path, "src/governed_bi/sample.py", _function_doc(5))
    assert tool.main(["--root", str(tmp_path)]) == 0


def test_a_six_line_class_docstring_exits_1(tmp_path: pathlib.Path, capsys) -> None:
    text = _function_doc(6).replace("def sample() -> None:", "class Sample:", 1)
    _write(tmp_path, "src/governed_bi/sample.py", text)
    assert _tool().main(["--root", str(tmp_path)]) == 1
    assert "docstring: class 6 lines" in capsys.readouterr().out


def test_an_eleven_line_module_docstring_fails_under_src_and_passes_under_tools(
    tmp_path: pathlib.Path, capsys
) -> None:
    tool = _tool()
    _write(tmp_path, "src/governed_bi/sample.py", _module_doc(11))
    assert tool.main(["--root", str(tmp_path)]) == 1
    assert "src/governed_bi/sample.py:1: docstring: module 11 lines" in capsys.readouterr().out
    _write(tmp_path, "src/governed_bi/sample.py", _module_doc(10))
    assert tool.main(["--root", str(tmp_path)]) == 0
    _write(tmp_path, "tools/sample.py", _module_doc(11))
    assert tool.main(["--root", str(tmp_path)]) == 0


def test_a_function_docstring_under_tools_is_not_exempt(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "tools/sample.py", _module_doc(11) + "\n" + _function_doc(6))
    assert _tool().main(["--root", str(tmp_path)]) == 1


def test_a_comment_saying_used_to_exits_1(tmp_path: pathlib.Path, capsys) -> None:
    _write(tmp_path, "src/governed_bi/sample.py", "# it used to return none\n")
    assert _tool().main(["--root", str(tmp_path)]) == 1
    assert "src/governed_bi/sample.py:1: history: used to" in capsys.readouterr().out


def test_the_other_history_phrases_exit_1(tmp_path: pathlib.Path) -> None:
    tool = _tool()
    for phrase in ("no longer", "Previously", "was changed"):
        _write(tmp_path, "src/governed_bi/sample.py", f"# {phrase} noted\n")
        assert tool.main(["--root", str(tmp_path)]) == 1


def test_a_string_literal_saying_used_to_exits_0(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "src/governed_bi/sample.py", 'LABEL = "used to"\n')
    assert _tool().main(["--root", str(tmp_path)]) == 0


def test_until_and_a_shared_prefix_do_not_match(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "src/governed_bi/sample.py", "# until the cap\n# used together\n")
    assert _tool().main(["--root", str(tmp_path)]) == 0


def test_a_docstring_with_a_date_exits_1(tmp_path: pathlib.Path, capsys) -> None:
    _write(tmp_path, "src/governed_bi/sample.py", 'def sample() -> None:\n    """noted 2026-09-18"""\n')
    assert _tool().main(["--root", str(tmp_path)]) == 1
    assert "history: 2026-09-18" in capsys.readouterr().out


def test_positional_paths_limit_the_scan(tmp_path: pathlib.Path) -> None:
    tool = _tool()
    _write(tmp_path, "src/governed_bi/keep/ok.py", "value = 1\n")
    _write(tmp_path, "src/governed_bi/drop/bad.py", _comments(4))
    assert tool.main(["--root", str(tmp_path), "src/governed_bi/keep"]) == 0
    assert tool.main(["--root", str(tmp_path), "src/governed_bi/drop/bad.py"]) == 1
    assert tool.main(["--root", str(tmp_path)]) == 1


def test_a_missing_positional_path_exits_2(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "src/governed_bi/sample.py", "value = 1\n")
    assert _tool().main(["--root", str(tmp_path), "src/governed_bi/missing"]) == 2


def test_count_prints_rule_and_package_and_exits_0(tmp_path: pathlib.Path, capsys) -> None:
    _write(tmp_path, "src/governed_bi/serve/sample.py", _comments(4))
    _write(tmp_path, "tests/api/sample.py", "# it used to return none\n")
    assert _tool().main(["--root", str(tmp_path), "--count"]) == 0
    out = capsys.readouterr().out
    assert "comment-run src/governed_bi/serve 1" in out
    assert "history tests/api 1" in out
    assert "comment-budget:" not in out


def test_a_git_ignored_file_is_not_scanned(tmp_path: pathlib.Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("src/governed_bi/hidden.py\n", encoding="utf-8")
    _write(tmp_path, "src/governed_bi/hidden.py", _comments(4))
    assert _tool().main(["--root", str(tmp_path)]) == 0
    _write(tmp_path, "src/governed_bi/shown.py", _comments(4))
    assert _tool().main(["--root", str(tmp_path)]) == 1
