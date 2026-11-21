"""Authenticated upload API and deliberately minimal responsive interface."""
from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, Response
from werkzeug.exceptions import RequestEntityTooLarge

from common import (ALLOWED_EXT, DATA, JOBS, MAX_FILE_BYTES, MAX_FILES, ORIGIN,
                    SITE, STATE, TZ, atomic_json, get_status, now, public_job,
                    read_json, safe_filename, set_status, setup_dirs, valid_job_id)

app = Flask(__name__)
app.config.update(MAX_CONTENT_LENGTH=512 * 1024 * 1024, MAX_FORM_MEMORY_SIZE=128 * 1024)
setup_dirs()


def credentials():
    user = os.getenv("UPLOAD_USER") or os.getenv("ARCHIVE_USER") or ""
    password = os.getenv("UPLOAD_PASS") or os.getenv("ARCHIVE_PASS") or ""
    return user, password


@app.before_request
def authorization():
    if request.path == "/health":
        return None
    user, password = credentials()
    if not user or not password:
        return jsonify(error="Uploader authentication is not configured"), 503
    supplied = request.authorization
    if (not supplied or supplied.type.lower() != "basic" or
            not hmac.compare_digest(supplied.username or "", user) or
            not hmac.compare_digest(supplied.password or "", password)):
        return Response("Authentication required", 401,
                        {"WWW-Authenticate": 'Basic realm="archive uploader", charset="UTF-8"'})
    if request.method in ("POST", "PUT", "DELETE"):
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != ORIGIN:
            return jsonify(error="Invalid request origin"), 403


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; "
        "script-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    return response


def component_status():
    heartbeat = STATE / "worker-heartbeat"
    try:
        worker_age = now().timestamp() - float(heartbeat.read_text().strip())
        worker_ok = 0 <= worker_age < 100
    except (OSError, ValueError):
        worker_ok = False
    return {
        "web": True,
        "ingest": worker_ok,
        "data": os.access(DATA, os.W_OK),
        "repo": (SITE / ".git").exists() and os.access(SITE, os.W_OK),
    }


@app.get("/health")
def health():
    status = component_status()
    return jsonify(ok=all(status.values()), components=status, version="2.0.0")


@app.get("/")
def index():
    d = now()
    return render_template("index.html", default_date=f"{d:%m %d %Y}")


@app.get("/api/status")
def api_status():
    return jsonify(components=component_status())


@app.get("/api/jobs")
def jobs():
    recent = []
    for p in JOBS.iterdir():
        if p.is_dir() and valid_job_id(p.name):
            s = get_status(p.name)
            if s:
                recent.append(public_job(s))
    recent.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify(jobs=recent[:8])


@app.get("/api/jobs/<job_id>")
def api_job(job_id):
    s = get_status(job_id)
    if not s:
        return jsonify(error="Job not found"), 404
    return jsonify(public_job(s))


@app.post("/api/jobs/<job_id>/retry")
def retry_job(job_id):
    s = get_status(job_id)
    if not s:
        return jsonify(error="Job not found"), 404
    if s.get("state") not in {"failed", "pending_push"}:
        return jsonify(error="Job cannot be retried in this state"), 409
    jdir = JOBS / job_id
    manifest = read_json(jdir / "manifest.json")
    if not manifest:
        return jsonify(error="Job manifest missing"), 409
    if s.get("state") == "failed" and not (jdir / "incoming").exists():
        return jsonify(error="Original upload is no longer available"), 409
    new_state = "pending_push" if s.get("commit") else "queued"
    return jsonify(public_job(set_status(job_id, state=new_state, stage="waiting", message="Retry scheduled")))


@app.errorhandler(RequestEntityTooLarge)
def too_large(exc):
    return jsonify(error="Upload exceeds 512 MB request limit"), 413


@app.post("/api/jobs")
@app.post("/upload")
def upload():
    collection = request.form.get("collection") or request.form.get("destination", "archive")
    if collection not in {"archive", "birds"}:
        return jsonify(error="Choose archive or birds"), 400

    note = request.form.get("note", "").replace("\r\n", "\n").strip()
    if len(note) > 12000:
        return jsonify(error="Note is too long (12,000 characters max)"), 400

    no_date = request.form.get("no_date", "").lower() in {"true", "on", "1", "yes"}
    date_input = request.form.get("date", "").strip()
    if no_date:
        day = now().date()
    else:
        try:
            day = datetime.strptime(date_input, "%m %d %Y").date()
        except ValueError:
            return jsonify(error="Use date format MM DD YYYY"), 400
        if day.year < 1900 or day.year > 2100:
            return jsonify(error="Date is outside the supported range"), 400

    files = [f for f in request.files.getlist("files") if f.filename]
    if len(files) > MAX_FILES:
        return jsonify(error=f"Maximum {MAX_FILES} files per upload"), 400
    if not files and not note:
        return jsonify(error="Choose files or write a note"), 400
    for f in files:
        if Path(f.filename).suffix.lower() not in ALLOWED_EXT:
            return jsonify(error=f"Unsupported file type: {safe_filename(f.filename)}"), 400

    job_id = uuid.uuid4().hex
    jdir = JOBS / job_id
    incoming = jdir / "incoming"
    incoming.mkdir(parents=True, mode=0o700)
    manifest_files = []
    seen = set()
    try:
        for idx, file in enumerate(files):
            safe = safe_filename(file.filename)
            name = f"{idx+1:03d}_{safe}"
            path = incoming / name
            sha = hashlib.sha256()
            count = 0
            with path.open("xb") as target:
                while True:
                    chunk = file.stream.read(1024 * 1024)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > MAX_FILE_BYTES:
                        raise ValueError(f"{safe} exceeds 220 MB")
                    sha.update(chunk)
                    target.write(chunk)
            if count == 0:
                raise ValueError(f"{safe} is empty")
            digest = sha.hexdigest()
            if digest in seen:
                path.unlink()
                continue
            seen.add(digest)
            manifest_files.append({"stored": name, "name": safe, "sha256": digest, "bytes": count})

        if not manifest_files and not note:
            raise ValueError("All files were duplicates within this upload")

        manifest = {
            "id": job_id, "collection": collection, "day": day.isoformat(),
            "no_date": no_date, "note": note, "files": manifest_files,
            "allow_duplicates": request.form.get("allow_duplicates", "").lower() in {"true", "on", "1"},
            "created_at": now().isoformat(timespec="seconds")
        }
        atomic_json(jdir / "manifest.json", manifest)
        set_status(job_id, id=job_id, state="queued", stage="queued", progress=0,
                   message="Waiting to process", created_at=manifest["created_at"],
                   collection=collection, date=day.isoformat(), file_count=len(manifest_files))
    except (ValueError, OSError) as e:
        shutil.rmtree(jdir, ignore_errors=True)
        return jsonify(error=str(e)), 400
    return jsonify(id=job_id, state="queued", message="Upload received"), 202


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
