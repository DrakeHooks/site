"""Single durable media worker; queues jobs on /data and publishes to Hugo via Git."""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import threading
from datetime import date
from pathlib import Path

from common import (DATA, JOBS, MAX_OUTPUT_BYTES, SITE, STATE, VIDEO_EXT,
                    atomic_json, get_status, now, read_json, safe_filename,
                    set_status, setup_dirs, valid_job_id)

POLL_SECONDS = 3
CRF = max(0, min(63, int(os.getenv("AVIF_CRF", "20"))))


class PublishError(Exception):
    pass


def say(message):
    print(f"[{now().isoformat(timespec='seconds')}] {message}", flush=True)


def run(cmd, *, timeout=900, cwd=None):
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                                timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublishError(f"Process error: {type(exc).__name__}") from exc
    if result.returncode:
        # Do not return raw Git/FFmpeg diagnostics to web clients.
        say(f"Command failed ({result.returncode}): {cmd[0]}: {(result.stderr or result.stdout)[-1200:]}")
        raise PublishError(f"{Path(cmd[0]).name} exited with status {result.returncode}; inspect worker logs")
    return result.stdout.strip()


def git(*args):
    return run(["git", "-C", str(SITE), *args], timeout=150)


def ensure_repo_clean():
    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        raise PublishError("Repository checkout must be on main")
    if git("status", "--porcelain"):
        raise PublishError("Repository has uncommitted changes; resolve them before publishing")
    git("fetch", "origin", "main")
    git("merge", "--ff-only", "origin/main")
    if git("rev-parse", "HEAD") != git("rev-parse", "origin/main"):
        raise PublishError("Repository has unpushed local commits; resolve them before publishing")


def registry():
    conn = sqlite3.connect(STATE / "published.sqlite", timeout=15)
    conn.execute("""CREATE TABLE IF NOT EXISTS published (
        sha256 TEXT NOT NULL, collection TEXT NOT NULL, slug TEXT NOT NULL,
        filename TEXT NOT NULL, published_at TEXT NOT NULL,
        PRIMARY KEY (sha256, collection)
    )""")
    conn.commit()
    return conn


def is_duplicate(conn, digest, collection):
    return conn.execute("SELECT 1 FROM published WHERE sha256=? AND collection=?",
                        (digest, collection)).fetchone() is not None


def record_published(conn, manifest, slug, outputs):
    for entry in outputs:
        conn.execute("""INSERT OR IGNORE INTO published
                     (sha256, collection, slug, filename, published_at)
                     VALUES (?, ?, ?, ?, ?)""",
                     (entry["sha256"], manifest["collection"], slug,
                      entry["name"], now().isoformat(timespec="seconds")))
    conn.commit()


def probe(path: Path, codec=None):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=codec_name,width,height", "-of", "json", str(path)], timeout=120)
    try:
        streams = json.loads(out).get("streams", [])
        valid = bool(streams and int(streams[0]["width"]) > 0 and int(streams[0]["height"]) > 0)
        if codec:
            valid = valid and streams[0].get("codec_name") == codec
        if not valid:
            raise ValueError("Video stream missing or unexpected codec")
    except (ValueError, KeyError, TypeError) as exc:
        raise PublishError("Converted file failed media validation") from exc
    if not path.stat().st_size or path.stat().st_size >= MAX_OUTPUT_BYTES:
        raise PublishError("Converted file empty or too large for GitHub; try a smaller input")


def encode(src: Path, dest: Path, video: bool):
    if video:
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
               "-c:v", "libx264", "-preset", "medium", "-crf", "22",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
               "-movflags", "+faststart", str(dest)]
    elif src.suffix.lower() == ".avif":
        shutil.copyfile(src, dest)
        probe(dest, "av1")
        return
    else:
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(src), "-map", "0:v:0", "-frames:v", "1",
               "-c:v", "libaom-av1", "-crf", str(CRF), "-b:v", "0",
               "-cpu-used", "4", "-threads", "4", "-pix_fmt", "yuv420p", str(dest)]
    run(cmd, timeout=1800 if video else 1200)
    probe(dest, "h264" if video else "av1")
    # Detect broken AVIF payloads, not just nonempty files/headers.
    if not video:
        run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(dest),
             "-frames:v", "1", "-f", "null", "-"], timeout=180)


