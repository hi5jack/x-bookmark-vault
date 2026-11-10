#!/bin/bash
# The Codex and Claude Code packages ship the same vault helpers. Claude Code
# plugins cannot reference files outside their own folder, so the Claude copy
# is duplicated. Run with --sync to copy the Codex scripts over, or with no
# argument to fail when the copies differ.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
codex="$root/codex/x-bookmark-vault/scripts"
claude="$root/claude/skills/x-bookmark-vault/scripts"
only_claude=read-bookmarks.js

if [[ "${1:-}" == --sync ]]; then
  find "$claude" -mindepth 1 -maxdepth 1 ! -name "$only_claude" -exec rm -f {} +
  cp -p "$codex"/* "$claude"/
  echo "Copied Codex scripts into the Claude package."
elif [[ $# != 0 ]]; then
  echo 'Usage: tools/check-shared-scripts.sh [--sync]' >&2
  exit 2
fi
diff -r -x "$only_claude" "$codex" "$claude"
[[ -x "$claude/vault" ]] || { echo "claude/.../scripts/vault is not executable" >&2; exit 1; }
echo "Shared scripts match."
