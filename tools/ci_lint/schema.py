"""Validation against the vendored GitHub Actions and GitLab CI schemas.

The schemas are copied into schemas/ rather than fetched at run time. A
validator that downloads its own rules turns a green pipeline red on a day
nobody touched the repository, and makes "it passed last week" unverifiable.
schemas/SOURCES.md records exactly which upstream revision each file came
from, and `make schemas-refresh` is how they move.
"""

from __future__ import annotations

import json
import os
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator
from jsonschema.exceptions import ValidationError, relevance

from .loader import Document
from .model import Finding

# The schemas belong to the checker, not to the repository being checked, so
# they are found next to the package. CI_LINT_SCHEMA_DIR exists for the case
# where the tool is vendored somewhere else in a caller repository.
SCHEMA_DIR = Path(
    os.environ.get("CI_LINT_SCHEMA_DIR") or Path(__file__).resolve().parents[2] / "schemas"
)
GITHUB_WORKFLOW = "github-workflow.json"
GITHUB_ACTION = "github-action.json"
GITLAB_CI = "gitlab-ci.json"

# One badly indented block produces dozens of errors from every branch of an
# anyOf. Printing the most relevant handful is the difference between a report
# somebody reads and a report somebody scrolls past.
MAX_ERRORS_PER_DOCUMENT = 4


@cache
def _validator(schema_path: str) -> Draft7Validator:
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    return Draft7Validator(schema)


def validator_for(name: str) -> Draft7Validator:
    path = SCHEMA_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"vendored schema {path} is missing; run `make schemas-refresh`")
    return _validator(str(path))


def _line_for(document: Document, data: Any, path: list[Any]) -> int:
    """Best line for a JSON pointer, by walking as deep as positions allow."""
    line = 1
    node = data
    for step in path:
        if isinstance(step, str) and isinstance(node, dict):
            line = document.line_of(node, step, line)
            node = node.get(step)
        elif isinstance(step, int) and isinstance(node, list) and 0 <= step < len(node):
            node = node[step]
        else:
            break
    return line


def _describe(error: ValidationError) -> str:
    where = "/".join(str(part) for part in error.absolute_path) or "<root>"
    message = error.message.replace("\n", " ")
    if len(message) > 240:
        message = message[:237] + "..."
    return f"{where}: {message}"


def check(document: Document, schema_name: str) -> list[Finding]:
    """Validate every YAML document in the file against one schema."""
    validator = validator_for(schema_name)
    findings: list[Finding] = []
    for data in document.documents:
        if data is None:
            continue
        errors = sorted(validator.iter_errors(data), key=relevance)
        for error in errors[:MAX_ERRORS_PER_DOCUMENT]:
            findings.append(
                Finding(
                    path=document.rel,
                    line=_line_for(document, data, list(error.absolute_path)),
                    rule="schema",
                    message=_describe(error),
                )
            )
    return findings
