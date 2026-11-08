"""MP3 / MP4 file converter.

Users upload their own audio/video file, pick MP3 or MP4, and download the result.
Conversion is done with ffmpeg. Nothing is stored after the response is sent.
"""
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections import defaultdict, deque

from flask import (
    Flask, Response, abort, jsonify, render_template, request,
)

# ---------- configuration (override with environment variables) ----------
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "100"))
FFMPEG_TIMEOUT = int(os.environ.get("FFMPEG_TIMEOUT", "300"))      # seconds per job
MAX_PARALLEL_JOBS = int(os.environ.get("MAX_PARALLEL_JOBS", "2"))  # protects your server CPU
RATE_LIMIT_JOBS = int(os.environ.get("RATE_LIMIT_JOBS", "15"))     # per IP ...
RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", "3600"))  # ... per this many seconds
SITE_NAME = os.environ.get("SITE_NAME", "QuickConvert")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "you@example.com")
ADSENSE_CLIENT = os.environ.get("ADSENSE_CLIENT", "")   # e.g. ca-pub-1234567890123456
ADSENSE_SLOT_TOP = os.environ.get("ADSENSE_SLOT_TOP", "")
ADSENSE_SLOT_BOTTOM = os.environ.get("ADSENSE_SLOT_BOTTOM", "")
TRUST_PROXY = os.environ.get("TRUST_PROXY", "0") == "1"  # set to 1 behind nginx/Cloudflare/Render etc.

