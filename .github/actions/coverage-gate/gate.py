"""Fail a job when total coverage falls below a threshold.

Reads a Cobertura XML report (coverage.py, istanbul, JaCoCo's Cobertura
writer) or a Go coverprofile, and reports one number in one format regardless
of which ecosystem produced it.

Configured entirely through the environment so that the same file runs as a
composite action step and as a plain script under the test suite.
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


class GateError(Exception):
    """Raised for a problem the caller has to fix, e.g. a missing report."""


@dataclass(frozen=True)
class Coverage:
    covered: int
    total: int

    @property
    def percent(self) -> float:
        # An empty report means nothing was measured. Reporting 100% there is
        # how a broken test command turns into a green coverage gate.
        if self.total == 0:
            return 0.0
        return 100.0 * self.covered / self.total


def parse_cobertura(text: str) -> Coverage:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise GateError(f"report is not valid XML: {exc}") from exc
    if root.tag != "coverage":
        raise GateError(f"expected a <coverage> root element, found <{root.tag}>")

    valid = root.get("lines-valid")
    covered = root.get("lines-covered")
    if valid is not None and covered is not None:
        # Preferred: exact counts, so the message can say 412/500 rather than
        # only a percentage that hides how small the sample is.
        try:
            return Coverage(covered=int(covered), total=int(valid))
        except ValueError as exc:
            raise GateError(f"lines-covered/lines-valid are not integers: {exc}") from exc

    rate = root.get("line-rate")
    if rate is None:
        raise GateError("report has neither lines-covered/lines-valid nor line-rate")
    try:
        fraction = float(rate)
    except ValueError as exc:
        raise GateError(f"line-rate is not a number: {rate!r}") from exc
    # Counts are unavailable, so scale to a fixed denominator and say so by
    # keeping the total at 10000 in the summary.
    return Coverage(covered=round(fraction * 10_000), total=10_000)


def parse_go_coverprofile(text: str) -> Coverage:
    """Sum statement counts from a Go coverprofile.

    Each line is `file.go:l.c,l.c numStmt count`; a block counts as covered
    when its execution count is non-zero. This is the same arithmetic that
    `go tool cover -func` prints as `total:`, done without a Go toolchain.
    """
    covered = 0
    total = 0
    seen_mode = False
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("mode:"):
            seen_mode = True
            continue
        fields = line.rsplit(" ", 2)
        if len(fields) != 3:
            raise GateError(f"line {lineno} is not a coverprofile block: {line!r}")
        try:
            statements = int(fields[1])
            count = int(fields[2])
        except ValueError as exc:
            raise GateError(f"line {lineno} has a non-integer count: {exc}") from exc
        total += statements
        if count > 0:
            covered += statements
    if not seen_mode:
        raise GateError("coverprofile is missing its `mode:` header")
    return Coverage(covered=covered, total=total)


def detect_format(path: Path, text: str) -> str:
    if path.suffix == ".xml" or text.lstrip().startswith("<"):
        return "cobertura"
    if text.lstrip().startswith("mode:"):
        return "go-coverprofile"
    raise GateError(
        f"cannot tell what {path} is; pass format: cobertura or format: go-coverprofile"
    )


def measure(path: Path, fmt: str) -> Coverage:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise GateError(
            f"coverage report {path} does not exist; the test command has to write it"
        ) from exc
    if fmt == "auto":
        fmt = detect_format(path, text)
    if fmt == "cobertura":
        return parse_cobertura(text)
    if fmt == "go-coverprofile":
        return parse_go_coverprofile(text)
    raise GateError(f"unknown format {fmt!r}")


SUMMARY_HEADER = (
    "| report | coverage | minimum | lines | verdict |\n| --- | --- | --- | --- | --- |\n"
)


def emit(name: str, value: str) -> None:
    target = os.environ.get(name)
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(value)


def emit_summary_row(row: str) -> None:
    """Append a row, writing the table header once per job summary."""
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    existing = ""
    summary = Path(target)
    if summary.exists():
        existing = summary.read_text(encoding="utf-8")
    with summary.open("a", encoding="utf-8") as handle:
        if SUMMARY_HEADER not in existing:
            handle.write(SUMMARY_HEADER)
        handle.write(row)


def main() -> int:
    report = os.environ.get("COVERAGE_REPORT", "")
    if not report:
        print("coverage-gate: COVERAGE_REPORT is empty", file=sys.stderr)
        return 2
    label = os.environ.get("COVERAGE_LABEL") or "coverage"
    fmt = os.environ.get("COVERAGE_FORMAT") or "auto"
    try:
        minimum = float(os.environ.get("COVERAGE_MINIMUM", ""))
    except ValueError:
        print("coverage-gate: COVERAGE_MINIMUM is not a number", file=sys.stderr)
        return 2

    try:
        coverage = measure(Path(report), fmt)
    except GateError as exc:
        print(f"::error::coverage-gate: {exc}", file=sys.stderr)
        return 2

    percent = coverage.percent
    passed = percent + 1e-9 >= minimum
    verdict = "pass" if passed else "fail"

    emit(
        "GITHUB_OUTPUT",
        f"coverage={percent:.2f}\ncovered={coverage.covered}\ntotal={coverage.total}\n",
    )
    emit_summary_row(
        f"| {label} | {percent:.2f}% | {minimum:.2f}% | {coverage.covered}/{coverage.total} |"
        f" {verdict} |\n"
    )

    line = (
        f"coverage-gate [{label}]: {percent:.2f}% of {coverage.total} "
        f"({coverage.covered} covered), minimum {minimum:.2f}%"
    )
    if passed:
        print(line)
        return 0
    print(f"::error::{line}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
