"""The plain-name check bites a docstring and leaves a string literal alone."""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]


def _tool():
    path = REPO / "tools" / "check_plain_names.py"
    spec = importlib.util.spec_from_file_location("check_plain_names", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_docstring_with_the_retired_word_exits_1(tmp_path: pathlib.Path) -> None:
    (tmp_path / "sample.py").write_text(
        'def sample() -> None:\n    """licensed set"""\n',
        encoding="utf-8",
    )
    assert _tool().main(["--root", str(tmp_path)]) == 1


def test_a_string_literal_with_the_same_words_exits_0(tmp_path: pathlib.Path) -> None:
    (tmp_path / "sample.py").write_text('LABEL = "licensed set"\n', encoding="utf-8")
    assert _tool().main(["--root", str(tmp_path)]) == 0


def test_a_guidance_word_is_printed_and_does_not_fail(tmp_path: pathlib.Path, capsys) -> None:
    (tmp_path / "sample.py").write_text("shape = 1\n", encoding="utf-8")
    assert _tool().main(["--root", str(tmp_path)]) == 0
    assert "shape 1" in capsys.readouterr().out


def test_a_git_ignored_file_is_not_scanned(tmp_path: pathlib.Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("vendor/\n", encoding="utf-8")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "notes.md").write_text("licensed set\n", encoding="utf-8")
    assert _tool().main(["--root", str(tmp_path)]) == 0
    (tmp_path / "notes.md").write_text("licensed set\n", encoding="utf-8")
    assert _tool().main(["--root", str(tmp_path)]) == 1
