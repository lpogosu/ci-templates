"""The GitHub/GitLab interface comparison.

Every case here is built as a throwaway two-file repository, so the test says
what the rule does rather than what this repository happens to look like today.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ci_lint import parity
from ci_lint.config import ConfigError
from ci_lint.config import load as load_config

WORKFLOW = """\
name: demo
on:
  workflow_call:
    inputs:
{inputs}
permissions:
  contents: read
jobs:
  run:
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    steps:
      - run: echo ${{{{ inputs.coverage-min }}}}
"""

COMPONENT = """\
spec:
  inputs:
{inputs}
---
run:
  image: "python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285"
  timeout: 5 minutes
  script:
    - echo "$[[ inputs.coverage_min ]]"
"""

CONFIG = """\
parity:
  pairs:
    - name: demo
      github: .github/workflows/demo.yml
      gitlab: gitlab/demo.yml
  exceptions:
{exceptions}
"""


def build(
    root: Path,
    github_inputs: str,
    gitlab_inputs: str,
    exceptions: str = "    []",
) -> None:
    workflow = root / ".github" / "workflows" / "demo.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(WORKFLOW.format(inputs=github_inputs), encoding="utf-8")

    component = root / "gitlab" / "demo.yml"
    component.parent.mkdir(parents=True, exist_ok=True)
    component.write_text(COMPONENT.format(inputs=gitlab_inputs), encoding="utf-8")

    (root / "ci-lint.yaml").write_text(CONFIG.format(exceptions=exceptions), encoding="utf-8")


MATCHING_GITHUB = """\
      coverage-min:
        description: Minimum coverage.
        type: number
        default: 80
"""
MATCHING_GITLAB = """\
    coverage_min:
      description: Minimum coverage.
      type: number
      default: 80
"""


def test_kebab_and_snake_case_are_the_same_input(tmp_path: Path) -> None:
    build(tmp_path, MATCHING_GITHUB, MATCHING_GITLAB)
    assert parity.check(load_config(tmp_path)) == []


def test_a_different_default_is_a_finding(tmp_path: Path) -> None:
    build(tmp_path, MATCHING_GITHUB, MATCHING_GITLAB.replace("default: 80", "default: 70"))
    findings = parity.check(load_config(tmp_path))
    assert [f.rule for f in findings] == ["parity-default-mismatch"]
    assert "'80' on GitHub and '70' on GitLab" in findings[0].message


def test_an_input_on_only_one_side_is_a_finding(tmp_path: Path) -> None:
    extra = (
        MATCHING_GITHUB
        + """\
      runs-on:
        description: Runner label.
        type: string
        default: ubuntu-24.04
"""
    )
    build(tmp_path, extra, MATCHING_GITLAB)
    findings = parity.check(load_config(tmp_path))
    assert [f.rule for f in findings] == ["parity-missing-input"]
    assert "`runs-on` exists on GitHub but not on GitLab" in findings[0].message


def test_a_recorded_reason_settles_the_difference(tmp_path: Path) -> None:
    extra = (
        MATCHING_GITHUB
        + """\
      runs-on:
        description: Runner label.
        type: string
        default: ubuntu-24.04
"""
    )
    exceptions = """\
    - pipeline: demo
      input: runs-on
      reason: GitLab selects runners per pipeline through default:tags, not per job.
"""
    build(tmp_path, extra, MATCHING_GITLAB, exceptions)
    assert parity.check(load_config(tmp_path)) == []


def test_a_wildcard_reason_covers_every_pipeline(tmp_path: Path) -> None:
    extra = (
        MATCHING_GITHUB
        + """\
      runs-on:
        description: Runner label.
        type: string
        default: ubuntu-24.04
"""
    )
    exceptions = """\
    - pipeline: "*"
      input: runs-on
      reason: GitLab selects runners per pipeline through default:tags, not per job.
"""
    build(tmp_path, extra, MATCHING_GITLAB, exceptions)
    assert parity.check(load_config(tmp_path)) == []


def test_an_exception_that_is_no_longer_needed_is_reported(tmp_path: Path) -> None:
    exceptions = """\
    - pipeline: demo
      input: coverage-min
      reason: This used to differ and no longer does, which nobody would notice.
"""
    build(tmp_path, MATCHING_GITHUB, MATCHING_GITLAB, exceptions)
    findings = parity.check(load_config(tmp_path))
    assert [f.rule for f in findings] == ["parity-stale-exception"]


def test_a_reason_has_to_be_a_sentence(tmp_path: Path) -> None:
    exceptions = """\
    - pipeline: demo
      input: runs-on
      reason: different
"""
    build(tmp_path, MATCHING_GITHUB, MATCHING_GITLAB, exceptions)
    with pytest.raises(ConfigError, match="at least 20 characters"):
        load_config(tmp_path)


def test_defaults_compare_by_value_not_by_yaml_spelling() -> None:
    assert parity.render_default(True) == "true"
    assert parity.render_default(15) == parity.render_default(15.0)
    assert parity.render_default(None) == ""
    assert parity.render_default(" spaced ") == "spaced"
