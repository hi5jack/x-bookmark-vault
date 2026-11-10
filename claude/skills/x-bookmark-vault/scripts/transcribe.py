"""Transcribe the videos in the most recent local bookmark capture."""

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


CHUNK_SECONDS = 600


def checked(command, *, timeout=900):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        detail = result.stderr.strip()
        raise RuntimeError(detail or f"{Path(command[0]).name} failed (exit {result.returncode})")
    return result.stdout


def speech_binary():
    binary = Path(os.environ["X_BOOKMARK_SPEECH_BIN"])
    source = Path(__file__).with_name("AppleSpeech.swift")
    if binary.is_file() and os.access(binary, os.X_OK) and binary.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return binary
    binary.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=binary.parent, prefix=".speech-") as staging:
        target = Path(staging) / "speech"
        checked(["/usr/bin/xcrun", "swiftc", "-O", "-parse-as-library", source, "-o", target], timeout=120)
        target.replace(binary)
    return binary


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class AppleSpeech:
    diarization = "off"

    def __init__(self, locale):
        self.binary = speech_binary()
        self.locale = locale
        self.model = f"Apple SpeechTranscriber ({locale}, system-managed model)"

    def segments(self, audio):
        return json.loads(checked([str(self.binary), str(audio), self.locale]))


def timestamp(seconds):
    millis = round(seconds * 1000)
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, rest = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{rest:03d}"


def collect_segments(video, engine):
    probe = json.loads(checked(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=index:format=duration", "-of", "json", str(video)],
        timeout=30,
    ))
    if not probe["streams"]:
        return "no_audio", []
    duration = float(probe["format"]["duration"])
    segments = []
    with tempfile.TemporaryDirectory(prefix="x-vault-audio-") as temp_dir:
        for chunk, offset in enumerate(range(0, math.ceil(duration), CHUNK_SECONDS), start=1):
            audio = Path(temp_dir) / f"{chunk}.wav"
            checked(["ffmpeg", "-v", "error", "-nostdin", "-ss", str(offset), "-i", str(video),
                     "-t", str(CHUNK_SECONDS), "-map", "0:a:0", "-ac", "1", "-ar", "16000",
                     "-c:a", "pcm_s16le", str(audio)])
            for item in engine.segments(audio):
                start, end = float(item["start"]), float(item["end"])
                text = item["text"].strip()
                if text and math.isfinite(start) and math.isfinite(end) and 0 <= start <= end and offset + start < duration:
                    segment = {"start": offset + start, "end": min(offset + end, duration), "text": text}
                    if item.get("speaker"):
                        segment["speaker"] = f"chunk {chunk} / {item['speaker']}"
                    segments.append(segment)
    return ("transcribed" if segments else "no_speech"), segments


def write_atomic(path, text):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(text)
            file.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def archive_transcript(video, source_url, engine, backend):
    stem = video.with_name(f"transcript-{backend}")
    json_path, md_path = stem.with_suffix(".json"), stem.with_suffix(".md")
    digest = sha256_file(video)
    if json_path.is_file() and md_path.is_file():
        existing = json.loads(json_path.read_text())
        if existing.get("video_sha256") == digest and existing.get("model") == engine.model:
            if existing.get("source_url") == source_url:
                return f"cached_{existing['status']}"
            existing["source_url"] = source_url
            write_transcript(json_path, md_path, existing)
            return f"cached_{existing['status']}"

    status, segments = collect_segments(video, engine)
    record = {
        "source_url": source_url, "video_sha256": digest, "status": status,
        "model": engine.model, "diarization": engine.diarization,
        "speaker_mapping": None, "known_speaker_references": [], "fallback": None,
        "segments": segments,
    }
    write_transcript(json_path, md_path, record)
    return status


def write_transcript(json_path, md_path, record):
    lines = ["# Video transcript", "", record["source_url"], "",
             f"Model: {record['model']}", f"Status: {record['status']}", ""]
    for item in record["segments"]:
        speaker = f" {item['speaker']}" if "speaker" in item else ""
        lines.append(f"- [{timestamp(item['start'])}–{timestamp(item['end'])}]{speaker} {item['text']}")
    write_atomic(md_path, "\n".join(lines) + "\n")
    write_atomic(json_path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locale", default="en-US", help="Speech language for Apple Speech")
    parser.add_argument("--post-id", help="Transcribe a specific archived post")
    args = parser.parse_args()
    vault = Path(os.environ["X_BOOKMARK_VAULT"]).resolve()
    with (vault / ".capture.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another capture or transcription is running") from None
        return transcribe_vault(vault, args)


def transcribe_vault(vault, args):
    if args.post_id:
        if not re.fullmatch(r"[0-9]{1,19}", args.post_id):
            raise ValueError("Expected an X post ID")
        path = vault / "posts" / args.post_id / "source.json"
        records = [json.loads(path.read_text())]
    else:
        records = json.loads((vault / "latest-run.json").read_text())["posts"]
    selected = [
        (post, media)
        for post in records
        for media in post["media"]
        if media.get("kind") == "video" and media["status"] == "saved"
    ]
    if not selected:
        print("No saved X video in the latest capture.")
        return 0

    engine = AppleSpeech(args.locale)
    print(f"Model: {engine.model}. Diarization: {engine.diarization}. "
          "Speaker mapping: none. Known-speaker references: none. Fallback: none.")
    failed = 0
    for post, media in selected:
        video = (vault / "posts" / post["id"] / media["path"]).resolve()
        try:
            if not video.is_relative_to(vault) or not video.is_file():
                raise RuntimeError("missing video or media path outside vault")
            source = media.get("source_url") or post.get("capture_url") or post["source_url"]
            print(post["id"], media["key"], archive_transcript(video, source, engine, "apple"))
        except (OSError, RuntimeError, KeyError, ValueError, subprocess.TimeoutExpired) as error:
            print(f"{post['id']} {media['key']} failed: {error}", file=sys.stderr)
            failed += 1
    return bool(failed)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, KeyError, ValueError, subprocess.TimeoutExpired) as error:
        sys.exit(str(error))
