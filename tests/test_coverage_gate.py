"""The coverage gate, which is the one piece of this repository that decides
whether somebody's pull request is allowed to merge.

The cases that matter are not "does it compute a percentage": they are the
ones where a broken pipeline could look like a passing one — an empty report,
a missing file, a profile with no covered blocks.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

COBERTURA = """\
<?xml version="1.0" ?>
<coverage version="7.6" lines-valid="{valid}" lines-covered="{covered}" line-rate="{rate}">
  <packages/>
</coverage>
"""

COVERPROFILE = """\
mode: atomic
example.com/pkg/a.go:10.20,12.3 2 1
example.com/pkg/a.go:14.20,16.3 3 0
example.com/pkg/b.go:5.10,7.2 5 4
"""


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_cobertura_uses_exact_counts_when_they_are_there(
    coverage_gate: ModuleType, tmp_path: Path
) -> None:
    report = write(tmp_path / "c.xml", COBERTURA.format(valid=500, covered=412, rate="0.824"))
    result = coverage_gate.measure(report, "cobertura")
    assert (result.covered, result.total) == (412, 500)
    assert result.percent == pytest.approx(82.4)


def test_cobertura_falls_back_to_line_rate(coverage_gate: ModuleType, tmp_path: Path) -> None:
    report = write(
        tmp_path / "c.xml",
        '<?xml version="1.0" ?><coverage line-rate="0.9137"><packages/></coverage>',
    )
    result = coverage_gate.measure(report, "cobertura")
    assert result.percent == pytest.approx(91.37, abs=0.01)


def test_go_coverprofile_counts_statements_not_lines(
    coverage_gate: ModuleType, tmp_path: Path
) -> None:
    report = write(tmp_path / "coverage.out", COVERPROFILE)
    result = coverage_gate.measure(report, "go-coverprofile")
    # 2 + 5 covered of 2 + 3 + 5 total: blocks are weighted by statement count,
    # which is what `go tool cover -func` reports as the total.
    assert (result.covered, result.total) == (7, 10)
    assert result.percent == pytest.approx(70.0)


def test_an_empty_report_is_zero_not_a_hundred(coverage_gate: ModuleType) -> None:
    assert coverage_gate.Coverage(covered=0, total=0).percent == 0.0


def test_a_profile_without_its_mode_header_is_rejected(
    coverage_gate: ModuleType, tmp_path: Path
) -> None:
    report = write(tmp_path / "coverage.out", "example.com/pkg/a.go:1.1,2.2 1 1\n")
    with pytest.raises(coverage_gate.GateError, match="mode:"):
        coverage_gate.measure(report, "go-coverprofile")


def test_a_missing_report_fails_loudly(coverage_gate: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(coverage_gate.GateError, match="does not exist"):
        coverage_gate.measure(tmp_path / "absent.xml", "cobertura")


def test_format_detection_reads_the_content(coverage_gate: ModuleType, tmp_path: Path) -> None:
    xml = write(tmp_path / "report.txt", COBERTURA.format(valid=10, covered=5, rate="0.5"))
    profile = write(tmp_path / "profile.txt", COVERPROFILE)
    assert coverage_gate.measure(xml, "auto").total == 10
    assert coverage_gate.measure(profile, "auto").total == 10


def test_unrecognisable_report_names_the_way_out(coverage_gate: ModuleType, tmp_path: Path) -> None:
    report = write(tmp_path / "report.txt", "coverage is fine, trust me\n")
    with pytest.raises(coverage_gate.GateError, match="format: cobertura"):
        coverage_gate.measure(report, "auto")


def _run(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    report: Path,
    minimum: str,
) -> tuple[int, str, str]:
    output = tmp_path / "out.txt"
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("COVERAGE_REPORT", str(report))
    monkeypatch.setenv("COVERAGE_MINIMUM", minimum)
    monkeypatch.setenv("COVERAGE_FORMAT", "auto")
    monkeypatch.setenv("COVERAGE_LABEL", "python 3.13")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code = module.main()
    return (
        code,
        output.read_text(encoding="utf-8") if output.exists() else "",
        summary.read_text(encoding="utf-8") if summary.exists() else "",
    )


def test_gate_passes_and_publishes_the_number(
    coverage_gate: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = write(tmp_path / "c.xml", COBERTURA.format(valid=100, covered=90, rate="0.9"))
    code, outputs, summary = _run(coverage_gate, monkeypatch, tmp_path, report=report, minimum="85")
    assert code == 0
    assert "coverage=90.00" in outputs
    assert "covered=90" in outputs
    assert "| python 3.13 | 90.00% | 85.00% | 90/100 | pass |" in summary


def test_gate_fails_below_the_threshold(
    coverage_gate: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = write(tmp_path / "c.xml", COBERTURA.format(valid=100, covered=84, rate="0.84"))
    code, _, summary = _run(coverage_gate, monkeypatch, tmp_path, report=report, minimum="85")
    assert code == 1
    assert "fail |" in summary


def test_exactly_on_the_threshold_passes(
    coverage_gate: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 85/100 is 85.0 in decimal and 84.99999999999999 in some float paths;
    # a gate that rejects the number it just printed is unusable.
    report = write(tmp_path / "c.xml", COBERTURA.format(valid=100, covered=85, rate="0.85"))
    code, _, _ = _run(coverage_gate, monkeypatch, tmp_path, report=report, minimum="85")
    assert code == 0


def test_a_missing_report_is_a_configuration_error_not_a_failure(
    coverage_gate: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _, _ = _run(
        coverage_gate, monkeypatch, tmp_path, report=tmp_path / "absent.xml", minimum="85"
    )
    # Exit 2, not 1: the tests did not fail the gate, the gate could not run.
    assert code == 2


def test_the_summary_header_is_written_once(
    coverage_gate: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = write(tmp_path / "c.xml", COBERTURA.format(valid=100, covered=90, rate="0.9"))
    _run(coverage_gate, monkeypatch, tmp_path, report=report, minimum="85")
    _, _, summary = _run(coverage_gate, monkeypatch, tmp_path, report=report, minimum="85")
    assert summary.count("| report | coverage |") == 1
    assert summary.count("| python 3.13 |") == 2
