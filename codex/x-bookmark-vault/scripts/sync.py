"""Enrich a browser-selected batch of up to 20 X bookmarks and transcribe locally."""

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from images import archive_photos, ocr_images
from library import PREVIEW_MARKER, media_path, post_body, post_id, posts, read_json
from transcribe import AppleSpeech, archive_transcript

LIMIT = 20
VAULT = Path(os.environ["X_BOOKMARK_VAULT"]).resolve()
RUNNER = Path(__file__).with_name("vault")
FX_HOST = "https://api.fxtwitter.com"


def curl_route():
    args = ["curl", "-q", "-fSs", "--connect-timeout", "8"]
    proxy = Path("/usr/sbin/scutil")
    if proxy.exists():
        config = subprocess.check_output([proxy, "--proxy"], text=True, timeout=10)
        parts = dict(re.findall(r"^\s*(HTTPSEnable|HTTPSProxy|HTTPSPort)\s*:\s*(\S+)\s*$", config, re.M))
        if parts.get("HTTPSEnable") == "1" and parts.get("HTTPSProxy") and parts.get("HTTPSPort"):
            args.extend(["--proxy", f'http://{parts["HTTPSProxy"]}:{parts["HTTPSPort"]}', "--noproxy", ""])
    return args


def fetch(row, curl):
    url = row["url"]
    if row.get("protected"):
        return None, "protected"
    path = urlsplit(url).path
    try:
        data = json.loads(subprocess.check_output([*curl, "--max-time", "18", "--max-filesize", "16000000",
                                                   FX_HOST + path], stderr=subprocess.DEVNULL))
        post = data.get("tweet") or data.get("status") or {}
        if data.get("code") == 200 and str(post.get("id")) == post_id(url):
            return post, None
        return None, f"FxTwitter returned {data.get('code', 'an unexpected response')}"
    except subprocess.CalledProcessError as error:
        return None, f"FxTwitter request failed (curl {error.returncode})"
    except (ValueError, AttributeError):
        return None, "Invalid FxTwitter response"


def post_text(post):
    text = post.get("text", "").strip()
    article = post.get("article") or {}
    blocks = (article.get("content") or {}).get("blocks") or []
    if blocks:
        parts = [article.get("title", "")]
        parts.extend(block.get("text", "") for block in blocks)
        return "\n\n".join(s for s in (text, *parts) if s.strip()), True
    if article:
        return "\n\n".join(s for s in (text, article.get("title", ""), article.get("preview_text", "")) if s), False
    return text, True


def media_items(post, kind):
    media = post.get("media") or {}
    items = media.get("photos" if kind == "photo" else "videos") or media.get("all") or []
    return [item for item in items if item.get("type") == kind]


def quoted_post(post):
    quote = (post or {}).get("quote") or {}
    url = quote.get("url")
    if not url:
        return {}
    try:
        return quote if post_id(url) == str(quote.get("id")) else {}
    except ValueError:
        return {}


def references_for(text, quote, root_id):
    references = {}
    if quote:
        url = quote["url"]
        references[post_id(url)] = {"id": post_id(url), "url": url, "kind": "quote"}
    links = re.findall(r"https://(?:www\.)?(?:x\.com|twitter\.com)/(?:[A-Za-z0-9_]+/status|i/web/status)/\d{1,19}\b", text)
    for url in links:
        id_ = post_id(url)
        if id_ != root_id:
            references.setdefault(id_, {"id": id_, "url": url, "kind": "link"})
    return list(references.values())


def video_variants(item):
    options = item.get("formats") or item.get("variants") or []
    options = [f for f in options if f.get("container") == "mp4" or f.get("content_type") == "video/mp4"]
    options.sort(key=lambda f: f.get("bitrate") or 0, reverse=True)
    for url in dict.fromkeys([f.get("url") for f in options] + [item.get("url")]):
        if isinstance(url, str):
            parsed = urlsplit(url)
            if parsed.scheme == "https" and parsed.netloc == "video.twimg.com" and parsed.path.endswith(".mp4"):
                yield url


def download(item, target, curl):
    if not shutil.which("ffprobe"):
        return False
    for url in video_variants(item):
        result = subprocess.run([*curl, "--max-time", "95", "--max-filesize", "536870912", "-o", target, url],
                                capture_output=True)
        if result.returncode:
            continue
        try:
            probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                    "stream=codec_type", "-of", "csv=p=0", target], capture_output=True, timeout=25)
        except subprocess.TimeoutExpired:
            continue
        if probe.returncode == 0 and b"video" in probe.stdout:
            return True
    target.unlink(missing_ok=True)
    return False


