"""Invariants for GitHub Actions workflows and composite actions.

These are the properties a schema cannot express and actionlint does not
check: that every action is pinned to an immutable ref, that no workflow holds
a write permission nothing in it uses, that every job can be killed by a
timeout, and that a reusable workflow's declared interface matches the one it
actually reads.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from .config import Config
from .loader import Document, mapping, sequence
from .model import Finding

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# The tag lives in a trailing comment. It is not enforceable by the runner and
# that is exactly the point: humans read the comment, the runner reads the SHA.
VERSION_COMMENT_RE = re.compile(r"^\s*(v?\d[\w.\-+]*)")
EXPRESSION_RE = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
SECRET_REF_RE = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_\-]*)")
INPUT_REF_RE = re.compile(r"inputs\.([A-Za-z_][A-Za-z0-9_\-]*)")

# Secrets that exist without being declared by anybody.
BUILTIN_SECRETS = frozenset({"github_token"})

# Contexts an outsider can set on a public repository. Interpolating any of
# them into a shell body means the string becomes part of the script.
UNTRUSTED_CONTEXT_RE = re.compile(
    r"""
      github\.head_ref
    | github\.event\.(?:issue|pull_request|discussion)\.(?:title|body)
    | github\.event\.(?:comment|review|review_comment)\.body
    | github\.event\.pull_request\.head\.(?:ref|label)
    | github\.event\.pull_request\.head\.repo\.(?:default_branch|description|homepage)
    | github\.event\.head_commit\.(?:message|author\.(?:name|email))
    | github\.event\.commits\[[^\]]*\]\.(?:message|author\.(?:name|email))
    | github\.event\.workflow_run\.(?:head_branch|display_title)
    | github\.event\.workflow_run\.head_commit\.message
    | github\.event\.pages\[[^\]]*\]\.(?:page_name|title)
    """,
    re.VERBOSE,
)

# What counts as evidence that a write scope is actually needed. This is a
# heuristic and is documented as one: it proves that something in the file
# plausibly writes, never that the grant is minimal. The escape hatch is a
# `# ci-lint: allow permissions-unjustified-write -- <reason>` comment.
WRITE_EVIDENCE: dict[str, tuple[str, ...]] = {
    "contents": (
        "action-gh-release",
        "release-action",
        "gh release",
        "git push",
        "git tag",
        "create-pull-request",
        "actions/create-release",
        "stefanzweifel/git-auto-commit",
    ),
    "packages": (
        "ghcr.io",
        "docker/login-action",
        "docker push",
        "npm publish",
        "docker/build-push-action",
    ),
    "id-token": (
        "cosign",
        "sigstore",
        "configure-aws-credentials",
        "google-github-actions/auth",
        "azure/login",
        "--provenance",
        "attest",
    ),
    "security-events": ("upload-sarif", "codeql-action", ".sarif"),
    "pull-requests": (
        "gh pr ",
        "github-script",
        "create-pull-request",
        "create-or-update-comment",
        "pull-request-comment",
    ),
    "issues": ("gh issue", "github-script", "create-or-update-comment"),
    "actions": ("gh run ", "gh cache ", "github-script"),
    "deployments": ("deployment", "gh api"),
    "checks": ("check-run", "github-script", "test-reporter"),
    "statuses": ("gh api", "github-script"),
    "pages": ("actions/deploy-pages", "actions/upload-pages-artifact"),
    "attestations": ("actions/attest",),
    "discussions": ("gh api graphql", "github-script"),
    "repository-projects": ("gh project", "github-script"),
}

WRITE_VALUES = frozenset({"write"})


def _dump(value: Any) -> str:
    """Serialise a fragment back to YAML so evidence can be searched in it."""
    return yaml.safe_dump(value, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _triggers(data: dict[str, Any]) -> dict[str, Any]:
    return mapping(data.get("on"))


def is_reusable(data: dict[str, Any]) -> bool:
    return "workflow_call" in _triggers(data)


def _permission_map(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        return {"*": value}
    return {key: str(item) for key, item in mapping(value).items()}


def _check_uses(
    document: Document,
    step: dict[str, Any],
    config: Config,
    findings: list[Finding],
) -> None:
    reference = step.get("uses")
    if not isinstance(reference, str):
        return
    line = document.line_of(step, "uses")
    raw = document.raw_line(line)

    if reference.startswith("./") or reference.startswith("."):
        # A path inside the checked-out workspace; there is no ref to pin.
        return

    if reference.startswith("docker://"):
        if "@sha256:" not in reference:
            findings.append(
                Finding(
                    document.rel,
                    line,
                    "pinned-uses",
                    f"docker reference `{reference}` is not pinned to a digest",
                )
            )
        return

    if "@" not in reference:
        findings.append(
            Finding(document.rel, line, "pinned-uses", f"`{reference}` has no ref at all")
        )
        return

    target, _, ref = reference.rpartition("@")

    self_reference = config.self_reference
    if self_reference is not None and target.startswith(self_reference.repository + "/"):
        # Self-references cannot be pinned to a SHA: the commit that would be
        # pinned does not exist until this very file is committed. The release
        # tag is the only ref available, and ci-lint.yaml says which one.
        if ref != self_reference.ref:
            findings.append(
                Finding(
                    document.rel,
                    line,
                    "self-reference-ref",
                    f"`{reference}` should use the release ref `{self_reference.ref}`",
                )
            )
        return

    if not SHA_RE.match(ref):
        findings.append(
            Finding(
                document.rel,
                line,
                "pinned-uses",
                f"`{reference}` is pinned to `{ref}`, which is a mutable ref, not a commit SHA",
            )
        )
        return

    if not VERSION_COMMENT_RE.search(raw.split("#", 1)[1] if "#" in raw else ""):
        findings.append(
            Finding(
                document.rel,
                line,
                "pinned-uses-comment",
                f"`{target}` is pinned but the line carries no `# v...` comment, "
                "so nobody can tell what version this is",
            )
        )


def _check_run(document: Document, step: dict[str, Any], findings: list[Finding]) -> None:
    script = step.get("run")
    if not isinstance(script, str):
        return
    line = document.line_of(step, "run")
    for expression in EXPRESSION_RE.findall(script):
        match = UNTRUSTED_CONTEXT_RE.search(expression)
        if match is not None:
            findings.append(
                Finding(
                    document.rel,
                    line,
                    "script-injection",
                    f"`{match.group(0)}` is interpolated into a shell body; "
                    "pass it through `env:` and read the variable instead",
                )
            )


def _check_steps(
    document: Document,
    steps: list[Any],
    config: Config,
    findings: list[Finding],
) -> None:
    for step in steps:
        if not isinstance(step, dict):
            continue
        _check_uses(document, step, config, findings)
        _check_run(document, step, findings)


def _check_permissions(
    document: Document,
    scope_name: str,
    permissions: Any,
    evidence_text: str,
    container: dict[str, Any],
    findings: list[Finding],
    justify_scopes: bool = True,
) -> None:
    line = document.line_of(container, "permissions")
    entries = _permission_map(permissions)
    haystack = evidence_text.lower()

    for name, value in entries.items():
        if name == "*":
            if value == "write-all":
                findings.append(
                    Finding(
                        document.rel,
                        line,
                        "permissions-write-all",
                        f"{scope_name} grants write-all; name the scopes instead",
                    )
                )
            continue
        if value not in WRITE_VALUES or not justify_scopes:
            continue
        markers = WRITE_EVIDENCE.get(name)
        if markers is None:
            findings.append(
                Finding(
                    document.rel,
                    line,
                    "permissions-unjustified-write",
                    f"{scope_name} grants `{name}: write` and the checker knows no use for it",
                )
            )
            continue
        if not any(marker.lower() in haystack for marker in markers):
            findings.append(
                Finding(
                    document.rel,
                    line,
                    "permissions-unjustified-write",
                    f"{scope_name} grants `{name}: write` but nothing in it looks like it writes "
                    f"{name}",
                )
            )


def _check_jobs(
    document: Document,
    data: dict[str, Any],
    config: Config,
    findings: list[Finding],
) -> None:
    jobs = mapping(data.get("jobs"))
    top_permissions = data.get("permissions")

    if top_permissions is None and jobs:
        without = [name for name, job in jobs.items() if mapping(job).get("permissions") is None]
        if without:
            findings.append(
                Finding(
                    document.rel,
                    document.line_of(data, "jobs"),
                    "permissions-missing",
                    "no `permissions:` at workflow level and none on job(s) "
                    + ", ".join(sorted(without))
                    + "; the repository default applies, and it is not read-only everywhere",
                )
            )
    elif top_permissions is not None:
        _check_permissions(document, "workflow", top_permissions, document.text, data, findings)

    for name, raw_job in jobs.items():
        job = mapping(raw_job)
        if not job:
            continue

        steps = sequence(job.get("steps"))
        if job.get("permissions") is not None:
            # A job that calls a reusable workflow is forwarding permissions
            # into code this checker cannot see, so per-scope justification is
            # left to the called workflow, which is checked on its own. The
            # write-all check still applies: forwarding everything is never
            # the minimal grant, whatever the callee does.
            _check_permissions(
                document,
                f"job `{name}`",
                job["permissions"],
                _dump(job),
                job,
                findings,
                justify_scopes=bool(steps),
            )

        if steps:
            if job.get("timeout-minutes") is None:
                findings.append(
                    Finding(
                        document.rel,
                        document.line_of(data.get("jobs"), name),
                        "job-timeout",
                        f"job `{name}` has no timeout-minutes; the default is six hours "
                        "of a paid runner",
                    )
                )
            _check_steps(document, steps, config, findings)
        elif "uses" in job:
            # GitHub rejects timeout-minutes on a job that calls a reusable
            # workflow, so the timeout has to live in the called workflow.
            # Nothing to check here beyond the pin on the reference itself.
            _check_uses(document, job, config, findings)


def _check_call_interface(
    document: Document, data: dict[str, Any], findings: list[Finding]
) -> None:
    call = mapping(_triggers(data).get("workflow_call"))
    inputs = mapping(call.get("inputs"))
    secrets = mapping(call.get("secrets"))

    referenced_secrets = {name.lower() for name in SECRET_REF_RE.findall(document.text)}
    referenced_inputs = {name.lower() for name in INPUT_REF_RE.findall(document.text)}

    for name, raw_spec in secrets.items():
        if name.lower() not in referenced_secrets:
            findings.append(
                Finding(
                    document.rel,
                    document.line_of(call.get("secrets"), name),
                    "unused-secret",
                    f"secret `{name}` is declared but never read; a caller that passes it "
                    "believes it is doing something",
                )
            )
        del raw_spec

    declared = {name.lower() for name in secrets}
    for name in sorted(referenced_secrets - declared - BUILTIN_SECRETS):
        findings.append(
            Finding(
                document.rel,
                1,
                "undeclared-secret",
                f"`secrets.{name}` is read but not declared, so this workflow only works "
                "when the caller writes `secrets: inherit`",
            )
        )

    for name, raw_spec in inputs.items():
        spec = mapping(raw_spec)
        line = document.line_of(call.get("inputs"), name)
        if "type" not in spec or "description" not in spec:
            missing = [key for key in ("type", "description") if key not in spec]
            findings.append(
                Finding(
                    document.rel,
                    line,
                    "input-untyped",
                    f"input `{name}` is missing {' and '.join(missing)}",
                )
            )
        if spec.get("required") is not True and "default" not in spec:
            findings.append(
                Finding(
                    document.rel,
                    line,
                    "input-no-default",
                    f"input `{name}` is optional and has no default, so a caller that omits it "
                    "gets an empty string with no warning",
                )
            )
        if name.lower() not in referenced_inputs:
            findings.append(
                Finding(
                    document.rel,
                    line,
                    "unused-input",
                    f"input `{name}` is declared but never used",
                )
            )


def check_workflow(document: Document, config: Config) -> list[Finding]:
    data = mapping(document.data)
    findings: list[Finding] = []
    _check_jobs(document, data, config, findings)
    if is_reusable(data):
        _check_call_interface(document, data, findings)
    return findings


def check_action(document: Document, config: Config) -> list[Finding]:
    """Invariants for a composite action's action.yml."""
    data = mapping(document.data)
    findings: list[Finding] = []
    runs = mapping(data.get("runs"))
    steps = sequence(runs.get("steps"))
    _check_steps(document, steps, config, findings)

    inputs = mapping(data.get("inputs"))
    referenced = {name.lower() for name in INPUT_REF_RE.findall(document.text)}
    for name, raw_spec in inputs.items():
        spec = mapping(raw_spec)
        line = document.line_of(data.get("inputs"), name)
        if "description" not in spec:
            findings.append(
                Finding(document.rel, line, "input-untyped", f"input `{name}` has no description")
            )
        if spec.get("required") is not True and "default" not in spec:
            findings.append(
                Finding(
                    document.rel,
                    line,
                    "input-no-default",
                    f"input `{name}` is optional and has no default",
                )
            )
        if name.lower() not in referenced:
            findings.append(
                Finding(
                    document.rel, line, "unused-input", f"input `{name}` is declared but never used"
                )
            )
    return findings
