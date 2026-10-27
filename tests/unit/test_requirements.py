"""Spec requirements R1 (independence) and R3 (no outcome leakage), checked on the code."""

from __future__ import annotations

import ast
import builtins
import io
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "dde"


def _python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_r1_no_salp_imports_or_sap_inputs() -> None:
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for n in names:
                assert not n.split(".")[0].lower().startswith("salp"), f"{path}: imports {n}"
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"sap[-_][\w]*\.json|sap_manifest|\.sap\b", text, re.I), path


def test_r1_dependencies_are_the_allowed_ones() -> None:
    pyproject = (SRC.parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    block = pyproject.split("dependencies = [", 1)[1].split("]", 1)[0]
    lines = [d.strip().strip('",') for d in block.splitlines()]
    deps = {re.split(r"[<>=!~ ]", d)[0] for d in lines if d}
    deps.discard("")
    assert deps <= {"tree-sitter", "tree-sitter-java", "pandas"}, deps


def test_r3_no_code_names_integration_outputs() -> None:
    """No module refers to RePatch results, conflict logs, or outcome labels as inputs."""
    pattern = re.compile(r"(repatch_(log|result|output|status)|conflict_log|outcome_label|"
                         r"cherry_pick_(log|result)|\bL[123]\b)", re.I)
    for path in _python_files():
        if path.name in ("manifest.py", "validate.py"):  # these refuse such columns by name
            continue
        assert not pattern.search(path.read_text(encoding="utf-8")), path


def test_r3_cli_offers_no_outcome_input() -> None:
    from dde.cli import build_parser

    parser = build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    for name, p in sub.choices.items():  # type: ignore[attr-defined]
        for action in p._actions:
            for opt in action.option_strings:
                assert not re.search(r"repatch|outcome|label|conflict", opt, re.I), (name, opt)


@pytest.mark.integration
def test_r3_extract_never_opens_outcome_files(scenario, fake_rm, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Plant outcome files next to the inputs and record every file the run opens."""
    from dde.cli import main
    from scenario import manifest

    for name in ("repatch_results.csv", "outcomes.csv", "conflicts.log"):
        (tmp_path / name).write_text("C01,L3\n")
    opened: list[str] = []
    real_open = builtins.open

    def spy(file, *a, **k):  # type: ignore[no-untyped-def]
        opened.append(str(file))
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy)  # what pathlib uses
    m = manifest(scenario, tmp_path / "cases.csv")
    assert main(["extract", "--manifest", str(m), "--repos", str(scenario.repo.parent),
                 "--rm", str(fake_rm), "--rm-cache", str(tmp_path / "c"),
                 "--out", str(tmp_path / "out")]) == 0
    assert opened
    assert not [p for p in opened if re.search(r"repatch_results|outcomes|conflicts", p)]
