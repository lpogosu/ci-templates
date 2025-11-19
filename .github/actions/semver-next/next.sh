#!/usr/bin/env bash
# Derive the next semantic version and a changelog fragment from the
# conventional commits made since the most recent tag carrying TAG_PREFIX.
#
# Reads:  TAG_PREFIX, INITIAL_VERSION, GITHUB_OUTPUT, RUNNER_TEMP
# Writes: the outputs listed in action.yml, plus a markdown fragment.
#
# Kept free of Actions-only constructs so that it can be run against a throw
# away git repository from the test suite, which is the only way any of this
# logic is ever going to be checked before it decides a version number.
set -euo pipefail

: "${TAG_PREFIX:=v}"
: "${INITIAL_VERSION:=0.1.0}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT must name a writable file}"
: "${RUNNER_TEMP:?RUNNER_TEMP must name a writable directory}"

changelog="${RUNNER_TEMP}/semver-next-changelog.md"

# --sort=-v:refname compares version components numerically, so 1.10.0 sorts
# above 1.9.0. Lexicographic sorting gets that backwards and would measure the
# range from a stale tag without ever failing.
previous="$(git tag --list "${TAG_PREFIX}[0-9]*" --sort=-v:refname | head -n 1)"

if [ -n "${previous}" ]; then
  range="${previous}..HEAD"
  previous_version="${previous#"${TAG_PREFIX}"}"
else
  range="HEAD"
  previous_version=""
fi

breaking=()
features=()
fixes=()
performance=()
other=()
count=0

# %x1f separates the fields of one commit, %x1e separates commits, so a commit
# body containing blank lines cannot be mistaken for a record boundary.
while IFS=$'\x1f' read -r -d $'\x1e' sha subject body; do
  sha="${sha#$'\n'}"
  [ -n "${sha}" ] || continue
  count=$((count + 1))
  short="${sha:0:7}"

  type=""
  scope=""
  bang=""
  description="${subject}"
  if [[ "${subject}" =~ ^([a-zA-Z]+)(\(([^\)]*)\))?(!)?:[[:space:]]+(.+)$ ]]; then
    type="${BASH_REMATCH[1],,}"
    scope="${BASH_REMATCH[3]}"
    bang="${BASH_REMATCH[4]}"
    description="${BASH_REMATCH[5]}"
  fi

  if [ -n "${scope}" ]; then
    entry="- **${scope}**: ${description} (\`${short}\`)"
  else
    entry="- ${description} (\`${short}\`)"
  fi

  # Both spellings are in the Conventional Commits specification, and both
  # turn up in real history.
  if [ -n "${bang}" ] || grep -qE '^BREAKING[ -]CHANGE:' <<<"${body}"; then
    breaking+=("${entry}")
    continue
  fi

  case "${type}" in
    feat) features+=("${entry}") ;;
    fix) fixes+=("${entry}") ;;
    perf) performance+=("${entry}") ;;
    *) other+=("${entry}") ;;
  esac
done < <(git log --no-merges --format='%H%x1f%s%x1f%b%x1e' "${range}")

if [ -z "${previous_version}" ]; then
  if [ "${count}" -eq 0 ]; then
    bump="none"
    version=""
  else
    bump="initial"
    version="${INITIAL_VERSION}"
  fi
else
  IFS=. read -r major minor patch <<<"${previous_version}"
  major="${major:-0}"
  minor="${minor:-0}"
  patch="${patch:-0}"

  if [ "${#breaking[@]}" -gt 0 ]; then
    if [ "${major}" -eq 0 ]; then
      # Semver 4: anything may change at any time while the major version is
      # zero. Bumping 0.x to 1.0.0 on the first `feat!:` would announce a
      # stable public API that nobody decided to publish.
      bump="minor"
    else
      bump="major"
    fi
  elif [ "${#features[@]}" -gt 0 ]; then
    bump="minor"
  elif [ "${#fixes[@]}" -gt 0 ] || [ "${#performance[@]}" -gt 0 ]; then
    bump="patch"
  else
    bump="none"
  fi

  case "${bump}" in
    major) version="$((major + 1)).0.0" ;;
    minor) version="${major}.$((minor + 1)).0" ;;
    patch) version="${major}.${minor}.$((patch + 1))" ;;
    none) version="" ;;
  esac
fi

section() {
  local title="$1"
  shift
  [ "$#" -gt 0 ] || return 0
  printf '### %s\n\n' "${title}"
  printf '%s\n' "$@"
  printf '\n'
}

{
  if [ -n "${version}" ]; then
    printf '## %s%s\n\n' "${TAG_PREFIX}" "${version}"
  else
    printf '## No release\n\n'
  fi
  section 'Breaking changes' ${breaking[@]+"${breaking[@]}"}
  section 'Features' ${features[@]+"${features[@]}"}
  section 'Fixes' ${fixes[@]+"${fixes[@]}"}
  section 'Performance' ${performance[@]+"${performance[@]}"}
  # Commits without a recognised type land here rather than disappearing: a
  # changelog that quietly drops history is worse than a noisy one.
  section 'Other' ${other[@]+"${other[@]}"}
  if [ -n "${previous}" ]; then
    printf '%s commit(s) since %s.\n' "${count}" "${previous}"
  else
    printf '%s commit(s), first release.\n' "${count}"
  fi
} >"${changelog}"

if [ -n "${version}" ]; then
  tag="${TAG_PREFIX}${version}"
else
  tag=""
fi

{
  printf 'version=%s\n' "${version}"
  printf 'tag=%s\n' "${tag}"
  printf 'previous-tag=%s\n' "${previous}"
  printf 'bump=%s\n' "${bump}"
  printf 'commits=%s\n' "${count}"
  printf 'changelog-file=%s\n' "${changelog}"
} >>"${GITHUB_OUTPUT}"

printf 'semver-next: %s -> %s (%s, %s commit(s))\n' \
  "${previous:-<none>}" "${tag:-<no release>}" "${bump}" "${count}"
