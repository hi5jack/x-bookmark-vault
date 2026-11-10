"""Discover and archive external sources referenced by X bookmarks."""

import argparse
from collections import Counter
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from library import X_HOSTS, post_body, posts, read_json
from transcribe import write_atomic

LINK = re.compile(r'https?://[^\s<>\[\]"“”]+')
YOUTUBE = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}
TRACKERS = {"fbclid", "gclid", "dclid", "mc_cid", "mc_eid"}
STATUSES = {"saved": 2, "metadata_only": 1, "unavailable": 0}


def store_path(vault):
    return vault / "linked-sources.json"


def read_sources(vault):
    path = store_path(vault)
    return read_json(path) if path.is_file() else []


def identity(url):
    parsed = urlsplit(url)
    host = parsed.hostname
    if parsed.scheme not in {"https", "http"} or not host or parsed.username or parsed.password:
        raise ValueError("Expected an HTTP(S) content URL")
    params = parse_qsl(parsed.query, keep_blank_values=True)
    if host in YOUTUBE:
        parts = parsed.path.strip("/").split("/")
        if host.endswith("youtu.be") and len(parts) == 1:
            video = parts[0]
        elif parts == ["watch"]:
            video = dict(params).get("v", "")
        elif len(parts) == 2 and parts[0] in {"shorts", "live", "embed"}:
            video = parts[1]
        else:
            video = ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
            raise ValueError("YouTube link must identify a single video")
        return f"https://www.youtube.com/watch?v={video}", "youtube"
    clean = [(key, value) for key, value in params
             if not key.lower().startswith("utm_") and key.lower() not in TRACKERS]
    port = parsed.port
    if port and (parsed.scheme, port) not in {("https", 443), ("http", 80)}:
        host = f"{host}:{port}"
    return urlunsplit((parsed.scheme, host, parsed.path or "/", urlencode(clean), "")), "article"


def observed_url(raw):
    url = raw.rstrip(".,;!?，。；！？”")
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1]
    return url


def youtube_start(url):
    parsed = urlsplit(url)
    params = dict(parse_qsl(parsed.query) + parse_qsl(parsed.fragment))
    value = params.get("t") or params.get("start") or ""
    if re.fullmatch(r"\d{1,8}", value):
        return int(value)
    units = re.fullmatch(r"(?:(\d{1,6})h)?(?:(\d{1,6})m)?(?:(\d{1,6})s)?", value)
    if units and value:
        hours, minutes, seconds = (int(item or 0) for item in units.groups())
        return 3600 * hours + 60 * minutes + seconds
    return None


def occurrences(vault, selection=None):
    archived = list(posts(vault))
    if selection is not None:
        quoted = {ref["id"] for _, post in archived if post["id"] in selection
                  for ref in post.get("post_references", []) if ref["kind"] == "quote"}
        selection = selection | quoted
    for directory, post in archived:
        if selection is not None and post["id"] not in selection:
            continue
        urls = Counter(observed_url(match) for match in LINK.findall(post_body(directory, post)))
        for raw, count in urls.items():
            try:
                if urlsplit(raw).hostname in X_HOSTS:
                    continue
                canonical, kind = identity(raw)
            except ValueError:
                continue
            reference = {
                "post_id": post["id"], "post_url": post.get("capture_url") or post["source_url"],
                "url": raw, "occurrences": count,
            }
            if kind == "youtube" and (start := youtube_start(raw)) is not None:
                reference["start"] = start
            yield canonical, kind, reference


def matching_aliases(record):
    return set(record["aliases"]) | {record["source_url"]}


def evidence(vault):
    records = read_sources(vault)
    if not records:
        return
    references = list(occurrences(vault))
    for record in records:
        aliases = matching_aliases(record)
        yield dict(record, references=[ref for url, _, ref in references if url in aliases])


