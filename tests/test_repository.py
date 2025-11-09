"""Checks about this repository itself.

The templates are the product, so they are also test subjects: they have to
pass their own checker, and the anti-patterns they replace have to fail it.
The second half matters more than the first — a checker that accepts
everything also passes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ci_lint import cli
from ci_lint.config import Config
from ci_lint.github import is_reusable
from ci_lint.loader import Document, mapping

from .conftest import ROOT, check, rules_of

REUSABLE_WORKFLOWS = sorted(
    path for path in (ROOT / ".github" / "workflows").glob("*.yml") if path.name != "ci.yml"
)
BEFORE_EXAMPLES = sorted((ROOT / "examples").rglob("*.before.yml"))
AFTER_EXAMPLES = sorted((ROOT / "examples").rglob("*.after.yml"))


def test_the_repository_passes_its_own_checker(repo_config: Config) -> None:
    report = cli.run(cli.discover(ROOT), repo_config)
    assert report.findings == [], "\n".join(f.format() for f in report.findings)


@pytest.mark.parametrize("path", REUSABLE_WORKFLOWS, ids=lambda p: p.name)
def test_every_shipped_workflow_is_callable(path: Path) -> None:
    assert is_reusable(mapping(Document(path, ROOT).data))


@pytest.mark.parametrize("path", REUSABLE_WORKFLOWS, ids=lambda p: p.name)
def test_every_input_documents_itself(path: Path) -> None:
    data = mapping(Document(path, ROOT).data)
    call = mapping(mapping(data["on"])["workflow_call"])
    for name, spec in mapping(call.get("inputs")).items():
        assert mapping(spec).get("description"), f"{path.name}: {name} has no description"
    for name, spec in mapping(call.get("secrets")).items():
        assert mapping(spec).get("description"), f"{path.name}: secret {name} is undocumented"


@pytest.mark.parametrize("path", BEFORE_EXAMPLES, ids=lambda p: p.parent.name)
def test_the_before_examples_are_rejected(path: Path, repo_config: Config) -> None:
    """The anti-pattern each example illustrates has to actually be caught.

    This is the test that stops the checker from becoming decorative: the
    "before" files are what real repositories look like, and if they pass then
    the rules are not saying anything.
    """
    report = check(path, repo_config)
    assert report.findings, f"{path} was accepted"


def test_the_before_examples_are_caught_for_the_right_reasons(repo_config: Config) -> None:
    found: set[str] = set()
    for path in BEFORE_EXAMPLES:
        found |= rules_of(check(path, repo_config))
    assert {"pinned-uses", "job-timeout", "permissions-missing"} <= found
    assert {"gitlab-pinned-image", "gitlab-job-timeout"} <= found


@pytest.mark.parametrize("path", AFTER_EXAMPLES, ids=lambda p: p.parent.name)
def test_the_after_examples_are_accepted(path: Path, repo_config: Config) -> None:
    report = check(path, repo_config)
    assert report.findings == [], "\n".join(f.format() for f in report.findings)


def effective_lines(path: Path) -> int:
    """Lines that configure something, ignoring comments and blank lines."""
    return sum(
        1
        for raw in path.read_text(encoding="utf-8").splitlines()
        if raw.strip() and not raw.lstrip().startswith("#")
    )


def test_the_after_examples_are_much_shorter() -> None:
    """The table in the README is a measurement, so it is measured here.

    The floor is deliberately low. A Go pipeline is small to begin with and
    shrinks by a factor of two and a half, not ten; the README prints the real
    number for each example rather than one flattering ratio.
    """
    for after in AFTER_EXAMPLES:
        before = after.with_name(after.name.replace(".after.", ".before."))
        shrink = effective_lines(before) / effective_lines(after)
        assert shrink >= 2, f"{after.parent.name}: shrink factor {shrink:.1f}"


def test_every_vendored_schema_is_present() -> None:
    from ci_lint import schema

    for name in (schema.GITHUB_WORKFLOW, schema.GITHUB_ACTION, schema.GITLAB_CI):
        assert (schema.SCHEMA_DIR / name).is_file()
