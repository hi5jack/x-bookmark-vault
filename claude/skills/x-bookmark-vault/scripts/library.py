"""Find local bookmark evidence and save cited answers."""

import argparse
import html
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from transcribe import timestamp, write_atomic

POST_PATH = re.compile(r"/(?:[A-Za-z0-9_]+/status|i/web/status)/(\d{1,19})/?")
X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
PREVIEW_MARKER = "[X 收藏页正文预览，完整内容请见原帖。]"


def post_id(url):
    parsed = urlsplit(url)
    match = POST_PATH.fullmatch(parsed.path)
    if parsed.scheme != "https" or parsed.netloc not in X_HOSTS or not match:
        raise ValueError("Expected an HTTPS X post URL")
    return match[1]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def posts(vault):
    for source in sorted((vault / "posts").glob("*/source.json")):
        if source.parent.name.isdecimal():
            record = read_json(source)
            if record.get("id") == source.parent.name:
                yield source.parent, record


def post_body(directory, record):
    note = (directory / "post.md").read_text(encoding="utf-8")
    body = note.split("\n---\n\n", 1)[-1].rsplit("\n\n## Source\n\n", 1)[0]
    if record.get("image_assets") or record.get("post_references"):
        body = body.rsplit("\n\n## Archive links\n\n", 1)[0]
    if any(item.get("path") for item in record["media"]):
        body = body.rsplit("\n\n## Media\n\n", 1)[0]
    return body


def media_path(directory, item):
    if item.get("status") != "saved" or not item.get("path"):
        return None
    path = (directory / item["path"]).resolve()
    return path if path.is_relative_to(directory.resolve()) and path.is_file() and path.stat().st_size else None


def latest_capture(vault):
    path = vault / "latest-run.json"
    return read_json(path) if path.is_file() else {"posts": [], "captured_at": None}


def bookmark_cursor(vault):
    posts = latest_capture(vault)["posts"]
    if not posts:
        raise ValueError("Sync recent bookmarks first")
    post = posts[-1]
    return {"after_id": post["id"], "url": post.get("capture_url") or post["source_url"]}


def latest_ids(vault):
    current = latest_capture(vault)["posts"]
    return {post["id"] for post in current} | {
        ref["id"] for post in current for ref in post.get("post_references", []) if ref["kind"] == "quote"
    }


def starter(vault):
    from sources import discover, read_sources

    capture = latest_capture(vault)
    current = capture["posts"]
    current_ids = {post["id"] for post in current}
    archived = {record["id"]: (directory, record) for directory, record in posts(vault)}
    excerpts = []
    for id_ in dict.fromkeys([post["id"] for post in current] + [
        ref["id"] for post in current for ref in post.get("post_references", []) if ref["kind"] == "quote"
    ]):
        if id_ not in archived:
            continue
        directory, record = archived[id_]
        body = post_body(directory, record).removesuffix("\n\n" + PREVIEW_MARKER)
        excerpts.append({
            "id": id_, "role": "bookmark" if id_ in current_ids else "quote",
            "source_url": record.get("capture_url") or record["source_url"],
            "note": str(directory / "post.md"), "text": body[:500], "text_truncated": len(body) > 500,
            "text_status": record.get("text_status", "unclassified"),
            "sync_pending": record.get("sync_pending", []),
            "references": record.get("post_references", []),
        })
    titles = {row["source_url"]: row["title"] for row in read_sources(vault)}
    linked = [dict(row, title=titles.get(row["source_url"], "")) for row in discover(vault, current_ids)]
    return {"captured_at": capture["captured_at"], "bookmark_count": len(current_ids),
            "posts": excerpts, "linked_sources": linked}


