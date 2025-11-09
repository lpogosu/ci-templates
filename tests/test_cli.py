"""The command line surface: discovery, exit codes and output shape."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ci_lint import cli

from .conftest import FIXTURES, ROOT


def test_discovery_finds_every_shipped_template() -> None:
    found = {path.relative_to(ROOT).as_posix() for path in cli.discover(ROOT)}
    assert found == {
        ".github/workflows/ci.yml",
        ".github/workflows/container.yml",
        ".github/workflows/go.yml",
        ".github/workflows/node.yml",
        ".github/workflows/python.yml",
        ".github/workflows/release.yml",
        ".github/actions/coverage-gate/action.yml",
        ".github/actions/semver-next/action.yml",
        "gitlab/container.yml",
        "gitlab/go.yml",
        "gitlab/node.yml",
        "gitlab/python.yml",
        "gitlab/release.yml",
    }


def test_discovery_ignores_fixtures_and_examples() -> None:
    found = {path.relative_to(ROOT).as_posix() for path in cli.discover(ROOT)}
    assert not any(path.startswith(("tests/", "examples/")) for path in found)


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        (Path(".github/workflows/python.yml"), "workflow"),
        (Path(".github/actions/semver-next/action.yml"), "action"),
        (Path("gitlab/python.yml"), "gitlab"),
        (Path("tests/fixtures/bad/gitlab/loose.yml"), "gitlab"),
    ],
)
def test_classification_follows_the_path(path: Path, kind: str) -> None:
    assert cli.classify(path) == kind


def test_clean_run_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["check", "--root", str(ROOT)])
    assert code == 0
    assert "0 finding(s)" in capsys.readouterr().out


def test_findings_exit_one(capsys: pytest.CaptureFixture[str]) -> None:
    target = FIXTURES / "bad" / "workflows" / "no-timeout.yml"
    code = cli.main(["check", "--root", str(ROOT), str(target)])
    assert code == 1
    assert "job-timeout" in capsys.readouterr().out


def test_json_output_is_machine_readable(capsys: pytest.CaptureFixture[str]) -> None:
    target = FIXTURES / "bad" / "workflows" / "no-timeout.yml"
    cli.main(["check", "--root", str(ROOT), "--format", "json", str(target)])
    payload = json.loads(capsys.readouterr().out)
    assert payload["files_checked"] == 1
    assert {item["rule"] for item in payload["findings"]} == {
        "job-timeout",
        "permissions-missing",
    }


def test_rules_can_be_disabled_independently_of_the_schema(
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = FIXTURES / "bad" / "workflows" / "schema-violation.yml"
    assert cli.main(["check", "--root", str(ROOT), "--no-schema", str(target)]) == 0
    capsys.readouterr()
    assert cli.main(["check", "--root", str(ROOT), "--no-rules", str(target)]) == 1
    assert "schema" in capsys.readouterr().out


def test_an_empty_repository_is_an_error_not_a_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Exiting 0 on "found nothing to check" is how a renamed directory turns
    # into a permanently green pipeline.
    assert cli.main(["check", "--root", str(tmp_path)]) == 2
    assert "no CI files found" in capsys.readouterr().err


def test_a_broken_config_is_reported_before_anything_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "ci-lint.yaml").write_text("parity:\n  pairs:\n    - name: x\n", encoding="utf-8")
    assert cli.main(["check", "--root", str(tmp_path)]) == 2
    assert "parity pair" in capsys.readouterr().err


def test_rules_command_prints_the_catalogue(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["rules"]) == 0
    output = capsys.readouterr().out
    assert "pinned-uses" in output
    assert "gitlab-job-timeout" in output
