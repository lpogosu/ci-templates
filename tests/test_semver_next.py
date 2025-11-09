"""The version arithmetic in the semver-next composite action.

The script is exercised against real git repositories rather than a mocked
`git log`, because most of the ways it can be wrong involve git itself: tag
ordering, merge commits, an empty range, a shallow history.
"""

from __future__ import annotations

import itertools
import subprocess
from pathlib import Path

import pytest

from .conftest import ROOT, find_bash

BASH = find_bash()
SCRIPT = ROOT / ".github" / "actions" / "semver-next" / "next.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason="no POSIX bash available")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", "--initial-branch=main")
    git(repo, "config", "user.email", "ci@example.test")
    git(repo, "config", "user.name", "ci")
    git(repo, "config", "commit.gpgsign", "false")
    return repo


_serial = itertools.count()


def commit(repo: Path, subject: str, body: str = "") -> None:
    # One new file per commit, so that a branch and main never touch the same
    # path and `git merge` in the merge-commit test cannot conflict.
    marker = repo / f"file-{next(_serial)}.txt"
    marker.write_text(subject, encoding="utf-8")
    git(repo, "add", "-A")
    args = ["commit", "--quiet", "-m", subject]
    if body:
        args += ["-m", body]
    git(repo, *args)


def run_script(repo: Path, tmp_path: Path, **env_extra: str) -> dict[str, str]:
    assert BASH is not None
    output = tmp_path / "github_output.txt"
    output.write_text("", encoding="utf-8")
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir(exist_ok=True)
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "GITHUB_OUTPUT": output.as_posix(),
        "RUNNER_TEMP": runner_temp.as_posix(),
        **env_extra,
    }
    result = subprocess.run(
        [BASH, str(SCRIPT)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    return dict(
        line.split("=", 1)
        for line in output.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )


def test_first_release_uses_the_initial_version(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commit(repo, "feat: first thing")
    outputs = run_script(repo, tmp_path)
    assert outputs["bump"] == "initial"
    assert outputs["version"] == "0.1.0"
    assert outputs["tag"] == "v0.1.0"
    assert outputs["previous-tag"] == ""


def test_a_repository_with_no_commits_releases_nothing(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commit(repo, "chore: initial")
    git(repo, "tag", "v1.0.0")
    outputs = run_script(repo, tmp_path)
    assert outputs["bump"] == "none"
    assert outputs["version"] == ""
    assert outputs["tag"] == ""


def test_only_chores_release_nothing(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commit(repo, "feat: base")
    git(repo, "tag", "v1.2.3")
    commit(repo, "chore(deps): bump a pin")
    commit(repo, "docs: fix a typo")
    outputs = run_script(repo, tmp_path)
    assert outputs["bump"] == "none"
    assert outputs["commits"] == "2"


@pytest.mark.parametrize(
    ("subject", "body", "expected_bump", "expected_version"),
    [
        ("fix: a crash", "", "patch", "1.2.4"),
        ("perf: fewer allocations", "", "patch", "1.2.4"),
        ("feat: a flag", "", "minor", "1.3.0"),
        ("feat!: drop the flag", "", "major", "2.0.0"),
        ("refactor: move things", "BREAKING CHANGE: the config moved", "major", "2.0.0"),
        ("refactor: move things", "BREAKING-CHANGE: the config moved", "major", "2.0.0"),
    ],
)
def test_bump_table(
    tmp_path: Path, subject: str, body: str, expected_bump: str, expected_version: str
) -> None:
    repo = make_repo(tmp_path)
    commit(repo, "feat: base")
    git(repo, "tag", "v1.2.3")
    commit(repo, subject, body)
    outputs = run_script(repo, tmp_path)
    assert outputs["bump"] == expected_bump
    assert outputs["version"] == expected_version


def test_the_strongest_commit_in_the_range_decides(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commit(repo, "feat: base")
    git(repo, "tag", "v1.2.3")
    commit(repo, "fix: small")
    commit(repo, "feat: medium")
    commit(repo, "docs: irrelevant")
    outputs = run_script(repo, tmp_path)
    assert outputs["bump"] == "minor"
    assert outputs["version"] == "1.3.0"


def test_breaking_change_below_one_point_zero_is_only_a_minor(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commit(repo, "feat: base")
    git(repo, "tag", "v0.4.2")
    commit(repo, "feat!: rework the API")
    outputs = run_script(repo, tmp_path)
    assert outputs["bump"] == "minor"
    assert outputs["version"] == "0.5.0"


def test_tags_are_ordered_by_version_not_alphabetically(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commit(repo, "feat: base")
    git(repo, "tag", "v1.9.0")
    commit(repo, "feat: more")
    git(repo, "tag", "v1.10.0")
    commit(repo, "fix: something")
    outputs = run_script(repo, tmp_path)
    # Alphabetically v1.9.0 sorts above v1.10.0, which would measure the range
    # from the wrong tag and produce 1.9.1.
    assert outputs["previous-tag"] == "v1.10.0"
    assert outputs["version"] == "1.10.1"


def test_a_custom_prefix_is_honoured_in_both_directions(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commit(repo, "feat: base")
    git(repo, "tag", "release-2.0.0")
    commit(repo, "fix: a bug")
    outputs = run_script(repo, tmp_path, TAG_PREFIX="release-")
    assert outputs["previous-tag"] == "release-2.0.0"
    assert outputs["tag"] == "release-2.0.1"


def test_tags_of_other_projects_in_the_same_repository_are_ignored(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commit(repo, "feat: base")
    git(repo, "tag", "v1.0.0")
    git(repo, "tag", "nightly-2026-09-01")
    commit(repo, "fix: a bug")
    outputs = run_script(repo, tmp_path)
    assert outputs["previous-tag"] == "v1.0.0"


def test_the_changelog_groups_commits_and_keeps_the_ones_with_no_type(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commit(repo, "feat: base")
    git(repo, "tag", "v1.0.0")
    commit(repo, "feat(api): add an endpoint")
    commit(repo, "fix: a crash")
    commit(repo, "feat!: remove an endpoint")
    commit(repo, "tidy up the makefile")
    outputs = run_script(repo, tmp_path)
    changelog = Path(outputs["changelog-file"]).read_text(encoding="utf-8")

    assert changelog.startswith("## v2.0.0\n")
    assert "### Breaking changes" in changelog
    assert "- remove an endpoint" in changelog
    assert "- **api**: add an endpoint" in changelog
    # A commit that does not follow the convention still appears, because a
    # changelog that quietly drops history is worse than an untidy one.
    assert "- tidy up the makefile" in changelog
    assert "4 commit(s) since v1.0.0." in changelog


def test_merge_commits_do_not_inflate_the_count(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commit(repo, "feat: base")
    git(repo, "tag", "v1.0.0")
    git(repo, "checkout", "--quiet", "-b", "side")
    commit(repo, "fix: on the side")
    git(repo, "checkout", "--quiet", "main")
    commit(repo, "docs: on main")
    git(repo, "merge", "--quiet", "--no-ff", "-m", "Merge branch 'side'", "side")
    outputs = run_script(repo, tmp_path)
    assert outputs["commits"] == "2"
    assert outputs["bump"] == "patch"