def find(vault, query, limit, latest=False):
    from images import asset_path, records as image_records
    from sources import evidence, store_path

    terms = tuple(dict.fromkeys(re.findall(r"[^\W_]+", query.casefold())))
    if not terms:
        raise ValueError("Provide at least one search term")
    results = []
    selected = latest_ids(vault) if latest else None
    for directory, record in posts(vault):
        if selected is not None and record["id"] not in selected:
            continue
        body = post_body(directory, record)
        status = record.get("text_status", "preview" if PREVIEW_MARKER in body else "unclassified")
        fields = {"post_id": record["id"], "author": record["author"],
                  "created_at": record["created_at"], "note": str(directory / "post.md")}
        if record.get("post_references"):
            fields["related_posts"] = record["post_references"]
        for paragraph in re.split(r"\n\s*\n", body):
            paragraph = paragraph.strip()
            if paragraph and paragraph != PREVIEW_MARKER:
                results.append(dict(fields, kind="post", source_url=record.get("capture_url", record["source_url"]),
                                    text_status=status, text=paragraph))
        for media in record["media"]:
            video = media_path(directory, media)
            if video is None or media["kind"] != "video":
                continue
            transcripts = sorted(video.parent.glob("transcript-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not transcripts:
                continue
            transcript = read_json(transcripts[0])
            for segment in transcript["segments"]:
                start, end = segment["start"], segment["end"]
                results.append(dict(fields, kind="video", source_url=transcript["source_url"], video=str(video),
                                    start=start, end=end, time=f"{timestamp(start)}–{timestamp(end)}",
                                    transcript=str(transcripts[0].with_suffix(".md")), text=segment["text"]))

    for path, record in image_records(vault):
        references = [ref for ref in record["references"] if selected is None or ref["post_id"] in selected]
        if not references or not (image := asset_path(vault, record)):
            continue
        primary = references[0]
        description = record.get("description", "")
        ocr = record.get("ocr_text", "")
        keywords = " · ".join(record.get("keywords", []))
        alt = " ".join(ref.get("alt_text") or "" for ref in references).strip()
        text = "\n\n".join(part for part in (description, keywords, alt, ocr) if part)
        if text:
            results.append(dict(kind="image", text=text, description=description, ocr_status=record["ocr_status"],
                                source_url=primary["source_url"], post_id=primary["post_id"], image=str(image),
                                note=str(path), post_references=references))

    for source in evidence(vault):
        references = [ref for ref in source["references"] if selected is None or ref["post_id"] in selected]
        if selected is not None and not references:
            continue
        fields = {"source_url": source["source_url"], "title": source["title"], "author": source["author"],
                  "created_at": source["published"], "note": str(store_path(vault)),
                  "text_status": source["status"], "post_references": references}
        if source["title"]:
            results.append(dict(fields, kind="linked_metadata", text=source["title"]))
        if source["status"] != "saved":
            continue
        for paragraph in re.split(r"\n\s*\n", source["content"]):
            if paragraph.strip():
                results.append(dict(fields, kind="linked_article", text=paragraph.strip()))
        for segment in source["segments"]:
            start, end = segment["start"], segment["end"]
            play_url = f'{source["source_url"]}&t={int(start)}s' if source["kind"] == "youtube" else source["source_url"]
            results.append(dict(fields, kind="linked_transcript", text=segment["text"], play_url=play_url,
                                start=start, end=end, time=f"{timestamp(start)}–{timestamp(end)}"))

    def score(result):
        searchable = result["text"].casefold()
        return sum(term in searchable for term in terms), int(query.casefold() in searchable)

    results = sorted((r for r in results if score(r)[0]), key=score, reverse=True)[:limit]
    for result in results:
        value = result["text"]
        if len(value) > 850:
            matches = [value.casefold().find(term) for term in terms if term in value.casefold()]
            start = max(0, min(matches, default=0) - 180)
            result["text"] = ("…" if start else "") + value[start:start + 820] + ("…" if start + 820 < len(value) else "")
    return results


def save_answer(vault, destination, answer):
    if not answer.strip():
        raise ValueError("Supply the cited Markdown answer on stdin")
    if not destination.is_absolute():
        parent = (vault / "answers").resolve()
        destination = (parent / destination).resolve()
        if not destination.is_relative_to(parent):
            raise ValueError("Relative answers must stay inside the vault answers folder")
    if destination.suffix.lower() != ".md":
        raise ValueError("The answer path must end in .md")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent) as staged:
        staged.write(answer.rstrip() + "\n")
        staged.flush()
        os.link(staged.name, destination)
    return destination


def play_link(vault, video, start):
    video = video.resolve(strict=True)
    if not video.is_relative_to(vault / "posts") or video.suffix.lower() != ".mp4":
        raise ValueError("Select a saved vault MP4 from a find result")
    if not math.isfinite(start) or start < 0:
        raise ValueError("The timestamp must be a nonnegative number of seconds")
    fragment = f"{start:.3f}"
    player = video.with_name(f"play-{round(start * 1000)}.html")
    title = html.escape(f"Play from {timestamp(start)}")
    source = html.escape(video.name, quote=True)
    write_atomic(player, f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>{title}</title>
<h1>{title}</h1>
<video controls preload="metadata" width="800" src="{source}#t={fragment}"></video>
</html>
""")
    return player


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("find")
    search.add_argument("query")
    search.add_argument("--limit", type=int, choices=range(1, 31), default=12, metavar="1–30")
    search.add_argument("--latest", action="store_true", help="Search the latest capture and its direct sources")
    commands.add_parser("cursor", help="Find the last bookmark of the most recent batch")
    commands.add_parser("starter", help="Read evidence from the latest capture for first-run options")
    save = commands.add_parser("save-answer")
    save.add_argument("destination", type=Path)
    play = commands.add_parser("play-link")
    play.add_argument("video", type=Path)
    play.add_argument("start", type=float)
    args = parser.parse_args()
    vault = Path(os.environ["X_BOOKMARK_VAULT"]).resolve()
    if args.command == "find":
        print(json.dumps(find(vault, args.query, args.limit, latest=args.latest), ensure_ascii=False, indent=2))
    elif args.command == "cursor":
        print(json.dumps(bookmark_cursor(vault), ensure_ascii=False))
    elif args.command == "starter":
        print(json.dumps(starter(vault), ensure_ascii=False, indent=2))
    elif args.command == "save-answer":
        print(save_answer(vault, args.destination, sys.stdin.read()))
    else:
        print(play_link(vault, args.video, args.start))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        sys.exit(str(error))
