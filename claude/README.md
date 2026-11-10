# X Bookmark Vault for Claude Code — macOS beta 0.1.0-beta.3

Use recent X bookmarks to make a storyboard, a brief, or a reading plan on your Mac, with links to the original sources.

## 1. Install

You need macOS 26 or newer, Claude Code with [Claude in Chrome](https://code.claude.com/docs/en/chrome), and an X account already signed in to Chrome. Claude in Chrome requires a direct Claude plan (Pro, Max, Team, or Enterprise). This beta has been tested on Apple silicon.

### Option A: plugin from GitHub (recommended)

Run these in Terminal:

```bash
claude plugin marketplace add hi5jack/x-bookmark-vault
```

```bash
claude plugin install x-bookmark-vault@x-bookmark-vault
```

Inside a Claude Code session you can use `/plugin marketplace add hi5jack/x-bookmark-vault` and `/plugin install x-bookmark-vault@x-bookmark-vault` instead. Update later with `claude plugin update x-bookmark-vault@x-bookmark-vault`. Then start Claude Code and ask it to run the X Bookmark Vault doctor to check your media tools.

### Option B: ZIP installer

1. Extract the whole ZIP, then open `Install.command` in Finder. If macOS blocks the downloaded command, open Terminal, type `/bin/bash ` with the trailing space, drag `Install.command` into the Terminal window, then press Return.
2. The installer copies the skill into `~/.claude/skills/x-bookmark-vault`. It does not need administrator access, make network requests, or overwrite a different copy. To upgrade, move the old skill folder out of `~/.claude/skills` and run the new installer.

### Media tools

The setup check lists anything missing. If it asks for Apple Command Line Tools, enter `xcode-select --install` in Terminal and finish the Apple prompt. Install FFmpeg for X videos with `brew install ffmpeg`, and optional YouTube caption support with `brew install yt-dlp`. If you don't have Homebrew, follow the instructions at [brew.sh](https://brew.sh/). Run `Install.command --check` (ZIP) or ask Claude to run the vault doctor again afterward.

## 2. Make something from your first sync

1. Start Claude Code with Chrome access: run `claude --chrome`, or run `/chrome` in an open session. In the Claude desktop app, use the Code tab with Claude in Chrome connected.
2. Click **Copy starter prompt** on `Start Here.html`, paste it into Claude Code, and send it. Allow x.com when Claude in Chrome asks.
3. Pick one of the suggested outputs by replying with its number. Claude uses the saved posts, fills in relevant evidence as available, and links to your finished Markdown file in the vault's `answers` folder.

If the welcome page is unavailable, paste this prompt:

> Use the x-bookmark-vault skill to sync my recent X bookmarks from Chrome and show me what I can make from them. Start with the saved text. Offer three specific choices with sources. When I pick one, fill in any evidence it needs and save the result to my local vault.

You can also invoke the skill directly with `/x-bookmark-vault` (ZIP install) or `/x-bookmark-vault:x-bookmark-vault` (plugin). The starter saves up to 20 recent bookmarks. You can also ask to finish a full sync or search earlier saved posts. Available images, original X clips, and linked sources can be archived locally. Apple Speech may download a language asset on first use. YouTube captions, when available, link back to the original video at the cited time.

## Where data goes

The vault lives on your Mac at `~/.local/share/x-bookmark-vault/vault`, the same vault the Codex edition uses. Claude reads the rendered Bookmarks timeline in a new Chrome tab with a small script that ships with the skill (`scripts/read-bookmarks.js`); it does not read cookies or browser storage or call X's APIs. Public post lookups use FxTwitter, which receives the public post path, and media is fetched from X's CDN. Links you ask to archive may be fetched from their publishers or YouTube. Claude can read vault contents when you ask it to describe images or answer questions. X OAuth, an OpenAI API key, and Whisper are unnecessary for this beta.

While the skill runs, it pre-approves its own `vault` helper, reads inside `~/.local/share/x-bookmark-vault`, and writes temporary manifests to `~/.local/share/x-bookmark-vault/tmp`. Claude Code may still ask before the Chrome steps and before other commands.

## Troubleshooting and removal

- If Chrome access stalls, run `/chrome` to check the connection, open `https://x.com/i/bookmarks` yourself in your signed-in Chrome profile, and ask Claude to try again.
- If X changes its layout and the reader script finds no posts, Claude falls back to the page's accessibility tree, which gives shorter previews; the enrich step still fetches the full text.
- If media setup is incomplete, run the installer check again or `~/.claude/skills/x-bookmark-vault/scripts/vault doctor` in Terminal.
- To uninstall, run `claude plugin uninstall x-bookmark-vault@x-bookmark-vault`, or move `~/.claude/skills/x-bookmark-vault` out of the skills directory. Remove the separate vault only if you want to delete the archived material.