ALLOWED_INPUT_EXT = {
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wma", ".aiff", ".amr",
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv", ".m4v", ".3gp", ".mpg", ".mpeg", ".ts",
}
TARGETS = {"mp3", "mp4"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

if TRUST_PROXY:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

_job_slots = threading.BoundedSemaphore(MAX_PARALLEL_JOBS)
_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def _rate_limited(ip: str) -> bool:
    now = time.time()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > RATE_LIMIT_WINDOW:
            q.popleft()
        if len(q) >= RATE_LIMIT_JOBS:
            return True
        q.append(now)
        return False


class _CleanupFile:
    """Iterable that streams a file and runs `cleanup` exactly once when closed.

    WSGI servers call close() when the response finishes OR the client drops, so the
    temp directory and the job slot are always released.
    """

    def __init__(self, path: str, cleanup):
        self._f = open(path, "rb")
        self._cleanup = cleanup
        self._closed = False

    def __iter__(self):
        return self

    def __next__(self):
        chunk = self._f.read(64 * 1024)
        if not chunk:
            raise StopIteration
        return chunk

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._f.close()
        finally:
            self._cleanup()


def _probe_streams(path: str) -> set:
    """Return the set of stream types ('audio', 'video') found in the file."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return set()
    return {line.strip() for line in out.splitlines() if line.strip() in ("audio", "video")}


def _ffmpeg_cmd(src: str, dst: str, target: str, quality: str, streams: set) -> list:
    base = ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-i", src]
    if target == "mp3":
        kbps = {"128": "128k", "192": "192k", "320": "320k"}.get(quality, "192k")
        return base + ["-vn", "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", kbps, dst]
    # mp4: H.264 + AAC, plays everywhere
    crf = {"low": "28", "medium": "23", "high": "19"}.get(quality, "23")
    cmd = base + [
        "-map", "0:v:0",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", crf, "-pix_fmt", "yuv420p",
    ]
    if "audio" in streams:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "160k"]
    return cmd + ["-movflags", "+faststart", dst]


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return resp


@app.route("/")
def index():
    return render_template(
        "index.html", site_name=SITE_NAME, max_mb=MAX_UPLOAD_MB,
        adsense_client=ADSENSE_CLIENT, slot_top=ADSENSE_SLOT_TOP, slot_bottom=ADSENSE_SLOT_BOTTOM,
    )


@app.route("/privacy")
def privacy():
    return render_template("privacy.html", site_name=SITE_NAME, email=CONTACT_EMAIL,
                           adsense=bool(ADSENSE_CLIENT))


@app.route("/terms")
def terms():
    return render_template("terms.html", site_name=SITE_NAME, email=CONTACT_EMAIL, max_mb=MAX_UPLOAD_MB)


@app.route("/healthz")
def healthz():
    return jsonify(ok=True, ffmpeg=bool(shutil.which("ffmpeg")))


@app.route("/ads.txt")
def ads_txt():
    # AdSense wants this file at the site root. Publisher id looks like ca-pub-123 -> pub-123
    if not ADSENSE_CLIENT:
        abort(404)
    pub = ADSENSE_CLIENT.replace("ca-", "", 1)
    return f"google.com, {pub}, DIRECT, f08c47fec0942fa0\n", 200, {"Content-Type": "text/plain"}


@app.errorhandler(413)
def too_large(_e):
    return jsonify(error=f"File is too large. The limit is {MAX_UPLOAD_MB} MB."), 413


@app.route("/convert", methods=["POST"])
def convert():
    if _rate_limited(request.remote_addr or "unknown"):
        return jsonify(error="Too many conversions from your connection. Please try again later."), 429

    f = request.files.get("file")
    target = (request.form.get("target") or "").lower()
    quality = (request.form.get("quality") or "").lower()
    if not f or not f.filename:
        return jsonify(error="No file uploaded."), 400
    if target not in TARGETS:
        return jsonify(error="Choose MP3 or MP4."), 400

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_INPUT_EXT:
        return jsonify(error="Unsupported file type."), 400

    if not _job_slots.acquire(blocking=False):
        return jsonify(error="Server is busy. Please try again in a minute."), 503

    workdir = tempfile.mkdtemp(prefix="conv_")
    try:
        src = os.path.join(workdir, f"in_{uuid.uuid4().hex}{ext}")
        dst = os.path.join(workdir, f"out_{uuid.uuid4().hex}.{target}")
        f.save(src)

        streams = _probe_streams(src)
        if not streams:
            raise ValueError("That file doesn't look like valid audio or video.")
        if target == "mp3" and "audio" not in streams:
            raise ValueError("This file has no audio track to convert to MP3.")
        if target == "mp4" and "video" not in streams:
            raise ValueError("MP4 needs a video file. This file only contains audio.")

        try:
            proc = subprocess.run(
                _ffmpeg_cmd(src, dst, target, quality, streams),
                capture_output=True, text=True, timeout=FFMPEG_TIMEOUT, check=False,
            )
        except subprocess.TimeoutExpired:
            raise ValueError("Conversion took too long. Try a shorter or smaller file.")
        if proc.returncode != 0 or not os.path.exists(dst):
            app.logger.warning("ffmpeg failed: %s", proc.stderr[-500:])
            raise ValueError("Conversion failed. The file may be corrupted or unsupported.")

        base = os.path.splitext(os.path.basename(f.filename))[0][:80] or "converted"
        safe = "".join(c for c in base if c.isalnum() or c in " -_.()") .strip() or "converted"
        # Delete temp files + free the job slot once the response is finished or the
        # client disconnects. (send_file bypasses call_on_close, so we stream ourselves.)
        def _cleanup():
            shutil.rmtree(workdir, ignore_errors=True)
            _job_slots.release()

        resp = Response(
            _CleanupFile(dst, _cleanup),
            mimetype="audio/mpeg" if target == "mp3" else "video/mp4",
            direct_passthrough=False,
        )
        resp.headers.set("Content-Disposition", "attachment", filename=f"{safe}.{target}")
        resp.headers["Content-Length"] = str(os.path.getsize(dst))
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except ValueError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        _job_slots.release()
        return jsonify(error=str(e)), 422
    except Exception:
        app.logger.exception("unexpected error")
        shutil.rmtree(workdir, ignore_errors=True)
        _job_slots.release()
        return jsonify(error="Something went wrong on our side."), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=False)