def discover(vault, selection):
    registered = {url: row for row in read_sources(vault) for url in matching_aliases(row)}
    found = {}
    for url, kind, reference in occurrences(vault, selection):
        existing = registered.get(url)
        canonical = existing["source_url"] if existing else url
        row = found.setdefault(canonical, dict(source_url=canonical, kind=kind,
                                               status=existing["status"] if existing else "new", references=[]))
        row["references"].append(reference)
    return list(found.values())


def segments_from(row):
    segments = []
    for segment in row.get("segments", []):
        start, end = float(segment["start"]), float(segment["end"])
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end):
            raise ValueError("Invalid transcript timestamp")
        if segment["text"].strip():
            segments.append({"start": start, "end": end, "text": segment["text"].strip()})
    return sorted(segments, key=lambda item: item["start"])


def import_sources(vault, manifest):
    rows = read_json(manifest)
    if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
        raise ValueError("Expected a list of 1–20 linked sources")
    references = list(occurrences(vault))
    observed = {url for url, _, _ in references}
    vault.mkdir(parents=True, mode=0o700, exist_ok=True)
    with (vault / ".linked-sources.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = read_sources(vault)
        records = list(previous)
        touched = []
        for row in rows:
            original, original_kind = identity(row["url"])
            canonical, kind = identity(row.get("canonical_url") or row["url"])
            resolved, resolved_kind = identity(row.get("resolved_url") or row["url"])
            aliases = {original, resolved, canonical}
            youtube_aliases = {url for url, alias_kind in ((original, original_kind), (resolved, resolved_kind))
                               if alias_kind == "youtube"}
            if youtube_aliases - {canonical}:
                raise ValueError("YouTube aliases must identify the same video")
            if kind == "article" and urlsplit(resolved).hostname.removeprefix("www.") != urlsplit(canonical).hostname.removeprefix("www."):
                raise ValueError("Article canonical must be on the resolved publisher's domain")
            matching = [item for item in records if aliases & matching_aliases(item)]
            if not aliases & observed and not matching:
                raise ValueError("Linked source is absent from saved bookmarks")
            for item in matching:
                aliases |= matching_aliases(item)
            segments = segments_from(row)
            content = row.get("content", "").strip()
            status = row.get("status") or ("saved" if segments or content else "metadata_only")
            if status not in STATUSES or (status == "saved" and not (segments or content)):
                raise ValueError("Invalid linked-source content status")
            fresh = dict(source_url=canonical, kind=kind, title=row.get("title", ""), author=row.get("author", ""),
                         published=row.get("published"), duration=row.get("duration"), language=row.get("language"),
                         status=status, reason=row.get("reason"), content=content, segments=segments)
            record = max([fresh, *matching], key=lambda item: STATUSES[item["status"]])
            if record is fresh:
                for item in matching:
                    for key in ("title", "author", "published", "duration", "language"):
                        record[key] = record[key] or item.get(key)
            record = dict(record, aliases=sorted(aliases))
            record["references"] = [ref for url, _, ref in references if url in aliases]
            records = [item for item in records if item not in matching] + [record]
            touched.append(record["source_url"])
        records.sort(key=lambda item: item["source_url"])
        if records != previous:
            write_atomic(store_path(vault), json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    return [dict(source_url=row["source_url"], status=row["status"], title=row["title"],
                 references=len({ref["post_id"] for ref in row["references"]}), segments=len(row["segments"]))
            for row in records if row["source_url"] in touched]


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--post-id", action="append", help="List links from specific archived X posts")
    parser.add_argument("--all", action="store_true", help="List links from the whole vault")
    parser.add_argument("--save", type=Path, help="Archive an explicit JSON manifest of fetched public sources")
    args = parser.parse_args()
    vault = Path(os.environ["X_BOOKMARK_VAULT"]).resolve()
    if args.save:
        if args.post_id or args.all:
            parser.error("--save already uses the manifest's selected sources")
        result = import_sources(vault, args.save)
    else:
        recent = vault / "latest-run.json"
        selection = set(args.post_id) if args.post_id else None
        if selection is None and not args.all and recent.is_file():
            selection = {post["id"] for post in read_json(recent)["posts"]}
        result = discover(vault, selection)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(str(error))
