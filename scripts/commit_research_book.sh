#!/usr/bin/env bash
# Race-safe commit of a discovery run's merged outputs + the monitored book.
#
# 1d/1h/15m run concurrently and each rewrites the same derived files, so
# `git pull --rebase` always conflicts and strands a half-applied rebase.
# The book is DERIVED state: on a rejected push, discard our commit, reset
# onto the remote tip, restore only this run's merged/<tf>/ output, and
# re-derive the book from every timeframe registry now present.
#
# Usage: scripts/commit_research_book.sh <timeframe> [--sync-book]
set -euo pipefail

TIMEFRAME="${1:?usage: commit_research_book.sh <timeframe> [--sync-book]}"
SYNC_BOOK="${2:-}"
BRANCH="${GITHUB_REF_NAME:-main}"
MERGED_DIR="localdata/research/merged/${TIMEFRAME}"
ATTEMPTS="${COMMIT_ATTEMPTS:-6}"

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

PATHS=(
  "${MERGED_DIR}"
  localdata/research/monitored_book_lifecycle.json
  localdata/monitored_slices.csv
  localdata/monitored_edge_metrics.csv
)

stage() {
  local existing=() p
  for p in "${PATHS[@]}"; do
    [ -e "$p" ] && existing+=("$p")
  done
  if ((${#existing[@]})); then git add -- "${existing[@]}"; fi
}

for attempt in $(seq 1 "${ATTEMPTS}"); do
  stage

  if git diff --cached --quiet; then
    echo "commit_research_book: no ${TIMEFRAME} changes to commit."
    exit 0
  fi

  git commit -q -m "chore(research-discovery-${TIMEFRAME}): merge complete shard set and update monitored book [skip ci]"

  if git push origin "HEAD:${BRANCH}"; then
    echo "commit_research_book: pushed ${TIMEFRAME} on attempt ${attempt}."
    exit 0
  fi

  [ "${attempt}" -eq "${ATTEMPTS}" ] && break

  echo "commit_research_book: push rejected (${attempt}/${ATTEMPTS}); rebuilding on origin/${BRANCH}."

  scratch="$(mktemp -d)"
  [ -d "${MERGED_DIR}" ] && cp -a "${MERGED_DIR}" "${scratch}/tf"

  git fetch --quiet origin "${BRANCH}"
  git reset --hard --quiet "origin/${BRANCH}"

  if [ -d "${scratch}/tf" ]; then
    rm -rf "${MERGED_DIR}"
    mkdir -p "$(dirname "${MERGED_DIR}")"
    cp -a "${scratch}/tf" "${MERGED_DIR}"
  fi
  rm -rf "${scratch}"

  [ "${SYNC_BOOK}" = "--sync-book" ] && python3 scripts/sync_monitored.py

  sleep $(( attempt * 5 + RANDOM % 7 ))
done

echo "::error::commit_research_book: could not push the ${TIMEFRAME} book after ${ATTEMPTS} attempts"
exit 1