def unique_name(filename: str, used: set[str], folder: Path):
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", Path(filename).stem)[:85].strip("_") or "photo"
    ext = Path(filename).suffix.lower()
    candidate = f"{stem}{ext}"
    i = 2
    while candidate.casefold() in used or (folder / candidate).exists():
        candidate = f"{stem}_{i}{ext}"
        i += 1
    used.add(candidate.casefold())
    return candidate


def figure_shortcode(name: str, video: bool):
    if video:
        # Site has unsafe Goldmark enabled; video remains inline for browsers.
        return (f'<video controls playsinline preload="metadata" style="max-width:100%;height:auto">'
                f'<source src="{name}" type="video/mp4"></video>')
    return '{{< figure src="' + name + '" alt="post" >}}'


def append_content(existing: str, manifest: dict, outputs: list[dict]):
    marker = f"<!-- uploader:{manifest['id']} -->"
    if marker in existing:
        return existing
    day = date.fromisoformat(manifest["day"])
    if not existing.strip():
        existing = ("---\n" +
                    f'title: "{day.month:02d} {day.day} {day.year}"\n' +
                    f"date: {day.isoformat()}\n" +
                    "draft: false\n---\n")
    parts = [marker]
    if manifest["note"]:
        parts.append(manifest["note"])
    for output in outputs:
        parts.append(figure_shortcode(output["name"], output["video"]))
    return existing.rstrip() + "\n\n" + "\n".join(parts) + "\n"


def copy_atomic(src: Path, dest: Path):
    temp = dest.with_name(f".{dest.name}.upload-part")
    try:
        with src.open("rb") as a, temp.open("xb") as b:
            shutil.copyfileobj(a, b, 1024 * 1024)
            b.flush()
            os.fsync(b.fileno())
        os.replace(temp, dest)
    finally:
        temp.unlink(missing_ok=True)


def cleanup_job_media(job_id):
    jdir = JOBS / job_id
    shutil.rmtree(jdir / "incoming", ignore_errors=True)
    shutil.rmtree(jdir / "converted", ignore_errors=True)


def finish_pending_push(job_id, status, manifest, conn):
    git("push", "origin", "main")
    outputs = read_json(JOBS / job_id / "outputs.json", [])
    slug = status.get("slug", "")
    if slug:
        record_published(conn, manifest, slug, outputs)
    cleanup_job_media(job_id)
    set_status(job_id, state="published", stage="done", progress=100,
               published_count=len(outputs), message="Published to GitHub; Pages build can now run")
    say(f"Published job {job_id} (commit {status.get('commit')})")


