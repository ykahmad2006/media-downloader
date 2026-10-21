"""
Fetchly website. Run: python app.py  then open http://127.0.0.1:5000
Needs fetchly_core.py next to it and a templates/ folder (index.html, options.html).
"""

import os
import shutil
import tempfile
import traceback
import uuid

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file

from fetchly_core import analyze, fetch

app = Flask(__name__)
# Work in the system temp folder: OneDrive can lock files and folders it is syncing
BASE = os.path.join(tempfile.gettempdir(), "fetchly_downloads")
os.makedirs(BASE, exist_ok=True)
FILES = {}  # job id -> finished file
CACHE = {}  # token -> analysed link (lives in memory while the server runs)

SORRY = ("We couldn’t get that one. It may be private, removed, or need a login "
         "(Instagram stories do). Make sure it opens in your browser, then try again.")


@app.route("/")
def home():
    return render_template("index.html")


@app.post("/api/analyze")
def api_analyze():
    url = ((request.get_json(silent=True) or {}).get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return jsonify(ok=False, error="That doesn’t look like a full link. It should start with https://"), 400
    try:
        result = analyze(url)
    except Exception:
        traceback.print_exc()  # the real reason shows up in this terminal
        return jsonify(ok=False, error=SORRY), 422
    token = uuid.uuid4().hex[:12]
    CACHE[token] = {**result, "url": url}
    return jsonify(ok=True, token=token)


@app.route("/options/<token>")
def options(token):
    data = CACHE.get(token)
    if not data:
        return redirect("/")  # server was restarted, start again
    return render_template("options.html", d=data, token=token)


@app.post("/api/fetch")
def api_fetch():
    body = request.get_json(silent=True) or {}
    data = CACHE.get(body.get("token"))
    oid = body.get("option")
    if not data or oid not in {o["id"] for o in data["options"]}:
        return jsonify(ok=False, error="That option has expired. Please paste the link again."), 400
    job_id = uuid.uuid4().hex
    job_dir = os.path.join(BASE, job_id)
    os.makedirs(job_dir)
    try:
        FILES[job_id] = fetch(data["url"], oid, job_dir)
    except Exception:
        traceback.print_exc()
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify(ok=False, error="That download failed. Try another size, or check the terminal for details."), 422
    return jsonify(ok=True, file=f"/file/{job_id}")


@app.get("/file/<job_id>")
def get_file(job_id):
    path = FILES.get(job_id)
    if not path or not os.path.isfile(path):
        abort(404)
    job_dir = os.path.join(BASE, job_id)
    response = send_file(path, as_attachment=True, download_name=os.path.basename(path))

    def cleanup():  # runs after the file has been sent; never crashes if Windows holds a lock
        FILES.pop(job_id, None)
        shutil.rmtree(job_dir, ignore_errors=True)
        try:
            os.remove(path)  # e.g. a ZIP stored beside the job folder
        except OSError:
            pass

    response.call_on_close(cleanup)
    return response


if __name__ == "__main__":
    # No auto-reloader: it can restart the server mid-download, which the browser reports as "Failed to fetch"
    app.run(debug=False, threaded=True)