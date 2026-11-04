"""
Fetchly website, production-ready.
Local test:  python app.py
Production:  gunicorn -c gunicorn.conf.py app:app   (see Dockerfile and docker-compose.yml)

Settings (environment variables, all optional):
  ACCESS_CODE    passcode for the whole site (browser asks for it; username can be anything)
  ALLOWED_HOSTS  comma list of sites accepted, or * for any public site (default: the big four)
  MAX_JOBS       downloads running at the same time (default 3)
  PER_IP         downloads one visitor may run at once (default 2)
  MAX_MB         biggest file saved, in MB (default 1000)
  COOKIES_FILE   path to an exported cookies.txt for sites that want a login
"""

import hmac
import ipaddress
import os
import shutil
import socket
import tempfile
import threading
import time
import traceback
import uuid
from urllib.parse import urlparse

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_file
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.wsgi import ClosingIterator

from fetchly_core import FRIENDLY, MAX_MB, MAX_UPLOAD, OVERLAY_POSITIONS, OVERLAY_THEMES, analyze, fetch, overlay

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD + 256 * 1024   # only the photo upload may be big (see small_bodies below)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # trust one proxy (Caddy): real IP and https
limiter = Limiter(get_remote_address, app=app, default_limits=["300 per hour"], storage_uri="memory://")

# Work in the system temp folder: OneDrive and antivirus can lock folders elsewhere
BASE = os.path.join(tempfile.gettempdir(), "fetchly_downloads")
os.makedirs(BASE, exist_ok=True)

MAX_JOBS = int(os.environ.get("MAX_JOBS", "3"))  # downloads running at the same time
PER_IP = int(os.environ.get("PER_IP", "2"))      # ...of which one visitor may use this many
KEEP_SECONDS = 3600                               # unused options and files are dropped after an hour
KEEP_AFTER_DOWNLOAD = 600                         # a file stays this long after it was fully sent, so a retry still works
MAX_CACHE = 500                                   # analysed links kept in memory at once
ACCESS_CODE = os.environ.get("ACCESS_CODE", "")

DEFAULT_HOSTS = "youtube.com,youtu.be,x.com,twitter.com,tiktok.com,instagram.com,instagr.am"
_hosts = os.environ.get("ALLOWED_HOSTS", "").strip().lower() or DEFAULT_HOSTS
ALLOW_ANY_SITE = _hosts == "*"
ALLOWED_HOSTS = [] if ALLOW_ANY_SITE else [h.strip() for h in _hosts.split(",") if h.strip()]

slots = threading.BoundedSemaphore(MAX_JOBS)
active_lock = threading.Lock()
ACTIVE = {}  # visitor IP -> downloads running now
CACHE = {}   # token -> analysed link
JOBS = {}    # job id -> {state, percent, stage, file, error, at}

SORRY = ("We couldn’t get that one. It may be private, removed, or need a login "
         "(Instagram stories do). Make sure it opens in your browser, then try again.")


def url_allowed(url):
    """Only public pages on allowed sites: blocks localhost, private networks and odd ports (SSRF)."""
    if len(url) > 2000:
        return False
    parts = urlparse(url)
    try:
        port = parts.port
    except ValueError:
        return False
    if parts.username or parts.password or port not in (None, 80, 443):
        return False
    host = (parts.hostname or "").lower()
    if not host:
        return False
    if not ALLOW_ANY_SITE and not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
        return False
    try:
        return all(ipaddress.ip_address(i[4][0]).is_global for i in socket.getaddrinfo(host, None))
    except (socket.gaierror, ValueError):
        return False


def forget(job_id):
    """Remove a finished job and its files."""
    job = JOBS.pop(job_id, None)
    shutil.rmtree(os.path.join(BASE, job_id), ignore_errors=True)
    if job and job.get("file"):
        try:
            os.remove(job["file"])                     # e.g. a ZIP stored beside the job folder
        except OSError:
            pass


def sweep():
    """Delete expired options and leftover files so the disk and memory never fill up."""
    now = time.time()
    for token, d in list(CACHE.items()):
        if now - d["at"] > KEEP_SECONDS:
            CACHE.pop(token, None)
    for job, j in list(JOBS.items()):
        age = now - j["at"]
        finished = j["state"] in ("done", "error")
        lost = j["state"] == "done" and not os.path.exists(j["file"])
        sent = j.get("sent_at") and now - j["sent_at"] > KEEP_AFTER_DOWNLOAD     # downloaded in full, grace time over
        if sent:
            forget(job)
        elif lost or (finished and age > KEEP_SECONDS) or age > 2 * KEEP_SECONDS:
            JOBS.pop(job, None)
    for name in os.listdir(BASE):
        path = os.path.join(BASE, name)
        try:
            if now - os.path.getmtime(path) > KEEP_SECONDS:
                shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else os.remove(path)
        except OSError:
            pass


