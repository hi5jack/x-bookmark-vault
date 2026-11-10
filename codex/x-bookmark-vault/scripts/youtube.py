"""Archive available public YouTube captions for an observed bookmark link."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from sources import discover, identity, import_sources
from sync import curl_route


def timed_segments(events, duration):
    cues = []
    for event in events:
        text = " ".join("".join(part.get("utf8", "") for part in event.get("segs", [])).split())
        if text and "tStartMs" in event and "dDurationMs" in event:
            start = event["tStartMs"] / 1000
            if 0 <= start < duration:
                cues.append((start, start + event["dDurationMs"] / 1000, text))
    cues.sort(key=lambda item: item[0])
    segments = []
    for index, (start, end, text) in enumerate(cues):
        next_start = cues[index + 1][0] if index + 1 < len(cues) else duration
        end = round(max(start, min(end, next_start)), 3)
        if segments:
            last = segments[-1]
            if not text.startswith(">>") and start - last["end"] <= 1.5 and end - last["start"] <= 15:
                last["text"] += " " + text
                last["end"] = end
                continue
        segments.append(dict(start=start, end=end, text=text))
    return segments


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    args = parser.parse_args()
    vault = Path(os.environ["X_BOOKMARK_VAULT"]).resolve()
    url, kind = identity(args.url)
    if kind != "youtube":
        raise ValueError("Expected a YouTube video link")
    existing = next((row for row in discover(vault, None) if row["source_url"] == url), None)
    if existing is None:
        raise ValueError("This YouTube link is absent from the saved X posts")
    if existing["status"] == "saved":
        print("YouTube source already archived.")
        return

    route = curl_route()
    proxy = ["--proxy", route[route.index("--proxy") + 1]] if "--proxy" in route else []
    command = ["yt-dlp", "--ignore-config", "--no-playlist", "--skip-download", "--socket-timeout", "12",
               "--retries", "1", "--extractor-retries", "0", "--no-progress", *proxy]
    result = subprocess.run([*command, "--dump-single-json", url], capture_output=True, timeout=70)
    if result.returncode or not result.stdout:
        raise RuntimeError(f"YouTube metadata unavailable (yt-dlp exit {result.returncode})")
    info = json.loads(result.stdout)
    duration = info.get("duration")

    manual = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    track = next((key for key in ("en", "en-US", "en-GB") if key in manual), None)
    automatic_track = track is None
    if automatic_track:
        track = next((key for key in ("en-orig", "en") if key in automatic), None)

    with tempfile.TemporaryDirectory(prefix="x-vault-captions-") as temp:
        staging = Path(temp)
        segments = []
        if track:
            flag = "--write-auto-subs" if automatic_track else "--write-subs"
            subprocess.run([*command, flag, "--sub-langs", track, "--sub-format", "json3",
                            "-o", str(staging / "episode.%(ext)s"), url], capture_output=True, timeout=90)
            path = staging / f"episode.{track}.json3"
            if path.is_file():
                try:
                    events = json.loads(path.read_text())["events"]
                    segments = timed_segments(events, duration or float("inf"))
                except (ValueError, TypeError, KeyError):
                    pass
        provenance = "YouTube automatic captions" if automatic_track else "YouTube publisher captions"
        record = dict(url=url, title=info.get("title") or "", author=info.get("channel") or "",
                      duration=duration, status="saved" if segments else "metadata_only",
                      language="en" if segments else None, segments=segments,
                      reason=(f"{provenance}. No verified speaker mapping." if segments
                              else "English YouTube captions could not be retrieved."))
        date = info.get("upload_date") or ""
        if re.fullmatch(r"[0-9]{8}", date):
            record["published"] = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        manifest = staging / "source.json"
        manifest.write_text(json.dumps([record]), encoding="utf-8")
        print(json.dumps(import_sources(vault, manifest), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
