from __future__ import annotations

import ast
from pathlib import Path


APP = Path(__file__).resolve().parents[2]
SRC = APP / "src" / "map_qa"


def test_only_evaluate_reads_labels_and_only_cli_imports_it():
    python_files = list(SRC.rglob("*.py"))
    label_refs = [p.name for p in python_files if "labels.csv" in p.read_text(encoding="utf-8") or "labels" in p.name.lower()]
    assert label_refs == ["evaluate.py"]

    evaluate_importers = []
    for path in python_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "map_qa.evaluate" for alias in node.names):
                    evaluate_importers.append(path.name)
            elif isinstance(node, ast.ImportFrom) and node.module == "map_qa" and any(alias.name == "evaluate" for alias in node.names):
                evaluate_importers.append(path.name)
    assert evaluate_importers == ["cli.py"]