def saved_media(directory, record):
    return [(item, path) for item in record.get("media", [])
            if item.get("kind") == "video" and (path := media_path(directory, item))]


def legacy_match(existing, origin):
    candidates = []
    for item, path in existing:
        if item.get("media_id"):
            continue
        transcript = path.with_name("transcript-apple.json")
        source = item.get("source_url") or (read_json(transcript).get("source_url") if transcript.is_file() else None)
        if source and post_id(source) == post_id(origin):
            candidates.append(item)
    return candidates[0] if len(candidates) == 1 else None


def enrich(row, existing, response, media_owners, stage, curl):
    post, failure = response
    url = row["url"]
    id_ = post_id(url)
    directory, prior = existing or (VAULT / "posts" / id_, {})
    saved = saved_media(directory, prior)
    text = row.get("text", "").removesuffix("\n\n" + PREVIEW_MARKER)
    full = not row.get("partial", False)
    quote = quoted_post(post)
    photos = prior.get("image_assets", [])
    failed_photos = 0
    references = prior.get("post_references", [])
    if post is not None:
        text, full = post_text(post)
        references = references_for(text, quote, id_)
        photos, failed_photos = archive_photos(VAULT, url, media_items(post, "photo"), stage, curl)
    elif prior.get("text_status") == "full":
        text, full = post_body(directory, prior), True

    selected = {}
    if post is not None:
        for video in media_items(post, "video"):
            media_id = str(video.get("id", ""))
            if re.fullmatch(r"\d{1,24}", media_id):
                selected.setdefault(media_id, (post.get("url") or url, video))

    annotations = {}
    paths, keys = [], []
    failed_videos = 0
    for media_id, (origin, video) in selected.items():
        origin_id = post_id(origin)
        if media_owners.get(media_id, id_) != id_:
            continue
        annotation = {"media_id": media_id, "source_url": origin}
        reuse = next((m for m, _ in saved if m.get("media_id") == media_id), None)
        if not reuse and sum(post_id(candidate) == origin_id for candidate, _ in selected.values()) == 1:
            reuse = legacy_match(saved, origin)
        if reuse:
            annotations[reuse["key"]] = annotation
        else:
            target = stage / f"{media_id}.mp4"
            if not download(video, target, curl):
                failed_videos += 1
                continue
            key = f"fx-{media_id}"
            annotations[key] = annotation
            paths.append(str(target))
            keys.append(key)
        media_owners[media_id] = id_

    observed_videos = max(0, row.get("unavailable_videos", 0) - len(media_items(quote, "video")))
    missing = failed_videos + max(0, observed_videos - max(len(saved), len(selected)))
    reasons = []
    if failure and failure != "protected":
        reasons.append(failure)
    if not full and failure != "protected":
        reasons.append("Full text unavailable")
    if missing and failure != "protected":
        reasons.append(f"{missing} video(s) unavailable")
    if failed_photos:
        reasons.append(f"{failed_photos} image(s) unavailable")
    if not full:
        text = text.rstrip() + "\n\n" + PREVIEW_MARKER
    created = row.get("created_at")
    if post and post.get("created_timestamp"):
        created = datetime.fromtimestamp(post["created_timestamp"], timezone.utc).isoformat()
    return dict(url=url, capture_url=url, author=row.get("author"), created_at=created, text=text,
                text_status="full" if full else "preview", tags=row.get("tags", prior.get("tags", [])),
                media_files=paths, media_keys=keys, media_annotations=annotations, unavailable_videos=missing,
                capture_role=row.get("capture_role", prior.get("capture_role", "bookmark")),
                post_references=references, image_assets=photos, sync_pending=reasons,
                protected=row.get("protected", False))


def archived_row(directory, record):
    return {
        "url": record.get("capture_url") or record["source_url"],
        "author": record.get("author"), "created_at": record.get("created_at"),
        "text": post_body(directory, record), "partial": record.get("text_status") == "preview",
        "unavailable_videos": sum(m.get("kind") == "video" and m["status"] != "saved" for m in record["media"]),
        "protected": record.get("protected", False),
    }


