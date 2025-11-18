"""YAML loading that keeps the line number of every mapping key.

PyYAML throws positions away as soon as it builds Python objects, and a linter
that can only say "somewhere in this file" is a linter people stop reading.
The loader below records, for every mapping it constructs, the source line of
each of its keys, so a finding can point at the exact `uses:` that is wrong.
"""

from __future__ import annotations

import re
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast

import yaml


class YamlError(Exception):
    """The file is not parseable YAML."""

    def __init__(self, message: str, line: int) -> None:
        super().__init__(message)
        self.line = line


class _PositionLoader(yaml.SafeLoader):
    """SafeLoader that remembers where each mapping key was written."""

    def __init__(self, stream: Any) -> None:
        super().__init__(stream)
        self.key_lines: dict[int, dict[str, int]] = {}

    def construct_yaml_map(self, node: yaml.Node) -> Generator[dict[Any, Any], None, None]:
        # SafeLoader yields the dict before filling it so that anchors can
        # refer to it; the identity of that first object is the one every
        # rule will later hold, so positions must be attached to it and not
        # to the temporary mapping returned by construct_mapping.
        data: dict[Any, Any] = {}
        yield data
        if isinstance(node, yaml.MappingNode):
            data.update(self.construct_mapping(node))
            positions: dict[str, int] = {}
            for key_node, _ in node.value:
                if isinstance(key_node, yaml.ScalarNode) and isinstance(key_node.value, str):
                    positions[key_node.value] = key_node.start_mark.line + 1
            self.key_lines[id(data)] = positions


_PositionLoader.add_constructor("tag:yaml.org,2002:map", _PositionLoader.construct_yaml_map)

# PyYAML implements YAML 1.1, where `on`, `off`, `yes` and `no` are booleans.
# GitHub Actions does not: `on:` is the trigger key of every workflow, and a
# loader that turns it into the boolean True cannot even find the triggers.
# Restricting the bool resolver to true/false matches how GitHub, actionlint
# and the JSON schemas all read these files.
_PositionLoader.yaml_implicit_resolvers = {
    char: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_PositionLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


class Document:
    """A parsed YAML file plus everything the rules need to report on it."""

    def __init__(self, path: Path, root: Path) -> None:
        self.path = path
        self.rel = path.relative_to(root).as_posix()
        self.text = path.read_text(encoding="utf-8")
        self.lines = self.text.splitlines()
        loader = _PositionLoader(self.text)
        try:
            documents: list[Any] = []
            # PyYAML's public loader API is untyped; the two calls below are
            # what `yaml.load_all` does internally, kept explicit so that the
            # loader instance stays reachable and its positions can be read.
            while loader.check_data():  # type: ignore[no-untyped-call]
                documents.append(loader.get_data())
            self._key_lines = loader.key_lines
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            line = mark.line + 1 if mark is not None else 1
            raise YamlError(str(exc).replace("\n", " "), line) from exc
        finally:
            loader.dispose()
        self.documents = documents

    @property
    def data(self) -> Any:
        return self.documents[0] if self.documents else None

    def line_of(self, container: Any, key: str, default: int = 1) -> int:
        """Source line of `key` inside `container`, or `default`."""
        positions = self._key_lines.get(id(container))
        if positions is None:
            return default
        return positions.get(key, default)

    def raw_line(self, line: int) -> str:
        if 1 <= line <= len(self.lines):
            return self.lines[line - 1]
        return ""


def mapping(value: Any) -> dict[str, Any]:
    """Return `value` when it is a string-keyed mapping, otherwise an empty one.

    CI files are full of places where a key may hold a mapping, a string or a
    list, and every rule would otherwise start with the same isinstance dance.

    The original object is returned untouched whenever it already has string
    keys. That matters: positions are recorded against object identity, so a
    defensive copy here would silently cost every finding its line number.
    """
    if isinstance(value, dict):
        if all(isinstance(key, str) for key in value):
            return cast("dict[str, Any]", value)
        return {str(key): item for key, item in value.items()}
    return {}


def sequence(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []
