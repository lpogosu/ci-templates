"""Command line entry point for ci-lint."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TextIO

from . import github as github_rules
from . import gitlab as gitlab_rules
from . import parity, schema
from .config import MIN_REASON_LENGTH, Config, ConfigError
from .config import load as load_config
from .loader import Document, YamlError
from .model import RULES, Finding, Report

WORKFLOW_DIR = Path(".github/workflows")
ACTION_DIR = Path(".github/actions")
GITLAB_DIR = Path("gitlab")
YAML_SUFFIXES = (".yml", ".yaml")

SUPPRESSION_RE = re.compile(r"#\s*ci-lint:\s*allow\s+([a-z0-9\-]+)\s*--\s*(.*)$")

KIND_WORKFLOW = "workflow"
KIND_ACTION = "action"
KIND_GITLAB = "gitlab"

SCHEMA_FOR_KIND = {
    KIND_WORKFLOW: schema.GITHUB_WORKFLOW,
    KIND_ACTION: schema.GITHUB_ACTION,
    KIND_GITLAB: schema.GITLAB_CI,
}


def classify(path: Path) -> str:
    """Decide which schema and rule set a file belongs to, from its path.

    A directory literally called `gitlab`, or a file named after GitLab's own
    entry point. Substring matching on the whole path would classify
    `examples/gitlab-python-service/ci.after.yml` — a GitHub workflow — as a
    GitLab template.
    """
    if path.name in {"action.yml", "action.yaml"}:
        return KIND_ACTION
    if "gitlab" in {part.lower() for part in path.parts[:-1]}:
        return KIND_GITLAB
    if path.name.lstrip(".").startswith("gitlab-ci"):
        return KIND_GITLAB
    return KIND_WORKFLOW


def discover(root: Path) -> list[Path]:
    found: list[Path] = []
    for directory, pattern in (
        (WORKFLOW_DIR, "*"),
        (ACTION_DIR, "*/action"),
        (GITLAB_DIR, "*"),
    ):
        base = root / directory
        if not base.is_dir():
            continue
        for suffix in YAML_SUFFIXES:
            found.extend(sorted(base.glob(pattern + suffix)))
    return found


def _suppression_on(document: Document, line: int) -> tuple[str, str] | None:
    """A `ci-lint: allow` comment on the finding's line or the line above it."""
    for candidate in (line, line - 1):
        match = SUPPRESSION_RE.search(document.raw_line(candidate))
        if match is not None:
            return match.group(1), match.group(2).strip()
    return None


def apply_suppressions(document: Document, findings: Iterable[Finding]) -> Report:
    report = Report()
    for finding in findings:
        suppression = _suppression_on(document, finding.line)
        if suppression is None or suppression[0] != finding.rule:
            report.add(finding)
            continue
        rule, reason = suppression
        if len(reason) < MIN_REASON_LENGTH:
            report.add(
                Finding(
                    finding.path,
                    finding.line,
                    "suppression-without-reason",
                    f"`ci-lint: allow {rule}` needs at least {MIN_REASON_LENGTH} characters "
                    f"of justification, got {len(reason)}",
                )
            )
            continue
        report.suppressed.append(finding)
    return report


def check_file(path: Path, config: Config, run_schema: bool, run_rules: bool) -> Report:
    kind = classify(path)
    report = Report(files_checked=1)
    try:
        document = Document(path, config.root)
    except YamlError as exc:
        report.add(Finding(path.relative_to(config.root).as_posix(), exc.line, "yaml", str(exc)))
        return report

    findings: list[Finding] = []
    if run_schema:
        findings.extend(schema.check(document, SCHEMA_FOR_KIND[kind]))
    if run_rules:
        if kind == KIND_WORKFLOW:
            findings.extend(github_rules.check_workflow(document, config))
        elif kind == KIND_ACTION:
            findings.extend(github_rules.check_action(document, config))
        else:
            findings.extend(gitlab_rules.check_template(document))

    suppressed = apply_suppressions(document, findings)
    suppressed.files_checked = 1
    return suppressed


def run(
    paths: Sequence[Path],
    config: Config,
    run_schema: bool = True,
    run_rules: bool = True,
    run_parity: bool = True,
) -> Report:
    report = Report()
    for path in paths:
        report.merge(check_file(path, config, run_schema, run_rules))
    if run_parity and config.parity_pairs:
        report.extend(parity.check(config))
    return report


def _print_text(report: Report, stream: TextIO) -> None:
    for finding in sorted(report.findings):
        stream.write(finding.format() + "\n")
    summary = f"{report.files_checked} file(s), {len(report.findings)} finding(s)"
    if report.suppressed:
        summary += f", {len(report.suppressed)} suppressed"
    stream.write(summary + "\n")


def _print_json(report: Report, stream: TextIO) -> None:
    payload = {
        "files_checked": report.files_checked,
        "findings": [
            {"path": f.path, "line": f.line, "rule": f.rule, "message": f.message}
            for f in sorted(report.findings)
        ],
        "suppressed": [
            {"path": f.path, "line": f.line, "rule": f.rule} for f in sorted(report.suppressed)
        ],
    }
    stream.write(json.dumps(payload, indent=2) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ci-lint",
        description="Validate CI templates against their schemas and the invariants "
        "that schemas cannot express.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="validate workflows, actions and GitLab templates")
    check.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="files to check; default is every template in the repository",
    )
    check.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    check.add_argument("--no-schema", action="store_true", help="skip JSON schema validation")
    check.add_argument("--no-rules", action="store_true", help="skip the invariant rules")
    check.add_argument("--no-parity", action="store_true", help="skip the GitHub/GitLab comparison")
    check.add_argument("--format", choices=("text", "json"), default="text")

    sub.add_parser("rules", help="print the rule catalogue")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "rules":
        for name in sorted(RULES):
            print(f"{name:34} {RULES[name]}")
        return 0

    root = args.root.resolve()
    try:
        config = load_config(root)
    except ConfigError as exc:
        print(f"ci-lint: {exc}", file=sys.stderr)
        return 2

    paths = [p.resolve() for p in args.paths] if args.paths else discover(root)
    if not paths:
        print(f"ci-lint: no CI files found under {root}", file=sys.stderr)
        return 2

    report = run(
        paths,
        config,
        run_schema=not args.no_schema,
        run_rules=not args.no_rules,
        run_parity=not args.no_parity and not args.paths,
    )

    if args.format == "json":
        _print_json(report, sys.stdout)
    else:
        _print_text(report, sys.stdout)
    return 0 if report.ok else 1