def rows_to_retry(recent, archived):
    for id_, (directory, record) in archived.items():
        if id_ not in recent and record.get("sync_pending") and record.get("capture_url"):
            yield id_, archived_row(directory, record)


def transcribe(locale, selected_ids, only_selected=False):
    targets = []
    for directory, record in posts(VAULT):
        if record["id"] not in selected_ids and (only_selected or "capture_url" not in record):
            continue
        for media, video in saved_media(directory, record):
            origin = media.get("source_url") or record.get("capture_url") or record["source_url"]
            cached = video.with_name("transcript-apple.json")
            if cached.is_file() and video.with_name("transcript-apple.md").is_file() and cached.stat().st_mtime_ns >= video.stat().st_mtime_ns:
                data = read_json(cached)
                if data.get("status") in {"transcribed", "no_speech", "no_audio"} and post_id(data["source_url"]) == post_id(origin):
                    continue
            targets.append((record["id"], video, origin))

    if not targets:
        return {}
    results = {}
    with (VAULT / ".capture.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        engine = AppleSpeech(locale)
        print(f"Model: {engine.model}. Diarization: off. Speaker mapping: none. Known-speaker references: none. Fallback: none.", flush=True)
        for id_, video, origin in targets:
            try:
                results[video.parent.name] = archive_transcript(video, origin, engine, "apple")
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                results[video.parent.name] = f"failed: {error}"
            print(id_, video.parent.name, results[video.parent.name], flush=True)
    return results


def needs_sync(existing):
    if existing is None:
        return True
    directory, record = existing
    return ("capture_url" not in record or "post_references" not in record or record.get("sync_pending")
            or not (directory / "post.md").is_file()
            or any(item.get("status") == "saved" and not media_path(directory, item) for item in record["media"])
            or any(not (VAULT / item["path"]).is_file() for item in record.get("image_assets", [])))


def observed_selection(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= LIMIT:
        raise ValueError(f"Expected a selection of 1–{LIMIT} bookmarks")
    recent = {}
    for row in rows:
        id_ = post_id(row["url"])
        count = row.get("unavailable_videos", 0)
        if type(count) is not int or not 0 <= count <= LIMIT:
            raise ValueError("Invalid observed video count")
        recent.setdefault(id_, row)
    return recent


def import_batch(rows, manifest, preserve=False):
    manifest.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    subprocess.run([RUNNER, "import", manifest, *(["--keep-latest"] if preserve else [])], check=True)


def sync_text(rows):
    batch = []
    for id_, row in observed_selection(rows).items():
        directory = VAULT / "posts" / id_
        prior_path = directory / "source.json"
        note_exists = (directory / "post.md").is_file()
        prior = read_json(prior_path) if prior_path.is_file() and note_exists else {}
        protected = row.get("protected", prior.get("protected", False))
        improved_preview = prior.get("text_status") == "preview" and row.get("text") and not row.get("partial")
        if (prior.get("capture_url") and prior.get("capture_role") == "bookmark"
                and protected == prior.get("protected", False) and not improved_preview):
            batch.append({"url": row["url"], "text": "", "reuse_existing": True})
            continue

        text = row.get("text", "")
        partial = row.get("partial", False)
        if prior.get("text_status") == "full":
            text, partial = post_body(directory, prior), False
        elif prior and not prior.get("text_status") and (partial or not text):
            previous_text = post_body(directory, prior)
            if len(previous_text) > len(text):
                text, partial = previous_text, True
        if partial:
            text = text.removesuffix("\n\n" + PREVIEW_MARKER).rstrip() + "\n\n" + PREVIEW_MARKER
        pending = [] if protected else ["Full archive pending"]
        batch.append({
            "url": row["url"], "capture_url": row["url"], "capture_role": "bookmark",
            "author": row.get("author") or prior.get("author"),
            "created_at": row.get("created_at") or prior.get("created_at"),
            "text": text, "text_status": "preview" if partial else "full",
            "protected": protected, "sync_pending": pending,
            "unavailable_videos": row.get("unavailable_videos", 0),
            "tags": row.get("tags", prior.get("tags", [])),
            "post_references": prior.get("post_references") or references_for(text, {}, id_),
        })

    with tempfile.TemporaryDirectory(prefix="x-vault-starter-") as work:
        import_batch(batch, Path(work) / "selection.json")


def latest_rows():
    manifest = VAULT / "latest-run.json"
    if not manifest.is_file():
        raise ValueError("Sync recent bookmarks before enriching the latest capture")
    archived = {record["id"]: (directory, record) for directory, record in posts(VAULT)}
    return [archived_row(*archived[item["id"]]) for item in read_json(manifest)["posts"]]


def sync(rows, locale, keep_latest=False, retry_older=True):
    recent = observed_selection(rows)
    archived = {record["id"]: (directory, record) for directory, record in posts(VAULT)}
    include_older = retry_older and not keep_latest
    pending = dict(rows_to_retry(recent, archived)) if include_older else {}
    for id_, row in recent.items():
        prior = archived.get(id_)
        prior_protection = prior[1].get("protected", False) if prior else False
        protected = row.get("protected", prior_protection)
        if (needs_sync(prior) or protected != prior_protection
                or (not keep_latest and prior[1].get("capture_role") != "bookmark")):
            pending[id_] = dict(row, protected=protected)

    owners = {item["media_id"]: id_ for id_, (directory, record) in archived.items()
              for item, _ in saved_media(directory, record) if item.get("media_id")}
    curl = curl_route() if pending else []
    with tempfile.TemporaryDirectory(prefix="x-vault-sync-") as work:
        stage = Path(work)
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = dict(zip(pending, pool.map(lambda row: fetch(row, curl), pending.values())))
        related = set()
        for post, _ in list(responses.values()):
            quote = quoted_post(post)
            if not quote:
                continue
            id_ = post_id(quote["url"])
            related.add(id_)
            if id_ not in recent and needs_sync(archived.get(id_)):
                pending.setdefault(id_, {"url": quote["url"], "capture_role": "related"})
                if not responses.get(id_, (None,))[0]:
                    responses[id_] = (quote, None)
        for id_, (post, _) in responses.items():
            for video in media_items(post or {}, "video"):
                owners.setdefault(str(video.get("id")), id_)

        changed = {}
        for id_, row in pending.items():
            print(f"Syncing {id_}", flush=True)
            effective_row = dict(row, capture_role="bookmark") if id_ in recent and not keep_latest else row
            changed[id_] = enrich(effective_row, archived.get(id_), responses[id_], owners, stage, curl)
        manifest = stage / "selection.json"
        outside_recent = [row for id_, row in changed.items() if id_ not in recent]
        batches = [(outside_recent[i:i + LIMIT], True) for i in range(0, len(outside_recent), LIMIT)]
        recent_batch = [changed.get(id_, dict(row, text=row.get("text", ""), reuse_existing=True))
                        for id_, row in recent.items()]
        batches.append((recent_batch, keep_latest))
        for batch, preserve in batches:
            import_batch(batch, manifest, preserve)

    print(f"Recent: {len(recent)}. Fetched or retried: {len(changed)}. Reused: {len(recent.keys() - changed.keys())}.", flush=True)
    for id_, row in changed.items():
        if row["sync_pending"]:
            print(f"{id_}: {', '.join(row['sync_pending'])}", flush=True)
    selected = recent.keys() | pending.keys() | related
    for id_ in recent:
        prior = archived.get(id_)
        if prior:
            selected.update(ref["id"] for ref in prior[1].get("post_references", []) if ref["kind"] == "quote")
    ocr_images(VAULT, selected)
    results = transcribe(locale, selected, only_selected=not include_older)
    return any(status.startswith("failed:") for status in results.values())


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path, nargs="?")
    parser.add_argument("--post", action="append", help="Archive a public X post without replacing the latest bookmarks")
    parser.add_argument("--locale", default="en-US")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--text-first", action="store_true", help="Save visible bookmark text before media")
    modes.add_argument("--enrich-latest", action="store_true", help="Enrich the most recent capture without Chrome")
    args = parser.parse_args()
    if sum(bool(value) for value in (args.selection, args.post, args.enrich_latest)) != 1:
        parser.error("Provide a selection file, one or more --post URLs, or --enrich-latest")
    if args.text_first and not args.selection:
        parser.error("--text-first requires a selection file")
    VAULT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (VAULT / ".sync.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.enrich_latest:
            return sync(latest_rows(), args.locale, retry_older=False)
        if args.selection:
            rows = read_json(args.selection)
            if args.text_first:
                return sync_text(rows)
            return sync(rows, args.locale)
        rows = [{"url": urlsplit(url)._replace(query="", fragment="").geturl(),
                 "partial": True, "capture_role": "manual"} for url in args.post]
        return sync(rows, args.locale, keep_latest=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
