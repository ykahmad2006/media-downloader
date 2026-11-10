"""
Checks that the site still behaves. Run from the fetchly folder:   python tests/test_site.py
It needs no internet and downloads nothing: videos and tweets are faked. Prints OK or the first thing that broke.
"""
import base64
import importlib
import io
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from xml.etree import ElementTree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def load_app(**env):
    """Import app.py fresh with the given settings (they are read when the file loads)."""
    for key in ("ACCESS_CODE", "SITE_URL", "GTM_ID", "GA_ID", "GSC_VERIFY", "BING_VERIFY", "CONTACT_EMAIL"):
        os.environ.pop(key, None)
    os.environ.update(env)
    import app
    app = importlib.reload(app)
    app.limiter.enabled = False
    return app


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.meta, self.h1, self.ld, self.title, self._ld, self._title, self.links, self.no_alt = {}, 0, [], "", False, False, set(), 0
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        at = dict(attrs)
        if tag == "meta" and (at.get("name") or at.get("property")):
            self.meta[at.get("name") or at.get("property")] = at.get("content", "")
        elif tag == "link" and at.get("rel") == "canonical":
            self.meta["canonical"] = at["href"]
        elif tag == "h1":
            self.h1 += 1
        elif tag == "title":
            self._title = True
        elif tag == "script" and at.get("type") == "application/ld+json":
            self._ld = True
            self.ld.append("")
        elif tag == "a" and at.get("href"):
            self.links.add(at["href"])
        elif tag == "img" and "alt" not in at:
            self.no_alt += 1

    def handle_endtag(self, tag):
        self._title = self._title and tag != "title"
        self._ld = self._ld and tag != "script"

    def handle_data(self, data):
        if self._title:
            self.title += data
        if self._ld:
            self.ld[-1] += data


