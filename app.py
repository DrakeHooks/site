import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from flask import (
    Flask,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────
TITLE_DEFAULT = "Drake Hooks Uploader"

DATA_ROOT = os.environ.get("DATA_ROOT", "/data").rstrip("/")
SITE_ROOT = os.environ.get("SITE_ROOT", "/site").rstrip("/")  # mounted but not used here
ARCHIVE_USER = os.environ.get("ARCHIVE_USER", "drake")
ARCHIVE_PASS = os.environ.get("ARCHIVE_PASS", "")

ARCHIVE_SUBDIR = "archive"  # under DATA_ROOT
NOTE_FILENAME = "note.md"   # in upload staging folder (IMPORTANT)

MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_BYTES", str(2 * 1024 * 1024 * 1024)))  # 2GB
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".avif", ".gif", ".webp", ".heic", ".mov", ".mp4"}

# ─────────────────────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("FLASK_SECRET", secrets.token_hex(16))
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _dbg(msg: str) -> None:
    print(f"[UPLOAD] {msg}", flush=True)


def _today_ui() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9\-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


def _safe_filename(name: str) -> str:
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return name[:200] if name else "file"


def _allowed_ext(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXT


def _require_basic_auth() -> Optional[Response]:
    # If no password is set, allow requests (but you should set one)
    if not ARCHIVE_PASS:
        return None
    auth = request.authorization
    if not auth or auth.username != ARCHIVE_USER or auth.password != ARCHIVE_PASS:
        return Response(
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="DrakeHooks Uploader"'},
        )
    return None


def _get_files_from_request() -> List:
    # support both <input name="files"> and <input name="file">
    files = request.files.getlist("files")
    if not files:
        files = request.files.getlist("file")
    return [f for f in files if f and getattr(f, "filename", None)]


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _make_upload_slug(date_str: str, custom_slug: str) -> str:
    d = (date_str or "").strip() or _today_ui()
    base = d[:10]
    extra = _slugify(custom_slug)
    if extra:
        # if user provides slug, keep it under the date
        if not extra.startswith(base):
            return f"{base}-{extra}"
        return extra
    return base


def _write_note(dest_dir: Path, note: str) -> None:
    note = (note or "").strip()
    if not note:
        return
    (dest_dir / NOTE_FILENAME).write_text(note + "\n", encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────
@app.get("/")
def home():
    auth_resp = _require_basic_auth()
    if auth_resp:
        return auth_resp
    return render_template("index.html", title=TITLE_DEFAULT, default_date=_today_ui())


@app.post("/upload")
def upload():
    auth_resp = _require_basic_auth()
    if auth_resp:
        return auth_resp

    date_str = (request.form.get("date") or "").strip()
    custom_slug = (request.form.get("slug") or "").strip()
    note = (request.form.get("note") or "").strip()

    files = _get_files_from_request()

    # Debug
    _dbg(f"content_type={request.content_type}")
    _dbg(f"form keys={list(request.form.keys())}")
    _dbg(f"files keys={list(request.files.keys())}")
    _dbg(f"files count={len(files)}")
    _dbg("filenames=" + ", ".join([getattr(f, "filename", "") for f in files]))

    # Base slug
    slug = _make_upload_slug(date_str, custom_slug)

    # NOTE-ONLY: make it unique so multiple notes per day always work
    if note and len(files) == 0:
        slug = f"{slug}-note-{secrets.token_hex(2)}"  # e.g. 2025-12-24-note-a3f9

    archive_root = Path(DATA_ROOT) / ARCHIVE_SUBDIR
    dest_dir = archive_root / slug
    _ensure_dir(dest_dir)

    if not files and not note:
        flash("No files or note provided.")
        return redirect(url_for("home"))

    saved = 0
    skipped = 0

    # Save files
    for f in files:
        filename = _safe_filename(f.filename)
        if not filename:
            skipped += 1
            continue
        if not _allowed_ext(filename):
            skipped += 1
            _dbg(f"Skipping disallowed ext: {filename}")
            continue

        out_path = dest_dir / filename
        _dbg(f"saving -> {out_path}")
        f.save(str(out_path))
        saved += 1

    # Save note.md (separate from Hugo index)
    if note:
        _write_note(dest_dir, note)
        _dbg(f"saved note -> {dest_dir / NOTE_FILENAME}")

    # User feedback
    if saved > 0 and note:
        flash(f"Uploaded {saved} file(s) + note to {slug}. It will publish automatically.")
    elif saved > 0:
        flash(f"Uploaded {saved} file(s) to {slug}. It will publish automatically.")
    elif note:
        flash(f"Saved note-only post: {slug}. It will publish automatically.")
    else:
        flash("Nothing saved. Check file types.")

    return redirect(url_for("home"))


@app.get("/health")
def health():
    return {"ok": True, "data_root": DATA_ROOT, "site_root": SITE_ROOT}


@app.get("/debug/staging")
def debug_staging():
    auth_resp = _require_basic_auth()
    if auth_resp:
        return auth_resp

    archive_root = Path(DATA_ROOT) / ARCHIVE_SUBDIR
    if not archive_root.exists():
        abort(404)

    items = []
    for p in sorted(archive_root.glob("*")):
        if p.is_dir():
            items.append(
                {
                    "slug": p.name,
                    "files": sorted([x.name for x in p.glob("*") if x.is_file()])[:200],
                }
            )
    return {"archive_root": str(archive_root), "items": items}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)