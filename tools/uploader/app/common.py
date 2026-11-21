"""Shared storage, validation and atomic status primitives."""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DATA = Path(os.environ.get("DATA_DIR", "/data"))
SITE = Path(os.environ.get("SITE_DIR", "/site"))
JOBS = DATA / "uploader-v2" / "jobs"
STATE = DATA / "uploader-v2" / "state"
TZ = ZoneInfo(os.environ.get("TZ", "America/New_York"))
ORIGIN = os.environ.get("PUBLIC_ORIGIN", "https://upload.drakehooks.net").rstrip("/")
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".avif", ".tif", ".tiff"}
VIDEO_EXT = {".mov", ".mp4", ".m4v"}
ALLOWED_EXT = IMAGE_EXT | VIDEO_EXT
MAX_FILE_BYTES = 220 * 1024 * 1024
MAX_OUTPUT_BYTES = 90 * 1024 * 1024  # Leave headroom under GitHub's 100 MB file limit.
MAX_FILES = 30


def now() -> datetime:
    return datetime.now(TZ)


def setup_dirs() -> None:
    JOBS.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)


def atomic_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: Path, default=None):
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def safe_filename(value: str) -> str:
    name = str(value).replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w.()\- ]", "_", name, flags=re.ASCII)
    name = re.sub(r"\s+", "_", name).lstrip(".")[:130]
    if not name or name in {".", ".."}:
        return "photo"
    return name


def valid_job_id(job_id: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{32}", job_id))


def state_path(job_id: str) -> Path:
    return JOBS / job_id / "status.json"


def get_status(job_id: str) -> dict | None:
    if not valid_job_id(job_id):
        return None
    return read_json(state_path(job_id))


def set_status(job_id: str, **changes) -> dict:
    path = state_path(job_id)
    current = read_json(path, {})
    current.update(changes)
    current["updated_at"] = now().isoformat(timespec="seconds")
    atomic_json(path, current)
    return current


def public_job(status: dict) -> dict:
    # Never send filesystem paths, credentials or raw subprocess output to clients.
    keys = ("id", "state", "stage", "progress", "message", "created_at", "updated_at",
            "collection", "date", "file_count", "published_count", "skipped_count", "commit")
    return {k: status[k] for k in keys if k in status}