def check_pages():
    app = load_app(SITE_URL="https://fetchly.example", GSC_VERIFY="abc_123", BING_VERIFY="bing-9", CONTACT_EMAIL="me@fetchly.example")
    client = app.app.test_client()
    paths = ["/"] + [f"/{s['slug']}" for s in app.SERVICES] + ["/privacy"]
    pages = {}
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, f"{path} gave {response.status_code}"
        page = Page(response.get_data(as_text=True))
        pages[path] = page
        assert page.h1 == 1, f"{path} must have exactly one h1 (has {page.h1})"
        assert 20 <= len(page.title) <= 70, f"{path} title length {len(page.title)}"
        assert 70 <= len(page.meta["description"]) <= 165, f"{path} description length {len(page.meta['description'])}"
        assert page.meta["canonical"] == "https://fetchly.example" + path, f"{path} canonical is {page.meta['canonical']}"
        assert page.meta["og:image"] == "https://fetchly.example/static/og-image.jpg"
        assert page.meta["twitter:card"] == "summary_large_image" and page.meta["og:title"] and page.meta["og:description"]
        assert page.no_alt == 0, f"{path} has an image without alt text"
        for block in page.ld:
            json.loads(block)                                  # structured data must be valid JSON
    assert pages["/"].meta["google-site-verification"] == "abc_123" and pages["/"].meta["msvalidate.01"] == "bing-9"
    for s in app.SERVICES:                                     # every tool page is linked from home, and links back home
        assert "/" + s["slug"] in pages["/"].links, f"home does not link to {s['slug']}"
        assert "/" in pages["/" + s["slug"]].links
    assert len({p.meta["description"] for p in pages.values()}) == len(pages), "two pages share a description"
    robots = client.get("/robots.txt").get_data(as_text=True)
    assert "Disallow: /api/" in robots and "Sitemap: https://fetchly.example/sitemap.xml" in robots
    urls = [e.text for e in ElementTree.fromstring(client.get("/sitemap.xml").get_data()).iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    assert urls == ["https://fetchly.example" + p for p in paths], urls
    assert client.get("/favicon.ico").status_code == 200 and client.get("/static/og-image.jpg").status_code == 200
    assert client.get("/no-such-page").status_code == 404
    assert "no-store" in client.get("/api/job/x").headers.get("Cache-Control", "")
    print(f"OK  {len(paths)} pages, link previews, structured data, robots.txt, sitemap")


def check_passcode():
    app = load_app(ACCESS_CODE="open sesame now")
    client = app.app.test_client()
    for path in ("/", "/video-to-gif", "/privacy", "/robots.txt", "/sitemap.xml", "/static/og-image.jpg"):
        assert client.get(path).status_code == 200, f"{path} should stay public (link previews and search engines need it)"
    assert client.post("/api/analyze", json={"url": "https://youtu.be/x"}).status_code == 401
    assert client.get("/options/abc").status_code == 401 and client.get("/file/abc").status_code == 401
    good = {"Authorization": "Basic " + base64.b64encode(b"x:open sesame now").decode()}
    assert client.post("/api/analyze", json={"url": "nope"}, headers=good).status_code == 400
    print("OK  passcode: public pages open, the tool locked")


def check_downloads():
    """A phone's download manager cancels the first request and asks again: the file must still be there."""
    from werkzeug.serving import make_server
    app = load_app()
    server = make_server("127.0.0.1", 5071, app.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def job(name, size):
        folder = os.path.join(app.BASE, name)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "video.mp4")
        open(path, "wb").write(os.urandom(size))
        app.JOBS[name] = {"state": "done", "percent": 100, "stage": "Ready", "file": path, "error": None, "at": time.time()}

    def get(name, headers=None):
        try:
            reply = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:5071/file/{name}", headers=headers or {}))
            return reply.status, len(reply.read())
        except urllib.error.HTTPError as error:
            return error.code, 0

    job("cancelled", 8 * 1024 * 1024)
    s = socket.create_connection(("127.0.0.1", 5071))
    s.sendall(b"GET /file/cancelled HTTP/1.1\r\nHost: x\r\n\r\n")
    s.recv(2000)
    s.close()
    time.sleep(0.6)
    assert get("cancelled") == (200, 8 * 1024 * 1024), "a cancelled first request must not delete the file"
    job("again", 1024 * 1024)
    assert get("again") == (200, 1024 * 1024) and get("again") == (200, 1024 * 1024), "tapping the link twice must work"
    assert get("again", {"Range": "bytes=1000-"})[0] == 206, "resume must work"
    app.JOBS["again"]["sent_at"] = time.time() - app.KEEP_AFTER_DOWNLOAD - 5
    app.sweep()
    assert get("again")[0] == 404 and not os.path.exists(os.path.join(app.BASE, "again")), "files go after the grace time"
    server.shutdown()
    print("OK  downloads survive cancelled and repeated requests, then are cleaned up")


def check_overlay():
    from PIL import Image
    app = load_app()
    import tweetcard
    tweetcard.fetch_avatar = lambda url: None
    tweetcard.tweet_info = lambda url, extra=(): {"id": "1", "text": "Hello from the test #python", "handle": "tester", "nick": "Tester",
                                                  "verified": True, "avatar": "", "photos": [], "cover": "", "media": 0}
    app.url_allowed = lambda url: True
    app.analyze = lambda url: {"title": "t", "thumb": None, "duration": 0, "by": "", "options": [
        {"id": "overlay", "kind": "overlay", "label": "x", "detail": "y", "size": None}]}
    client = app.app.test_client()
    token = client.post("/api/analyze", json={"url": "https://x.com/a/status/1"}).get_json()["token"]

    def run(**form):
        reply = client.post("/api/overlay", data={"token": token, **form}, content_type="multipart/form-data")
        data = reply.get_json()
        if not data["ok"]:
            return reply.status_code, data["error"]
        for _ in range(100):
            status = client.get("/api/job/" + data["job"]).get_json()
            if status["state"] in ("done", "error"):
                break
            time.sleep(0.1)
        if status["state"] == "error":
            return 200, status["error"]
        picture = client.get(status["file"])
        return 200, Image.open(io.BytesIO(picture.data))

    photo = io.BytesIO()
    Image.new("RGB", (1200, 800), (90, 140, 200)).save(photo, "JPEG")
    code, result = run(theme="dim", position="top", photo=(io.BytesIO(photo.getvalue()), "me.jpg"))
    assert code == 200 and result.size == (1200, 800), "the photo must keep its own shape"
    assert run(photo=(io.BytesIO(b"not a picture"), "x.jpg"))[1] == "That file is not a photo we can open."
    assert run(photo=(io.BytesIO(b"\0" * (16 * 1024 * 1024)), "big.jpg"))[0] == 413
    assert run()[0] == 400 and run(theme="neon", photo=(io.BytesIO(photo.getvalue()), "me.jpg"))[0] == 400
    big = client.post("/api/analyze", data=b"x" * 40000, content_type="application/json")
    assert big.status_code == 413, "JSON routes keep their small limit"
    print("OK  tweet over a photo: uploads, bad files, limits")


if __name__ == "__main__":
    for check in (check_pages, check_passcode, check_downloads, check_overlay):
        check()
    print("\nAll checks passed.")
