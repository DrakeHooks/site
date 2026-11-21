# Drake archive uploader v2

A minimal personal publishing interface for the Hugo repository `DrakeHooks/site`. It intentionally lives in `tools/uploader/` rather than `content/`, and it contains **no credentials or originals**.

## Features

- Unfolded/folded iPhone Duo, desktop and tablet layout; compact controls, accessible 16px text fields, keyboard navigation, drag-and-drop and light/dark appearance.
- Basic authentication (existing `UPLOAD_USER` / `UPLOAD_PASS`, or legacy `ARCHIVE_USER` / `ARCHIVE_PASS`), explicit same-origin writes, safe file names, and per-file/request limits.
- Separate, single-instance worker and Gunicorn web server; jobs persisted under `/data/uploader-v2/jobs/` and recovered across restarts.
- FFmpeg AV1 AVIF at CRF `20`, no downscaling by default. Existing AVIFs validated and copied, MOV/MP4 transcoded to H.264/AAC MP4.
- Appends to Hugo page bundles `content/archive/YYYY-MM-DD/index.md` and `content/birds/YYYY-MM-DD/index.md`, preserving existing notes/figure shortcodes.
- SHA-256 duplicate identification **for uploads made through v2** (doesn't scan historic Git media). Optional duplicate override in UI.
- Git checkout cleanliness checks, fast-forward-only synchronization, commit and push, retryable push failures, per-job progress and recent job status.
- **No permanent source JPEG/PNG files.** Raw source uploads exist only in the temporary queue while converting. After conversion and commit, both raw and converted queue copies are removed. Failed uploads keep temporary sources for manual retry, expiring after 7 days. Website media live in Git.

## Deployment

Use `publish-and-deploy.sh` at the root of the downloadable release ZIP to publish the source into the site's existing `main` checkout and recreate the Compose services. See `INSTALL.md` in the ZIP. Real auth values remain in the existing private `/opt/drakehooks/uploader/.env`.

Compose uses the same old port `8011`, storage `/mnt/pve/mnt/media/drakehooks-uploads:/data`, Git checkout `/opt/drakehooks/archive-site:/site`, SSH mount `/root/.ssh:/root/.ssh:ro`, and DNS `upload.drakehooks.net`. No tunnel changes should be necessary.

## Endpoints

- `GET /` private uploader UI
- `GET /health` public limited health status
- `GET /api/status` authenticated health indicators
- `GET /api/jobs` authenticated recent jobs
- `POST /api/jobs` authenticated multipart upload with `collection`, `date` (`MM DD YYYY`), `no_date`, `note`, `files[]`, and `allow_duplicates`
- `GET /api/jobs/<id>` authenticated job state
- `POST /api/jobs/<id>/retry` authenticated retry of failed/unpushed job

## Production notes

- Use HTTPS at Cloudflare/Nginx; Basic Auth credentials must never travel over plain public HTTP.
- `PUBLIC_ORIGIN` checks the browser's Origin on state-changing requests. Update it if you change the public uploader hostname.
- Preserve the existing `.env` and SSH key mount. The public GitHub repository must never contain either.
- `ffmpeg` uses 8-bit 4:2:0 AVIF output to maximize Safari compatibility; alpha transparency, unusual HDR profiles, and certain DNG RAW camera files aren't guaranteed. Export DNGs to JPEG before uploading.
- A single image output must be less than 90 MB, leaving headroom under GitHub's file limit. Video is supported only within the input/output size caps; large or long MOV clips may not fit GitHub.
- Successful Git push is not the same as successful GitHub Pages deployment; confirm the Actions workflow separately.
- Publishing refuses dirty, diverged, or ahead-of-remote Git checkouts rather than risking a reset or lost posts.
- Existing legacy staging jobs are **not** migrated automatically. The installer stops if it finds legacy staged files.
- The UI is private; any user with the shared Basic Auth password can view job history and upload. If accessible directly from the Internet, consider an additional Cloudflare Access policy.

## Operational commands

```sh
cd /opt/drakehooks/archive-site/tools/uploader
docker compose ps
docker logs --tail 100 drake-uploader
docker logs --tail 100 drake-uploader-worker
wget -qO- http://127.0.0.1:8011/health
```

## Smoke test

Run `python3 tests/smoke.py` on a machine with FFmpeg (including libaom), Git, and Python 3.12+. It creates a temporary bare Git repository, encodes a generated photo, writes a Hugo page, pushes to the test remote, and asserts that raw inputs were deleted. It does not touch the real site.
