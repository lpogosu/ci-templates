"""Invariants for GitLab CI templates and CI/CD components.

The rule set is deliberately smaller than the GitHub one. GitLab has no
per-job permission model, so there is no equivalent of the write-permission
rule; what it does have is `image:`, which is the same problem as `uses:` —
an artefact fetched by a mutable name and then executed.
"""

from __future__ import annotations

import re
from typing import Any

from .loader import Document, mapping, sequence
from .model import Finding

# Interpolation of a component input, e.g. `$[[ inputs.image ]]`.
INPUT_INTERPOLATION_RE = re.compile(r"\$\[\[\s*inputs\.([A-Za-z_][A-Za-z0-9_]*)[^\]]*\]\]")
# A plain CI/CD variable, which is only known at run time.
VARIABLE_RE = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?")

# Top-level keys that configure the pipeline rather than describe a job.
GLOBAL_KEYS = frozenset(
    {
        "spec",
        "stages",
        "variables",
        "default",
        "include",
        "workflow",
        "image",
        "services",
        "before_script",
        "after_script",
        "cache",
        "!reference",
        "$schema",
    }
)


def spec_inputs(document: Document) -> tuple[dict[str, Any], Any]:
    """Return the component's declared inputs and the mapping holding them."""
    for data in document.documents:
        block = mapping(data)
        if "spec" in block:
            inputs_container = mapping(block.get("spec")).get("inputs")
            return mapping(inputs_container), inputs_container
    return {}, None


def body(document: Document) -> dict[str, Any]:
    """Return the pipeline body: the last document that is not a spec header."""
    for data in reversed(document.documents):
        block = mapping(data)
        if block and "spec" not in block:
            return block
    return {}


def _jobs(pipeline: dict[str, Any]) -> dict[str, Any]:
    return {
        name: value
        for name, value in pipeline.items()
        if name not in GLOBAL_KEYS and isinstance(value, dict)
    }


def _inherited(
    pipeline: dict[str, Any], job: dict[str, Any], key: str, seen: frozenset[str] = frozenset()
) -> Any:
    """Look up `key` on a job, following `extends` and then `default:`.

    GitLab merges later entries of an `extends` list over earlier ones, so the
    list is walked in reverse; without that the checker would report a timeout
    as missing when a second parent supplies it.
    """
    if key in job:
        return job[key]
    parents = job.get("extends")
    names = [parents] if isinstance(parents, str) else sequence(parents)
    for parent_name in reversed(names):
        if not isinstance(parent_name, str) or parent_name in seen:
            continue
        parent = pipeline.get(parent_name)
        if isinstance(parent, dict):
            value = _inherited(pipeline, parent, key, seen | {parent_name})
            if value is not None:
                return value
    return mapping(pipeline.get("default")).get(key)


def _image_reference(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    name = mapping(value).get("name")
    return name if isinstance(name, str) else None


def _check_reference(
    document: Document,
    where: str,
    reference: str,
    inputs: dict[str, Any],
    line: int,
    findings: list[Finding],
) -> None:
    referenced_inputs = INPUT_INTERPOLATION_RE.findall(reference)
    if referenced_inputs:
        # The pin lives in the input default. Checking it there is what stops
        # an input from being a hole in the policy: a caller can still pass an
        # unpinned image, but the template never ships one.
        for name in referenced_inputs:
            spec = mapping(inputs.get(name))
            default = spec.get("default")
            if not isinstance(default, str) or "@sha256:" not in default:
                findings.append(
                    Finding(
                        document.rel,
                        line,
                        "gitlab-pinned-image",
                        f"{where} comes from input `{name}`, whose default "
                        f"`{default!r}` is not pinned to a digest",
                    )
                )
        return

    if "@sha256:" in reference:
        return

    if VARIABLE_RE.search(reference):
        # A run-time variable such as $CI_REGISTRY_IMAGE; its value is not in
        # this file and the checker says so rather than guessing.
        return

    findings.append(
        Finding(
            document.rel,
            line,
            "gitlab-pinned-image",
            f"{where} `{reference}` is not pinned to a digest",
        )
    )


def check_template(document: Document) -> list[Finding]:
    findings: list[Finding] = []
    inputs, inputs_container = spec_inputs(document)
    pipeline = body(document)
    jobs = _jobs(pipeline)

    for name, raw_job in jobs.items():
        job = mapping(raw_job)
        line = document.line_of(pipeline, name)

        if _inherited(pipeline, job, "timeout") is None:
            findings.append(
                Finding(
                    document.rel,
                    line,
                    "gitlab-job-timeout",
                    f"job `{name}` sets no timeout and inherits none; it will run until the "
                    "project-wide limit, which is an hour of a runner nobody is watching",
                )
            )

        image = _image_reference(_inherited(pipeline, job, "image"))
        if image is not None:
            _check_reference(document, f"job `{name}` image", image, inputs, line, findings)

        for service in sequence(_inherited(pipeline, job, "services")):
            reference = _image_reference(service)
            if reference is not None:
                _check_reference(
                    document, f"job `{name}` service", reference, inputs, line, findings
                )

    referenced = set(INPUT_INTERPOLATION_RE.findall(document.text))
    for name, raw_spec in inputs.items():
        spec = mapping(raw_spec)
        line = document.line_of(inputs_container, name)
        missing = [key for key in ("type", "description") if key not in spec]
        if missing:
            findings.append(
                Finding(
                    document.rel,
                    line,
                    "gitlab-input-undocumented",
                    f"input `{name}` is missing {' and '.join(missing)}",
                )
            )
        if name not in referenced:
            findings.append(
                Finding(
                    document.rel,
                    line,
                    "gitlab-unused-input",
                    f"input `{name}` is declared but never interpolated",
                )
            )

    return findings
