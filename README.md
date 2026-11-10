# X Bookmark Vault

Turn recent X bookmarks into a brief, storyboard, or reading plan. Browse the original sources and jump to cited video timestamps. Your archive and finished Markdown files live on your Mac.

## Install for Codex

1. Download the macOS ZIP from the [v0.1.0-beta.2 pre-release](https://github.com/hi5jack/x-bookmark-vault/releases/tag/v0.1.0-beta.2).
2. Extract it and open `Install.command`.
3. Follow the welcome page to start with your X bookmarks in Codex.

The beta requires macOS 26 or newer, Codex in the ChatGPT desktop app, and a signed-in X session in Chrome. See the [Codex installation guide](codex/README.md) for media dependencies and troubleshooting.

The [Codex source](codex/) includes the installer, welcome page, and [skill](codex/x-bookmark-vault/).

## Install for Claude Code

Add this repository as a plugin marketplace and install the plugin:

```bash
claude plugin marketplace add hi5jack/x-bookmark-vault
```

```bash
claude plugin install x-bookmark-vault@x-bookmark-vault
```

Then start Claude Code with `claude --chrome` and send the starter prompt from the [Claude Code guide](claude/README.md). The guide also covers the ZIP installer from the [v0.1.0-beta.3 pre-release](https://github.com/hi5jack/x-bookmark-vault/releases/tag/v0.1.0-beta.3), which copies the skill into `~/.claude/skills`. The Claude Code edition requires macOS 26 or newer and [Claude in Chrome](https://code.claude.com/docs/en/chrome) with a signed-in X session. It uses the same local vault as the Codex edition.

The [Claude Code source](claude/) is a plugin with the [skill](claude/skills/x-bookmark-vault/). Its helper scripts are copies of the Codex scripts; `tools/check-shared-scripts.sh` verifies they match.
