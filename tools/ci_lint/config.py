"""Repository-level configuration for the checker.

Everything that is a property of *this* repository rather than of CI in
general lives in ci-lint.yaml: which ref self-references use, and which
template pairs are supposed to expose the same inputs on both CI systems.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_NAME = "ci-lint.yaml"
MIN_REASON_LENGTH = 20


class ConfigError(Exception):
    """ci-lint.yaml exists but does not say what it has to say."""


@dataclass(frozen=True)
class SelfReference:
    repository: str
    ref: str


@dataclass(frozen=True)
class ParityPair:
    name: str
    github: str
    gitlab: str


@dataclass(frozen=True)
class ParityException:
    pipeline: str
    input: str
    reason: str


@dataclass(frozen=True)
class Config:
    root: Path
    self_reference: SelfReference | None = None
    parity_pairs: tuple[ParityPair, ...] = ()
    parity_exceptions: tuple[ParityException, ...] = ()

    def exception_for(self, pipeline: str, name: str) -> ParityException | None:
        """Exact pipeline first, then the `*` entry for differences common to all."""
        for wanted in (pipeline, "*"):
            for item in self.parity_exceptions:
                if item.pipeline == wanted and item.input == name:
                    return item
        return None


def load(root: Path) -> Config:
    path = root / CONFIG_NAME
    if not path.is_file():
        return Config(root=root)

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return Config(root=root)
    if not isinstance(raw, dict):
        raise ConfigError(f"{CONFIG_NAME}: expected a mapping at the top level")

    self_ref: SelfReference | None = None
    raw_self = raw.get("self_reference")
    if raw_self is not None:
        if not isinstance(raw_self, dict) or "repository" not in raw_self or "ref" not in raw_self:
            raise ConfigError(f"{CONFIG_NAME}: self_reference needs `repository` and `ref`")
        self_ref = SelfReference(str(raw_self["repository"]), str(raw_self["ref"]))

    pairs: list[ParityPair] = []
    exceptions: list[ParityException] = []
    raw_parity = raw.get("parity")
    if raw_parity is not None:
        if not isinstance(raw_parity, dict):
            raise ConfigError(f"{CONFIG_NAME}: parity must be a mapping")
        for entry in raw_parity.get("pairs") or []:
            if not isinstance(entry, dict) or not {"name", "github", "gitlab"} <= set(entry):
                raise ConfigError(f"{CONFIG_NAME}: every parity pair needs name, github, gitlab")
            pairs.append(ParityPair(str(entry["name"]), str(entry["github"]), str(entry["gitlab"])))
        for entry in raw_parity.get("exceptions") or []:
            if not isinstance(entry, dict) or not {"pipeline", "input", "reason"} <= set(entry):
                raise ConfigError(
                    f"{CONFIG_NAME}: every parity exception needs pipeline, input, reason"
                )
            reason = str(entry["reason"]).strip()
            if len(reason) < MIN_REASON_LENGTH:
                # An exception without an argument is how a rule quietly stops
                # being a rule. The length floor is crude but it does force a
                # sentence instead of the word "different".
                raise ConfigError(
                    f"{CONFIG_NAME}: parity exception {entry['pipeline']}.{entry['input']} "
                    f"needs a reason of at least {MIN_REASON_LENGTH} characters"
                )
            exceptions.append(ParityException(str(entry["pipeline"]), str(entry["input"]), reason))

    return Config(
        root=root,
        self_reference=self_ref,
        parity_pairs=tuple(pairs),
        parity_exceptions=tuple(exceptions),
    )
