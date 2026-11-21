"""Standard-library smoke test for FFmpeg → Hugo bundle → Git push; no Flask needed.
Run with: python3 tests/smoke.py
"""
import json
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path


def cmd(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.STDOUT).strip()


def main():
    with tempfile.TemporaryDirectory(prefix="uploader-v2-smoke-") as td:
        root = Path(td)
        site = root / "site"
        data = root / "data"
        origin = root / "remote.git"
        cmd("git", "init", "--bare", "-b", "main", str(origin))
        cmd("git", "clone", str(origin), str(site))
        cmd("git", "config", "user.name", "Test", cwd=site)
        cmd("git", "config", "user.email", "test@example.com", cwd=site)
        (site / "README.md").write_text("archive\n")
        cmd("git", "add", "README.md", cwd=site)
        cmd("git", "commit", "-m", "Initial", cwd=site)
        cmd("git", "push", "origin", "main", cwd=site)
        os.environ["DATA_DIR"] = str(data)
        os.environ["SITE_DIR"] = str(site)
        os.environ["TZ"] = "America/New_York"
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
        import common
        import worker
        common.setup_dirs()
        job = "0123456789abcdef0123456789abcdef"
        jdir = common.JOBS / job
        incoming = jdir / "incoming"
        incoming.mkdir(parents=True)
        source = incoming / "001_sample.jpg"
        cmd("ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=yellow:s=72x56:d=1",
            "-frames:v", "1", str(source))
        import hashlib
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        manifest = {"id": job, "collection": "archive", "day": "2026-11-21",
                    "no_date": False, "note": "a new photograph", "allow_duplicates": False,
                    "files": [{"stored": source.name, "name": "sample.jpg", "sha256": digest, "bytes": source.stat().st_size}]}
        common.atomic_json(jdir / "manifest.json", manifest)
        common.set_status(job, id=job, state="queued", created_at=common.now().isoformat())
        with worker.registry() as db:
            worker.process(job, db)
        status = common.get_status(job)
        assert status["state"] == "published", status
        assert (site / "content/archive/2026-11-21/sample.avif").is_file()
        assert "a new photograph" in (site / "content/archive/2026-11-21/index.md").read_text()
        assert not incoming.exists(), "Temporary original must be removed on successful publish"
        assert (site / "content/archive/2026-11-21/sample.avif").stat().st_size > 0
        assert cmd("git", "rev-parse", "HEAD", cwd=site) == cmd("git", "rev-parse", "origin/main", cwd=site)
        print("PASS: encoded AVIF, Hugo content, Git push, and source cleanup")
        print("commit:", status["commit"])


if __name__ == "__main__":
    main()
