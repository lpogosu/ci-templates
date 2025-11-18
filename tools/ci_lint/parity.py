"""Cross-checking the GitHub and GitLab sides of the same pipeline.

Two CI systems cannot share code: a reusable workflow is a GitHub artefact and
a component is one GitLab YAML file that can carry no scripts. What they can
share is an interface, and this is the only mechanical guarantee that they
still do — that `coverage_min` means the same thing and defaults to the same
number on both, and that an input added to one side is either added to the
other or written down in ci-lint.yaml with a reason.
"""

from __future__ import annotations

from typing import Any

from .config import Config
from .gitlab import spec_inputs
from .loader import Document, YamlError, mapping
from .model import Finding


def canonical(name: str) -> str:
    """GitHub inputs are kebab-case, GitLab inputs are snake_case.

    Forcing one convention on to the other platform would make every caller
    file look foreign, so the names differ by convention and are compared
    after normalisation.
    """
    return name.replace("-", "_").lower()


def render_default(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _github_inputs(document: Document) -> dict[str, Any]:
    triggers = mapping(mapping(document.data).get("on"))
    call = mapping(triggers.get("workflow_call"))
    return mapping(call.get("inputs"))


def check(config: Config) -> list[Finding]:
    findings: list[Finding] = []
    used_exceptions: set[tuple[str, str]] = set()

    for pair in config.parity_pairs:
        try:
            github = Document(config.root / pair.github, config.root)
            gitlab = Document(config.root / pair.gitlab, config.root)
        except YamlError:
            # The per-file pass already reported the parse error; repeating it
            # here would double every message on a broken file.
            continue

        github_inputs = _github_inputs(github)
        gitlab_inputs, gitlab_container = spec_inputs(gitlab)

        github_by_key = {canonical(name): (name, spec) for name, spec in github_inputs.items()}
        gitlab_by_key = {canonical(name): (name, spec) for name, spec in gitlab_inputs.items()}

        for key in sorted(set(github_by_key) | set(gitlab_by_key)):
            on_github = github_by_key.get(key)
            on_gitlab = gitlab_by_key.get(key)

            if on_github is None or on_gitlab is None:
                present, missing = (
                    ("GitHub", "GitLab") if on_gitlab is None else ("GitLab", "GitHub")
                )
                name = (on_github or on_gitlab)[0]  # type: ignore[index]
                exception = config.exception_for(pair.name, name)
                if exception is None:
                    document = github if on_github is not None else gitlab
                    container = (
                        _github_inputs(github) if on_github is not None else gitlab_container
                    )
                    findings.append(
                        Finding(
                            document.rel,
                            document.line_of(container, name),
                            "parity-missing-input",
                            f"`{name}` exists on {present} but not on {missing}; add it there "
                            "or record a reason in ci-lint.yaml",
                        )
                    )
                else:
                    used_exceptions.add((exception.pipeline, exception.input))
                continue

            github_default = render_default(mapping(on_github[1]).get("default"))
            gitlab_default = render_default(mapping(on_gitlab[1]).get("default"))
            if github_default != gitlab_default:
                exception = config.exception_for(pair.name, on_github[0])
                if exception is None:
                    findings.append(
                        Finding(
                            github.rel,
                            github.line_of(github_inputs, on_github[0]),
                            "parity-default-mismatch",
                            f"`{on_github[0]}` defaults to {github_default!r} on GitHub and "
                            f"{gitlab_default!r} on GitLab",
                        )
                    )
                else:
                    used_exceptions.add((exception.pipeline, exception.input))

    for exception in config.parity_exceptions:
        if (exception.pipeline, exception.input) not in used_exceptions:
            findings.append(
                Finding(
                    "ci-lint.yaml",
                    1,
                    "parity-stale-exception",
                    f"exception for {exception.pipeline}.{exception.input} is not needed any "
                    "more; the two sides agree",
                )
            )

    return findings
