"""The rules, exercised against fixtures that are wrong on purpose.

A linter is only worth its runtime if it can be shown to reject things. Every
rule below has a fixture that violates it and a fixture that does not, and the
last test in this file asserts that no rule in the catalogue is left without
one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ci_lint.config import Config
from ci_lint.model import RULES

from .conftest import FIXTURES, check, rules_of

BAD_WORKFLOWS: dict[str, set[str]] = {
    "unpinned.yml": {"pinned-uses", "pinned-uses-comment"},
    "permissive.yml": {"permissions-unjustified-write", "permissions-write-all"},
    "no-timeout.yml": {"job-timeout", "permissions-missing"},
    "leaky-interface.yml": {
        "unused-secret",
        "undeclared-secret",
        "unused-input",
        "input-untyped",
        "input-no-default",
    },
    "injection.yml": {"script-injection"},
    "empty-suppression.yml": {"suppression-without-reason"},
    "malformed.yml": {"yaml"},
    "schema-violation.yml": {"schema"},
    "self-reference.yml": {"self-reference-ref"},
}

BAD_GITLAB: dict[str, set[str]] = {
    "loose.yml": {
        "gitlab-job-timeout",
        "gitlab-pinned-image",
        "gitlab-input-undocumented",
        "gitlab-unused-input",
    },
    "unpinned-input-default.yml": {"gitlab-pinned-image"},
}

BAD_ACTIONS: dict[str, set[str]] = {
    "loose-action": {"input-untyped", "input-no-default", "unused-input", "pinned-uses"},
}

GOOD_FILES = [
    FIXTURES / "good" / "workflows" / "clean.yml",
    FIXTURES / "good" / "workflows" / "suppressed.yml",
    FIXTURES / "good" / "gitlab" / "clean.yml",
    FIXTURES / "good" / "actions" / "tidy-action" / "action.yml",
]


@pytest.mark.parametrize(("name", "expected"), sorted(BAD_WORKFLOWS.items()))
def test_bad_workflow_is_rejected(name: str, expected: set[str], repo_config: Config) -> None:
    report = check(FIXTURES / "bad" / "workflows" / name, repo_config)
    assert expected <= rules_of(report), rules_of(report)


@pytest.mark.parametrize(("name", "expected"), sorted(BAD_GITLAB.items()))
def test_bad_gitlab_template_is_rejected(
    name: str, expected: set[str], repo_config: Config
) -> None:
    report = check(FIXTURES / "bad" / "gitlab" / name, repo_config)
    assert expected <= rules_of(report), rules_of(report)


@pytest.mark.parametrize(("name", "expected"), sorted(BAD_ACTIONS.items()))
def test_bad_action_is_rejected(name: str, expected: set[str], repo_config: Config) -> None:
    report = check(FIXTURES / "bad" / "actions" / name / "action.yml", repo_config)
    assert expected <= rules_of(report), rules_of(report)


@pytest.mark.parametrize("path", GOOD_FILES, ids=lambda p: p.name)
def test_good_fixture_is_silent(path: Path, repo_config: Config) -> None:
    report = check(path, repo_config)
    assert report.findings == []


def test_suppression_with_a_reason_hides_the_finding(repo_config: Config) -> None:
    report = check(FIXTURES / "good" / "workflows" / "suppressed.yml", repo_config)
    assert [f.rule for f in report.suppressed] == ["job-timeout"]


def test_suppression_without_a_reason_becomes_its_own_finding(repo_config: Config) -> None:
    report = check(FIXTURES / "bad" / "workflows" / "empty-suppression.yml", repo_config)
    # The muted rule is gone and a louder one has taken its place, so the
    # comment cannot be used to make the file quiet.
    assert rules_of(report) == {"suppression-without-reason"}


def test_findings_carry_the_line_they_are_about(repo_config: Config) -> None:
    report = check(FIXTURES / "bad" / "workflows" / "unpinned.yml", repo_config)
    by_line = {f.line: f.rule for f in report.findings}
    assert by_line[14] == "pinned-uses"
    assert by_line[17] == "pinned-uses-comment"


def test_docker_reference_needs_a_digest_not_a_tag(repo_config: Config) -> None:
    report = check(FIXTURES / "bad" / "workflows" / "unpinned.yml", repo_config)
    messages = [f.message for f in report.findings if "docker://" in f.message]
    assert messages == ["docker reference `docker://alpine:3.22` is not pinned to a digest"]


def test_caller_job_needs_no_timeout(repo_config: Config, tmp_path: Path) -> None:
    """A job that calls a reusable workflow cannot set timeout-minutes.

    GitHub rejects the key there, so requiring it would make every correct
    caller file fail. The timeout has to live in the called workflow, and the
    rule has to know that.
    """
    workflow = tmp_path / ".github" / "workflows" / "caller.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "name: caller\n"
        "on: push\n"
        "permissions:\n"
        "  contents: read\n"
        "jobs:\n"
        "  test:\n"
        "    uses: lpogosu/ci-templates/.github/workflows/python.yml@v1\n",
        encoding="utf-8",
    )
    report = check(workflow, Config(root=tmp_path, self_reference=repo_config.self_reference))
    assert rules_of(report) == set()


def test_every_rule_in_the_catalogue_has_a_fixture(repo_config: Config) -> None:
    emitted: set[str] = set()
    for name in BAD_WORKFLOWS:
        emitted |= rules_of(check(FIXTURES / "bad" / "workflows" / name, repo_config))
    for name in BAD_GITLAB:
        emitted |= rules_of(check(FIXTURES / "bad" / "gitlab" / name, repo_config))
    for name in BAD_ACTIONS:
        emitted |= rules_of(check(FIXTURES / "bad" / "actions" / name / "action.yml", repo_config))
    # The parity rules are driven from a whole repository rather than a single
    # file, so they are covered in test_parity.py and excluded here.
    parity_rules = {rule for rule in RULES if rule.startswith("parity-")}
    assert emitted == set(RULES) - parity_rules
