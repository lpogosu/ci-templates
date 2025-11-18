"""Findings and the rule catalogue."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, order=True)
class Finding:
    """One violated invariant, at one place in one file."""

    path: str
    line: int
    rule: str
    message: str

    def format(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.message}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    suppressed: list[Finding] = field(default_factory=list)
    files_checked: int = 0

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def extend(self, findings: list[Finding]) -> None:
        self.findings.extend(findings)

    def merge(self, other: Report) -> None:
        self.findings.extend(other.findings)
        self.suppressed.extend(other.suppressed)
        self.files_checked += other.files_checked

    @property
    def ok(self) -> bool:
        return not self.findings


# Every rule the checker can emit, with the one-line rationale that is printed
# by `ci-lint rules`. Keeping it in one place means a rule cannot be added
# without saying out loud what it is for, and the test suite asserts that
# every emitted rule id appears here.
RULES: dict[str, str] = {
    "schema": "the file does not validate against the vendored CI schema",
    "yaml": "the file is not parseable YAML",
    "pinned-uses": "`uses:` must name a commit SHA, or a digest for a docker: reference",
    "pinned-uses-comment": "a pinned `uses:` must carry its version in a trailing comment",
    "self-reference-ref": "a reference into this repository must use the declared release ref",
    "job-timeout": "every job that runs steps must set timeout-minutes",
    "permissions-missing": "a workflow must declare `permissions:` rather than inherit them",
    "permissions-write-all": "`write-all` grants every scope, including unknown ones",
    "permissions-unjustified-write": "a `write` scope with no step that plausibly needs it",
    "unused-secret": "a reusable workflow declares a secret it never reads",
    "undeclared-secret": "a secret is read but not declared, so it needs `secrets: inherit`",
    "unused-input": "a declared input is never referenced",
    "input-untyped": "a workflow_call input must declare both `type` and `description`",
    "input-no-default": "an optional input must have a default, otherwise it arrives empty",
    "script-injection": "attacker-controlled context interpolated straight into a `run:` body",
    "suppression-without-reason": "`ci-lint: allow` needs a reason of at least 20 characters",
    "gitlab-job-timeout": "every GitLab job must set `timeout`, directly or through `extends`",
    "gitlab-pinned-image": "a job image must be digest-pinned, directly or through an input",
    "gitlab-input-undocumented": "a component input must declare both `type` and `description`",
    "gitlab-unused-input": "a declared component input is never interpolated",
    "parity-missing-input": "an input exists on one CI system and not the other, with no reason",
    "parity-default-mismatch": "the same input has different defaults on the two CI systems",
    "parity-stale-exception": "ci-lint.yaml records a parity exception that no longer applies",
}