def process(job_id, conn):
    jdir = JOBS / job_id
    manifest = read_json(jdir / "manifest.json")
    if not manifest:
        raise PublishError("Job manifest is missing")
    status = get_status(job_id) or {}
    if status.get("state") == "pending_push":
        finish_pending_push(job_id, status, manifest, conn)
        return
    # The worker may have crashed between a successful Git commit and the
    # status-file update. Detect that commit before doing any re-encoding.
    try:
        last_message = git("log", "-1", "--format=%B")
        if f"[uploader:{job_id}]" in last_message:
            commit = git("rev-parse", "HEAD")
            set_status(job_id, state="pending_push", stage="pushing", progress=94,
                       commit=commit, message="Recovering committed upload")
            finish_pending_push(job_id, get_status(job_id), manifest, conn)
            return
    except PublishError:
        pass
    if not (jdir / "incoming").is_dir():
        raise PublishError("Upload files missing; cannot retry conversion")

    set_status(job_id, state="processing", stage="checking repository", progress=3,
               message="Checking archive checkout")
    ensure_repo_clean()

    collection = manifest["collection"]
    day = manifest["day"]
    slug = f"{day}-no-date-{job_id[:8]}" if manifest["no_date"] else day
    folder = SITE / "content" / collection / slug
    if not folder.resolve().is_relative_to((SITE / "content" / collection).resolve()):
        raise PublishError("Invalid destination")
    converted = jdir / "converted"
    if converted.exists():
        shutil.rmtree(converted)
    converted.mkdir(mode=0o700)
    seen = set()
    if folder.exists():
        seen.update(p.name.casefold() for p in folder.iterdir())
    outputs = []
    skipped = 0
    total = len(manifest["files"])

    for i, info in enumerate(manifest["files"]):
        if not manifest["allow_duplicates"] and is_duplicate(conn, info["sha256"], collection):
            skipped += 1
            continue
        src = jdir / "incoming" / info["stored"]
        if not src.is_file():
            raise PublishError(f"Missing temporary input: {info['name']}")
        video = src.suffix.lower() in VIDEO_EXT
        suffix = ".mp4" if video else ".avif"
        name = unique_name(Path(info["name"]).stem + suffix, seen, folder)
        target = converted / name
        set_status(job_id, stage="encoding", progress=5 + round(65 * i / max(1, total)),
                   message=f"Converting {i+1} of {total}: {info['name']}")
        encode(src, target, video)
        outputs.append({"sha256": info["sha256"], "name": name, "video": video})

    set_status(job_id, stage="publishing", progress=75, message="Preparing Hugo content")
    if not outputs and not manifest["note"]:
        cleanup_job_media(job_id)
        set_status(job_id, state="published", stage="done", progress=100,
                   published_count=0, skipped_count=skipped,
                   message="No changes: all files were previously published")
        return
    folder.mkdir(parents=True, exist_ok=True)
    for output in outputs:
        copy_atomic(converted / output["name"], folder / output["name"])
    md = folder / "index.md"
    current = md.read_text(encoding="utf-8") if md.exists() else ""
    updated = append_content(current, manifest, outputs)
    if updated != current:
        tmp = folder / ".index.md.upload-part"
        tmp.write_text(updated, encoding="utf-8")
        os.replace(tmp, md)

    paths = [str((folder / o["name"]).relative_to(SITE)) for o in outputs]
    paths.append(str(md.relative_to(SITE)))
    git("add", "--", *paths)
    if not git("diff", "--cached", "--name-only"):
        cleanup_job_media(job_id)
        set_status(job_id, state="published", stage="done", progress=100,
                   message="No new changes")
        return
    git("-c", "user.name=Drake Uploader", "-c", "user.email=uploader@users.noreply.github.com",
        "commit", "-m", f"Upload {collection}/{slug} [uploader:{job_id}]")
    commit = git("rev-parse", "HEAD")
    atomic_json(jdir / "outputs.json", outputs)
    # No permanent originals or duplicate processed media in /data.
    # The new Git commit now holds the AVIFs/MP4s until it can be pushed.
    cleanup_job_media(job_id)
    set_status(job_id, state="pending_push", stage="pushing", progress=94,
               slug=slug, commit=commit, skipped_count=skipped,
               message="Commit created; pushing to GitHub")
    finish_pending_push(job_id, get_status(job_id), manifest, conn)


def cleanup_old():
    """Don't retain failed upload originals indefinitely."""
    cutoff = time.time() - 7 * 86400
    for p in JOBS.iterdir():
        if not p.is_dir() or not valid_job_id(p.name):
            continue
        s = get_status(p.name) or {}
        if s.get("state") in {"failed", "published"} and p.stat().st_mtime < cutoff:
            cleanup_job_media(p.name)
            if s.get("state") == "failed":
                set_status(p.name, message="Failed job expired; temporary inputs deleted")


def work_forever():
    setup_dirs()
    lock = (STATE / "worker.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        say("Another worker holds the lock; exiting")
        sys.exit(1)
    say(f"Uploader v2 worker started (AVIF CRF {CRF})")
    # Long AVIF encodes can run for several minutes. Maintain a live heartbeat
    # independently of job processing so health never reports a false outage.
    def heartbeat():
        while True:
            try:
                (STATE / "worker-heartbeat").write_text(str(time.time()))
            except OSError as e:
                say(f"Heartbeat write failed: {e}")
            time.sleep(12)
    threading.Thread(target=heartbeat, daemon=True).start()
    with registry() as conn:
        last_cleanup = 0
        while True:
            if time.time() - last_cleanup > 3600:
                cleanup_old()
                last_cleanup = time.time()
            candidates = []
            for p in JOBS.iterdir():
                if not p.is_dir() or not valid_job_id(p.name):
                    continue
                s = get_status(p.name) or {}
                if s.get("state") in {"queued", "pending_push", "processing"}:
                    candidates.append((s.get("created_at", ""), p.name))
            for _, job_id in sorted(candidates):
                s = get_status(job_id) or {}
                if s.get("state") == "processing":
                    # Crash recovery: a locally committed upload must never be encoded twice.
                    if s.get("commit"):
                        set_status(job_id, state="pending_push")
                    else:
                        set_status(job_id, state="queued")
                try:
                    process(job_id, conn)
                except Exception as e:
                    say(f"Job {job_id} failed: {e}")
                    current = get_status(job_id) or {}
                    state = "pending_push" if current.get("commit") else "failed"
                    set_status(job_id, state=state, stage="error",
                               message=str(e)[:250])
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    work_forever()