def sweeper():
    while True:
        time.sleep(120)  # every two minutes, even when nobody is visiting
        try:
            sweep()
        except Exception:
            traceback.print_exc()


threading.Thread(target=sweeper, daemon=True).start()


def remember(result, url):
    """Store an analysed link, dropping the oldest ones if the memory cap is reached."""
    if len(CACHE) >= MAX_CACHE:
        oldest = sorted(CACHE.items(), key=lambda kv: kv[1]["at"])[: len(CACHE) - MAX_CACHE + 1]
        for token, _ in oldest:
            CACHE.pop(token, None)
    token = uuid.uuid4().hex[:12]
    CACHE[token] = {**result, "url": url, "at": time.time()}
    return token


@app.before_request
def small_bodies():
    """Every request except the photo upload is a few bytes of JSON: refuse anything bigger."""
    if request.method == "POST" and request.path != "/api/overlay" and (request.content_length or 0) > 16 * 1024:
        abort(413)


@app.errorhandler(413)
def too_big(_):
    message = "That photo is bigger than 15 MB." if request.path == "/api/overlay" else "That request is too large."
    return jsonify(ok=False, error=message), 413


@app.before_request
def gate():
    """Optional passcode for the whole site (HTTP Basic: the browser shows its own login box)."""
    if not ACCESS_CODE or request.path == "/healthz":
        return None
    auth = request.authorization
    given = (getattr(auth, "password", None) or "").encode()
    if auth and hmac.compare_digest(given, ACCESS_CODE.encode()):
        return None
    return Response("Passcode needed.", 401, {"WWW-Authenticate": 'Basic realm="Fetchly"'})


