# Vendored schemas

These three files are copies, pinned to an upstream revision. They are not
fetched at run time, and that is a decision rather than an oversight: a
validator that downloads its own rules can turn a pipeline red on a morning
when nobody touched the repository, and it makes "this passed last week"
impossible to reproduce.

`make schemas-check` compares the copies with the pinned upstream and reports
a difference without failing anybody's pull request. `make schemas-refresh`
re-downloads them; the pins live in the `SCHEMASTORE_REF` and `GITLAB_REF`
variables at the top of the Makefile and have to be moved by hand first.

| File | Upstream | Revision | SHA-256 |
|---|---|---|---|
| `github-workflow.json` | `SchemaStore/schemastore`, `src/schemas/json/github-workflow.json` | `cd8aa06c45eacd397835176606512d3401cd1a9f` | `0dc634ef11929ddcfd1356e244754aecbf35d02573cbf52b1860f8c50e48c921` |
| `github-action.json` | `SchemaStore/schemastore`, `src/schemas/json/github-action.json` | `cd8aa06c45eacd397835176606512d3401cd1a9f` | `224326cc5a334bdb97b56b6fc320cd4381ba7d61b477b425ed506e6a420b52ba` |
| `gitlab-ci.json` | `gitlab-org/gitlab`, `app/assets/javascripts/editor/schema/ci.json` | `v19.3.1-ee` | `7568b544b4b95102f21e9fd89d769d54ee6f86b35d2fbb7f7b947d6fa9fc4dda` |

All three declare JSON Schema draft-07 and contain no external `$ref`s, so
validation needs no network and no resolver.

## What the schemas do and do not catch

They describe the shape of a file: which keys exist, what type each value has,
which combinations are mutually exclusive. That covers a large class of typos
and is worth having on every commit.

They say nothing about whether the file is *correct*. A workflow that grants
`write-all`, pins nothing, sets no timeout and reads a secret it never
declared is a perfectly valid GitHub Actions workflow, and all three schemas
will accept it. That gap is what `tools/ci_lint` exists to close.

A second, quieter gap: the GitLab schema describes `.gitlab-ci.yml` as GitLab
documents it, not as GitLab's own parser implements it. Passing it means the
file is well-formed, not that GitLab will accept it — see the limitations
section of the README.
