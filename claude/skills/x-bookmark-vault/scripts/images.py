"""Save original X images and index local OCR and visual descriptions."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from library import post_id, read_json
from transcribe import sha256_file, write_atomic

DIGEST = re.compile(r"[0-9a-f]{64}")
MEDIA_ID = re.compile(r"\d{1,24}")


def records(vault):
    for path in sorted((vault / "images").glob("*/source.json")):
        if DIGEST.fullmatch(path.parent.name):
            yield path, read_json(path)


def save_record(path, record):
    encoded = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    if not path.is_file() or path.read_text(encoding="utf-8") != encoded:
        write_atomic(path, encoded)


def asset_path(vault, record):
    path = (vault / record["path"]).resolve()
    return path if path.is_relative_to((vault / "images").resolve()) and path.is_file() and path.stat().st_size else None


def extension(path):
    with path.open("rb") as file:
        header = file.read(12)
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("Expected an original JPEG, PNG or WebP image")


def fetch_photo(url, destination, curl):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "pbs.twimg.com" or not parsed.path.startswith("/media/"):
        return False
    result = subprocess.run([*curl, "--max-time", "25", "--max-filesize", "30000000", "-o", destination, url],
                            capture_output=True)
    return result.returncode == 0 and destination.is_file() and destination.stat().st_size > 0


def archive_photos(vault, post_url, photos, stage, curl):
    if not photos:
        return [], 0
    saved, failed = [], 0
    with (vault / ".images.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        by_media = {ref["media_id"]: (path, record) for path, record in records(vault)
                    for ref in record["references"] if ref.get("media_id")}
        for photo in photos:
            media_id = str(photo.get("id") or "")
            if not MEDIA_ID.fullmatch(media_id):
                failed += 1
                continue
            known = by_media.get(media_id)
            if known and asset_path(vault, known[1]):
                path, record = known
            else:
                temporary = stage / f"{media_id}.image"
                if not fetch_photo(photo.get("url") or "", temporary, curl):
                    failed += 1
                    continue
                try:
                    suffix = extension(temporary)
                except ValueError:
                    failed += 1
                    continue
                digest = sha256_file(temporary)
                directory = vault / "images" / digest
                directory.mkdir(parents=True, mode=0o700, exist_ok=True)
                path = directory / "source.json"
                asset = directory / ("asset" + suffix)
                if not asset.is_file() or not asset.stat().st_size:
                    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
                        temp_asset = Path(output.name)
                    try:
                        shutil.copyfile(temporary, temp_asset)
                        temp_asset.replace(asset)
                    finally:
                        temp_asset.unlink(missing_ok=True)
                record = read_json(path) if path.is_file() else {
                    "sha256": digest, "path": str(asset.relative_to(vault)), "references": [], "ocr_status": "pending",
                }
            reference = {"post_id": post_id(post_url), "source_url": post_url, "media_id": media_id,
                         "media_url": photo["url"], "alt_text": photo.get("altText") or photo.get("alt_text")}
            if not any((old["post_id"], old["media_id"]) == (reference["post_id"], media_id) for old in record["references"]):
                record["references"].append(reference)
            save_record(path, record)
            by_media[media_id] = path, record
            if record["sha256"] not in {item["sha256"] for item in saved}:
                saved.append({"sha256": record["sha256"], "path": record["path"]})
    return saved, failed


def ocr_binary():
    binary = Path(os.environ["X_BOOKMARK_OCR_BIN"])
    source = Path(__file__).with_name("AppleOCR.swift")
    if binary.is_file() and binary.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return binary
    binary.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=binary.parent, prefix=".ocr-") as work:
        target = Path(work) / "ocr"
        subprocess.run(["/usr/bin/xcrun", "swiftc", "-O", source, "-o", target], check=True, timeout=60)
        target.replace(binary)
    return binary


def ocr_images(vault, selected):
    if not (vault / "images").is_dir():
        return
    with (vault / ".images.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        pending = [(path, record, image) for path, record in records(vault)
                   if (not selected or selected.intersection(ref["post_id"] for ref in record["references"]))
                   and record.get("ocr_status") not in {"done", "no_text"} and (image := asset_path(vault, record))]
        if not pending:
            return
        try:
            binary = ocr_binary()
        except (OSError, subprocess.SubprocessError):
            print("Apple Vision OCR helper unavailable; the original images are saved.", file=sys.stderr)
            return
        for path, record, image in pending:
            try:
                lines = json.loads(subprocess.check_output([binary, image], timeout=35))
                record.update(ocr_text="\n".join(lines), ocr_status="done" if lines else "no_text")
            except (OSError, ValueError, subprocess.SubprocessError):
                record["ocr_status"] = "failed"
            save_record(path, record)
            print(f'Image {record["sha256"][:12]} OCR: {record["ocr_status"]}', flush=True)


def save_descriptions(vault, manifest):
    rows = read_json(manifest)
    if not isinstance(rows, list) or not rows:
        raise ValueError("Expected a nonempty array of image descriptions")
    with (vault / ".images.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        changes = []
        for row in rows:
            digest = row["sha256"]
            if not DIGEST.fullmatch(digest):
                raise ValueError("Expected an existing image SHA-256")
            path = vault / "images" / digest / "source.json"
            record = read_json(path)
            if not asset_path(vault, record) or not row["description"].strip():
                raise ValueError("Expected a saved image and a nonempty description")
            keywords = row.get("keywords", [])
            if not isinstance(keywords, list) or any(not isinstance(word, str) for word in keywords):
                raise ValueError("Image keywords must be a list of strings")
            record.update(description=row["description"].strip(), keywords=list(dict.fromkeys(keywords)))
            changes.append((path, record))
        for path, record in changes:
            save_record(path, record)
    return len(changes)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--post-id", action="append", default=[])
    parser.add_argument("--save", type=Path)
    parser.add_argument("--ocr", action="store_true")
    args = parser.parse_args()
    vault = Path(os.environ["X_BOOKMARK_VAULT"]).resolve()
    selected = set(args.post_id)
    if args.save:
        print(f"Saved {save_descriptions(vault, args.save)} image descriptions.")
        return
    if args.ocr:
        ocr_images(vault, selected)
        return
    print(json.dumps([dict(record, image=str(asset_path(vault, record)), note=str(path))
                      for path, record in records(vault)
                      if not selected or selected.intersection(ref["post_id"] for ref in record["references"])],
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(str(error))
