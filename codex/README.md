# X Bookmark Vault — macOS beta 0.1.0-beta.2

Use recent X bookmarks to make a storyboard, a brief, or a reading plan on your Mac, with links to the original sources.

## 1. Install

You need macOS 26 or newer, the ChatGPT desktop app with Codex and Chrome browser access, and an X account already signed in to Chrome. This beta has been tested on Apple silicon.

1. Extract the whole ZIP, then open `Install.command` in Finder. If macOS blocks the downloaded command, open Terminal, type `/bin/bash ` with the trailing space, drag `Install.command` into the Terminal window, then press Return.
2. Follow the installer's dependency check. If it asks for Apple Command Line Tools, enter `xcode-select --install` in Terminal and finish the Apple prompt. Use Homebrew to install FFmpeg for X videos with `brew install ffmpeg`. Install optional YouTube caption support with `brew install yt-dlp`. If you don't have Homebrew, follow the installation instructions at [brew.sh](https://brew.sh/). Then rerun the installer to check your setup.
3. Follow the welcome page that the installer opens. You can also open `Start Here.html` from the extracted folder. In **Settings → Computer Use**, set up Chrome if prompted.

The installer copies the skill into `~/.agents/skills/x-bookmark-vault`. It does not need administrator access, make network requests, or overwrite a different copy. To upgrade, move the old skill folder out of your skills directory and run the new installer. If you have an older copy under `~/.codex/skills`, move it out of that directory first. The vault stays at `~/.local/share/x-bookmark-vault/vault`.

## 2. Make something from your first sync

1. Open X Bookmarks in the Chrome profile where you are signed in. In Codex desktop, use the @ menu to tag that tab.
2. Click **Copy starter prompt** on the welcome page, paste it into that chat, and send it.
3. Pick one of the suggested outputs by replying with its number. Codex uses the saved posts, fills in relevant evidence as available, and links to your finished Markdown file in the vault's `answers` folder.

If the welcome page is unavailable, paste this prompt:

> $x-bookmark-vault Sync my recent X bookmarks from the Chrome tab and show me what I can make from them. Start with the saved text. Offer three specific choices with sources. When I pick one, fill in any evidence it needs and save the result to my local vault.

The starter saves up to 20 recent bookmarks. You can also ask to finish a full sync or search earlier saved posts. Available images, original X clips, and linked sources can be archived locally. Apple Speech may download a language asset on first use. YouTube captions, when available, link back to the original video at the cited time.

## Where data goes

The vault lives on your Mac. The skill reads the visible Chrome bookmarks page without extracting browser cookies. Public post lookups use FxTwitter, which receives the public post path, and media is fetched from X's CDN. Links you ask to archive may be fetched from their publishers or YouTube. Codex can read vault contents when you ask it to describe images or answer questions. X OAuth, an OpenAI API key, and Whisper are unnecessary for this beta.

## Troubleshooting and removal

- If Chrome access stalls, open the bookmarks page yourself in your signed-in Chrome profile and tag that tab in Codex.
- If media setup is incomplete, run `Install.command` again or run `~/.agents/skills/x-bookmark-vault/scripts/vault doctor` in Terminal.
- To uninstall, move `~/.agents/skills/x-bookmark-vault` out of the skills directory. Remove the separate vault only if you want to delete the archived material.