@app.after_request
def secure(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(404)
def not_found(_):
    if request.path.startswith("/file/"):
        return Response("This download link has expired. Go back to Fetchly and make it again.", 404, {"Content-Type": "text/plain; charset=utf-8"})
    return Response("Page not found.", 404, {"Content-Type": "text/plain; charset=utf-8"})


@app.errorhandler(429)
def too_many(_):
    return jsonify(ok=False, error="Too many requests. Please wait a minute and try again."), 429


@app.get("/healthz")
@limiter.exempt
def healthz():
    return "ok"


@app.route("/")
def home():
    return render_template("index.html")


@app.post("/api/analyze")
@limiter.limit("12 per minute")
def api_analyze():
    url = ((request.get_json(silent=True) or {}).get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return jsonify(ok=False, error="That doesn’t look like a full link. It should start with https://"), 400
    if not url_allowed(url):
        return jsonify(ok=False, error="That link can’t be used here. Try a link from YouTube, X, TikTok or Instagram."), 400
    try:
        result = analyze(url)
    except FRIENDLY as error:         # our own, safe-to-show reason
        return jsonify(ok=False, error=str(error)), 422
    except Exception:
        traceback.print_exc()  # the real reason goes to the server log
        return jsonify(ok=False, error=SORRY), 422
    return jsonify(ok=True, token=remember(result, url))


@app.route("/options/<token>")
def options(token):
    data = CACHE.get(token)
    if not data:
        return redirect("/")  # expired or server restarted: start again
    return render_template("options.html", d=data, token=token, max_mb=MAX_MB)


def run_job(job_id, work, ip):
    """Runs in its own thread: does work(say, job_dir) and reports progress for the page to poll."""
    job_dir = os.path.join(BASE, job_id)

    def say(percent=None, stage=None):
        j = JOBS.get(job_id)
        if j is None:
            return
        if stage:
            j["stage"] = stage
        if percent is not None:
            j["percent"] = max(j["percent"] or 0, round(percent))  # a bar that never moves backwards
        j["at"] = time.time()

    try:
        os.makedirs(job_dir)
        JOBS[job_id].update(state="working", stage="Starting")
        path = work(say, job_dir)
        JOBS[job_id].update(state="done", percent=100, stage="Ready", file=path, at=time.time())
    except FRIENDLY as error:         # our own, safe-to-show reason (for example an unsupported script)
        shutil.rmtree(job_dir, ignore_errors=True)
        JOBS[job_id].update(state="error", error=str(error), at=time.time())
    except Exception:
        traceback.print_exc()
        shutil.rmtree(job_dir, ignore_errors=True)
        JOBS[job_id].update(state="error", error="That download failed. Try another size.", at=time.time())
    finally:
        slots.release()
        with active_lock:
            ACTIVE[ip] = max(0, ACTIVE.get(ip, 1) - 1)
            if not ACTIVE[ip]:
                ACTIVE.pop(ip, None)


def start_job(work):
    """Start work(say, job_dir) in the background if there is room, and tell the page which job to watch."""
    if shutil.disk_usage(BASE).free < 2 * max(MAX_MB, 1000) * 1048576:
        return jsonify(ok=False, error="The server is full right now. Please try again later."), 503
    ip = get_remote_address()
    with active_lock:
        if ACTIVE.get(ip, 0) >= PER_IP:
            return jsonify(ok=False, error="You already have downloads running. Wait for one to finish."), 429
        if not slots.acquire(blocking=False):
            return jsonify(ok=False, error="We’re busy right now. Please try again in a minute."), 503
        ACTIVE[ip] = ACTIVE.get(ip, 0) + 1
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"state": "queued", "percent": None, "stage": "Starting", "file": None, "error": None, "at": time.time()}
    threading.Thread(target=run_job, args=(job_id, work, ip), daemon=True).start()
    return jsonify(ok=True, job=job_id)


@app.post("/api/fetch")
@limiter.limit("4 per minute;40 per hour")
def api_fetch():
    body = request.get_json(silent=True) or {}
    data = CACHE.get(body.get("token"))
    oid = body.get("option")
    chosen = next((o for o in (data or {}).get("options", []) if o["id"] == oid), None)
    if not chosen:
        return jsonify(ok=False, error="That option has expired. Please paste the link again."), 400
    # the size estimate makes the progress bar exact for plain video and original audio
    expected = chosen["size"] if oid.startswith("v") or oid == "a_orig" else None
    return start_job(lambda say, job_dir: fetch(data["url"], oid, job_dir, say, expected))


@app.post("/api/overlay")
@limiter.limit("4 per minute;30 per hour")
def api_overlay():
    """A tweet drawn across a photo: the visitor's upload, or the tweet's own picture when own=1."""
    data = CACHE.get(request.form.get("token", ""))
    if not data or not any(o["id"] == "overlay" for o in data["options"]):
        return jsonify(ok=False, error="That option has expired. Please paste the link again."), 400
    theme, position = request.form.get("theme", "dark"), request.form.get("position", "bottom")
    if theme not in OVERLAY_THEMES or position not in OVERLAY_POSITIONS:
        return jsonify(ok=False, error="Pick a look and a place for the card."), 400
    upload, photo = request.files.get("photo"), None
    if request.form.get("own") != "1":
        if not upload or not upload.filename:
            return jsonify(ok=False, error="Choose a photo, or use the tweet's own picture."), 400
        photo = upload.read(MAX_UPLOAD + 1)
        if len(photo) > MAX_UPLOAD:
            return jsonify(ok=False, error="That photo is bigger than 15 MB."), 413
    return start_job(lambda say, job_dir: overlay(data["url"], photo, theme, position, job_dir, say))


@app.get("/api/job/<job_id>")
@limiter.exempt  # the page asks once a second; the work itself is limited above
def api_job(job_id):
    j = JOBS.get(job_id)
    if not j:
        return jsonify(ok=False, error="That download has expired. Please start it again."), 404
    out = {"ok": True, "state": j["state"], "percent": j["percent"], "stage": j["stage"]}
    if j["state"] == "done":
        out["file"] = f"/file/{job_id}"
    if j["state"] == "error":
        out["error"] = j["error"]
    return jsonify(out)


def counted(chunks, job_id, total):
    """Pass a file through and note when ALL of it was sent. A cancelled or repeated request (phones do this) is harmless."""
    sent = 0
    try:
        for chunk in chunks:
            sent += len(chunk)
            yield chunk
        else:                                          # reached the end: the whole file went out
            job = JOBS.get(job_id)
            if job and sent >= total:
                job["sent_at"] = time.time()
    finally:
        close = getattr(chunks, "close", None)
        if close:
            close()


@app.get("/file/<job_id>")
def get_file(job_id):
    job = JOBS.get(job_id)
    path = job and job["state"] == "done" and job["file"]
    if not path or not os.path.isfile(path):
        abort(404)
    # send_file also answers range requests, so interrupted downloads can resume
    response = send_file(path, as_attachment=True, download_name=os.path.basename(path))
    if request.method == "GET" and response.status_code == 200:
        response.response = counted(response.response, job_id, os.path.getsize(path))
    return response


if __name__ == "__main__":
    app.run(debug=False, threaded=True)  # local testing only; production uses gunicorn