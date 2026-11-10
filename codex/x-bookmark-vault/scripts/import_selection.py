"""Write a browser selection and its local media to an X Bookmark Vault."""

import argparse
import fcntl
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from library import media_path, post_id, read_json
from transcribe import write_atomic

LIMIT = 20
KINDS = {".mp4": "video", ".jpg": "photo", ".jpeg": "photo", ".png": "photo"}
MEDIA_KEY = re.compile(r"[0-9A-Za-z_-]+")


def save_json(path, data):
    write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def prepare(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= LIMIT:
        raise ValueError("Expected 1–20 selected X posts")
    selected = {}
    for row in rows:
        id_ = post_id(row["url"])
        if id_ in selected:
            raise ValueError("The selection contains a duplicate X post")
        files = {}
        keys = row.get("media_keys", [])
        for index, name in enumerate(row.get("media_files", [])):
            path = Path(name).resolve(strict=True)
            key = keys[index] if index < len(keys) else f"local-{id_}-{index}"
            if not MEDIA_KEY.fullmatch(key) or key in files:
                raise ValueError("Invalid or duplicate local media key")
            if path.suffix.lower() not in KINDS or not path.is_file() or not path.stat().st_size:
                raise ValueError("Expected a nonempty local MP4, JPG or PNG file")
            files[key] = path
        unavailable = row.get("unavailable_videos", 0)
        if type(unavailable) is not int or not 0 <= unavailable <= LIMIT:
            raise ValueError("unavailable_videos must be an integer from 0 to 20")
        if not isinstance(row.get("text"), str):
            raise ValueError("Expected the post text as a string")
        if any(f"unavailable-{id_}-{i}" in files for i in range(unavailable)):
            raise ValueError("Local media key collides with an unavailable video")
        selected[id_] = row, files
    return selected


def copy_media(directory, key, original):
    destination = directory / "media" / key / ("asset" + original.suffix.lower())
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".copy-") as staging:
        temp = Path(staging) / destination.name
        shutil.copyfile(original, temp)
        temp.replace(destination)
    return {"key": key, "kind": KINDS[original.suffix.lower()], "status": "saved",
            "path": str(destination.relative_to(directory))}


def save_post(directory, id_, row, files, now):
    path = directory / "source.json"
    previous = read_json(path) if path.is_file() else {}
    if row.get("reuse_existing"):
        if not previous or not (directory / "post.md").is_file():
            raise ValueError(f"Cannot reuse missing post {id_}")
        return previous

    directory.mkdir(parents=True, exist_ok=True)
    media = {key: copy_media(directory, key, original) for key, original in files.items()}
    for index in range(row.get("unavailable_videos", 0)):
        key = f"unavailable-{id_}-{index}"
        media[key] = {"key": key, "kind": "video", "status": "unavailable"}
    for item in previous.get("media", []):
        key = item["key"]
        if media.get(key, {}).get("status") != "saved" and media_path(directory, item):
            media[key] = item
    for key, item in media.items():
        annotations = row.get("media_annotations", {}).get(key, {})
        item.update({field: annotations[field] for field in ("media_id", "source_url") if field in annotations})

    handle = urlsplit(row["url"]).path.split("/")[1]
    record = {"id": id_, "author": row.get("author") or (handle if handle != "i" else None),
              "created_at": row.get("created_at"), "first_synced_at": previous.get("first_synced_at", now),
              "source_url": f"https://x.com/i/web/status/{id_}",
              "tags": list(dict.fromkeys(row.get("tags", []))), "media": list(media.values())}
    for key in ("capture_url", "text_status", "sync_pending", "capture_role", "post_references", "image_assets",
                "protected"):
        if key in row:
            record[key] = row[key]
        elif key in previous:
            record[key] = previous[key]

    header = [f"{key}: {json.dumps(value, ensure_ascii=False)}"
              for key, value in record.items() if key != "media"]
    links = [f"- [{item['kind']}]({item['path']})" for item in record["media"] if item.get("path")]
    archive = [f"- [Original image](../../{item['path']})" for item in record.get("image_assets", [])]
    for item in record.get("post_references", []):
        label = "Quoted X post" if item["kind"] == "quote" else "Linked X post"
        archive.append(f"- [{label}]({item['url']})")
    lines = ["---", *header, "---", "", row["text"]]
    for title, entries in (("Media", links), ("Archive links", archive)):
        if entries:
            lines.extend(["", f"## {title}", "", *entries])
    lines.extend(["", "## Source", "", record["source_url"], ""])
    write_atomic(directory / "post.md", "\n".join(lines))
    save_json(path, record)
    return record


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path)
    parser.add_argument("--keep-latest", action="store_true")
    args = parser.parse_args()
    selected = prepare(read_json(args.selection))
    vault = Path(os.environ["X_BOOKMARK_VAULT"]).resolve()
    vault.mkdir(parents=True, exist_ok=True)
    with (vault / ".capture.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another capture or transcription is running") from None
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        records = []
        for id_, (row, files) in selected.items():
            relative = Path("posts") / id_
            record = save_post(vault / relative, id_, row, files, now)
            records.append(dict(record, note=str(relative / "post.md")))
        if not args.keep_latest:
            save_json(vault / "latest-run.json", {"captured_at": now, "requested": LIMIT, "posts": records})
    print(f"Archived {len(records)} X post(s) to {vault}.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, KeyError, TypeError, ValueError) as error:
        sys.exit(str(error))
