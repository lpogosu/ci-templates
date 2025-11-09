"""The YAML layer, which two subtle things depend on."""

from __future__ import annotations

from pathlib import Path

import pytest

from ci_lint.loader import Document, YamlError, mapping, sequence


def write(tmp_path: Path, text: str, name: str = "workflow.yml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_on_stays_a_string_key(tmp_path: Path) -> None:
    """YAML 1.1 says `on` is a boolean. GitHub says it is the trigger key.

    PyYAML implements YAML 1.1, so without the narrowed bool resolver the
    parsed document has a key `True` and every trigger-aware rule silently
    finds nothing.
    """
    document = Document(write(tmp_path, "name: x\non: push\njobs: {}\n"), tmp_path)
    assert document.data["on"] == "push"
    assert True not in document.data


@pytest.mark.parametrize("literal", ["yes", "no", "off", "y", "n"])
def test_other_yaml_one_point_one_booleans_stay_strings(tmp_path: Path, literal: str) -> None:
    document = Document(write(tmp_path, f"value: {literal}\n"), tmp_path)
    assert document.data["value"] == literal


@pytest.mark.parametrize(("literal", "expected"), [("true", True), ("false", False)])
def test_real_booleans_still_parse(tmp_path: Path, literal: str, expected: bool) -> None:
    document = Document(write(tmp_path, f"value: {literal}\n"), tmp_path)
    assert document.data["value"] is expected


def test_key_positions_survive_nesting(tmp_path: Path) -> None:
    document = Document(
        write(
            tmp_path,
            "name: x\n"  # 1
            "on: push\n"  # 2
            "jobs:\n"  # 3
            "  build:\n"  # 4
            "    steps:\n"  # 5
            "      - uses: a/b@c\n",  # 6
        ),
        tmp_path,
    )
    assert document.line_of(document.data, "jobs") == 3
    jobs = document.data["jobs"]
    assert document.line_of(jobs, "build") == 4
    step = jobs["build"]["steps"][0]
    assert document.line_of(step, "uses") == 6


def test_mapping_does_not_copy_and_so_keeps_positions(tmp_path: Path) -> None:
    document = Document(write(tmp_path, "a:\n  b: 1\n"), tmp_path)
    assert mapping(document.data) is document.data
    assert document.line_of(mapping(document.data), "a") == 1


def test_mapping_and_sequence_tolerate_the_wrong_shape() -> None:
    assert mapping("a string") == {}
    assert mapping(None) == {}
    assert mapping({1: "x"}) == {"1": "x"}
    assert sequence({"not": "a list"}) == []


def test_a_multi_document_file_keeps_both_documents(tmp_path: Path) -> None:
    document = Document(
        write(tmp_path, "spec:\n  inputs: {}\n---\njob:\n  script: [echo]\n"), tmp_path
    )
    assert len(document.documents) == 2
    assert "spec" in document.documents[0]
    assert "job" in document.documents[1]


def test_a_parse_error_carries_the_line(tmp_path: Path) -> None:
    with pytest.raises(YamlError) as excinfo:
        Document(write(tmp_path, "a: 1\n b: 2\n"), tmp_path)
    assert excinfo.value.line == 2
