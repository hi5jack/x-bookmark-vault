#!/bin/bash
set -euo pipefail
umask 077

release_dir="$(cd "$(dirname "$0")" && pwd)"
source_dir="$release_dir/skills/x-bookmark-vault"
skills_dir="$HOME/.claude/skills"
target="$skills_dir/x-bookmark-vault"
if [[ "${1:-}" == --check && $# == 1 ]]; then
  exec "$source_dir/scripts/vault" doctor
fi
if [[ $# != 0 ]]; then
  echo 'Usage: Install.command [--check]' >&2
  exit 2
fi
if [[ "$(uname -s)" != Darwin || "$(/usr/bin/sw_vers -productVersion | cut -d. -f1)" -lt 26 ]]; then
  echo 'X Bookmark Vault beta requires macOS 26 or newer.' >&2
  exit 1
fi
if [[ ! -f "$source_dir/SKILL.md" ]]; then
  echo 'Extract the entire ZIP and run Install.command next to the skills folder.' >&2
  exit 1
fi
if compgen -G "$HOME/.claude/plugins/cache/*/x-bookmark-vault" >/dev/null; then
  echo 'Note: the X Bookmark Vault plugin is also installed. Keep one copy to avoid two skills with the same purpose.'
fi

mkdir -p "$skills_dir"
lock="$skills_dir/.x-bookmark-vault-install.lock"
if ! mkdir "$lock" 2>/dev/null; then
  echo 'Another installation is running. If it was interrupted, remove the empty .x-bookmark-vault-install.lock directory.' >&2
  exit 1
fi
trap 'rmdir "$lock"' EXIT
if [[ -e "$target" || -L "$target" ]]; then
  if [[ ! -L "$target" ]] && diff -qr "$source_dir" "$target" >/dev/null; then
    echo 'This release is already installed.'
  else
    printf 'A different copy already exists at %s\nMove that skill folder aside before installing this release.\n' "$target" >&2
    exit 1
  fi
else
  staging="$lock/skill"
  mkdir "$staging"
  trap 'rm -rf -- "$staging"; rmdir "$lock"' EXIT
  cp -R "$source_dir/." "$staging/"
  chmod 700 "$staging/scripts/vault"
  /bin/mv "$staging" "$target"
  echo "Installed X Bookmark Vault at $target"
fi

if ! "$target/scripts/vault" doctor; then
  echo 'The skill was installed. Complete the setup items above, then rerun this installer to check them.'
fi
welcome="$release_dir/Start Here.html"
printf 'Open the welcome page to start.\n%s\n' "$welcome"
if [[ -t 1 ]]; then
  /usr/bin/open -a "Google Chrome" "$welcome" 2>/dev/null ||
    /usr/bin/open "$welcome" ||
    echo 'Open Start Here.html from the extracted folder.'
fi
