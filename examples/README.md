# Caller repositories, before and after

Each directory holds two files that both live at the same path in a caller
repository — `.github/workflows/ci.yml`, or `.gitlab-ci.yml`. The `.before.`
file is what the team had. The `.after.` file is what replaces it.

The `.before.` files are not strawmen. They are the shape a workflow reaches
after a year of small fixes, and every block in them exists because something
once went wrong. Each one also carries at least one bug that survived in the
original for months, called out in a comment where it lives:

| Example | The bug that hid in it |
|---|---|
| `python-service` | the inline coverage gate exits 0 when `coverage.xml` is missing, so a test command that stopped writing it turned the gate off silently |
| `node-app` | the coverage threshold is a Vitest flag in one job and a `grep` in another, and the two disagree |
| `go-service` | `[ "85.4%" -ge 70 ]` is a bash error, swallowed by `\|\| echo`, so this gate has never once failed |
| `gitlab-python-service` | the image is pushed before it is scanned, and the scan carries `allow_failure: true` |

## Measured size

Counted with `wc -l`, and again ignoring comments and blank lines, which is
the fairer number because the `.after.` files are mostly explanation.

| Example | before | after | effective before | effective after | factor |
|---|---:|---:|---:|---:|---:|
| `python-service` | 154 | 40 | 121 | 30 | 4.0x |
| `node-app` | 65 | 18 | 43 | 14 | 3.1x |
| `go-service` | 52 | 23 | 37 | 15 | 2.5x |
| `gitlab-python-service` | 108 | 20 | 92 | 13 | 7.1x |

`test_the_after_examples_are_much_shorter` in `tests/test_repository.py`
recomputes these, so the table cannot quietly go stale.

Note the range. A Go pipeline shrinks by a factor of two and a half, because a
Go pipeline is small to begin with; the interesting saving there is not lines
but the fact that its coverage gate starts working. The seven-fold number on
the GitLab side is real but flattered by the container and release stages
being folded in at the same time.

## What the numbers do not say

Line count is the least interesting thing that changed. The three properties
that matter are not visible in the table:

* **Every action reference is pinned and stays pinned.** In the before files
  they float on tags — one of them on `@master`. In the after files the caller
  writes no `uses:` for an action at all, so there is nothing left to drift.
* **The gates actually run.** Three of the four before files contain a
  coverage or vulnerability gate that cannot fail. After the move all four use
  a gate with tests of its own.
* **There is one copy.** The before files are four variations on the same
  workflow, and the variations were not decisions — they are the residue of
  four different weeks.

## Checking them

The before files are held to the standard they violate, and the after files to
the one they meet:

```bash
make reject                       # every before file must be rejected
python3 -m ci_lint check --root . examples/python-service/ci.after.yml
```
