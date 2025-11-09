from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

from ci_lint import cli
from ci_lint.config import Config
from ci_lint.config import load as load_config
from ci_lint.model import Report

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture(scope="session")
def repo_config() -> Config:
    return load_config(ROOT)


def check(path: Path, config: Config, *, schema: bool = True) -> Report:
    """Run the checker over one file, the way the CLI does."""
    return cli.check_file(path, config, run_schema=schema, run_rules=True)


def rules_of(report: Report) -> set[str]:
    return {finding.rule for finding in report.findings}


def find_bash() -> str | None:
    """Locate a POSIX bash.

    On Windows `bash` on PATH is usually the WSL launcher, which cannot see
    Windows paths the way the test needs, so Git's bash is preferred when it
    is there.
    """
    override = os.environ.get("CI_LINT_BASH")
    if override:
        return override
    if sys.platform == "win32":
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        if git_bash.is_file():
            return str(git_bash)
    return shutil.which("bash")


@pytest.fixture(scope="session")
def coverage_gate() -> ModuleType:
    """Import the composite action's script as a module."""
    path = ROOT / ".github" / "actions" / "coverage-gate" / "gate.py"
    spec = importlib.util.spec_from_file_location("coverage_gate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules, so a module that
    # is executed without being registered there cannot define one.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
