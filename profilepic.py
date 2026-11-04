"""
Profile pictures: paste a profile link, get the picture as ONE image file (JPG or PNG, never a ZIP)
in the best quality the site gives out. Public accounts.

  Instagram  the HD version        X        the original upload (not the small thumbnail)
  TikTok     the largest version   YouTube  the channel picture at full size

Used by fetchly_core.py, so the website and get.py both have it.
"""

import json
import mimetypes
import os
import re
import subprocess
import sys
from urllib.parse import urlparse

import requests
import yt_dlp

try:
    from PIL import Image
except ImportError:      # Pillow only converts odd formats (WEBP and the like) to PNG; without it they are kept as they are
    Image = None

TOOL_TIMEOUT = 120
MAX_BYTES = 25 * 1024 * 1024
YOUTUBE_IMAGE_HOSTS = ("ggpht.com", "googleusercontent.com", "ytimg.com")

# first part of a link that is NOT somebody's profile
IG_RESERVED = {"p", "reel", "reels", "tv", "stories", "explore", "accounts", "s", "direct", "about", "legal", "web",
               "developer", "api", "privacy", "press", "directory", "challenge", "emails", "oauth", "static", "graphql"}
X_RESERVED = {"home", "explore", "i", "search", "settings", "messages", "notifications", "compose", "hashtag", "intent",
              "share", "login", "signup", "tos", "privacy", "about", "jobs", "download", "account", "lists", "topics"}
YT_TABS = {"videos", "shorts", "streams", "playlists", "community", "featured", "about", "podcasts", "releases", "courses"}


class PictureError(Exception):
    """A reason we can show to the visitor."""


def _on(host, *domains):
    return any(host == d or host.endswith("." + d) for d in domains)


def avatar_target(url):
    """If url is a link to a profile or channel, say which one. Otherwise None (posts, reels and videos are not profiles)."""
    parts = urlparse(url or "")
    host = (parts.hostname or "").lower()
    segs = [s for s in parts.path.split("/") if s]
    if not host or not segs:
        return None

    if _on(host, "instagram.com", "instagr.am"):
        name = segs[0]
        if name.lower() not in IG_RESERVED and re.fullmatch(r"[A-Za-z0-9._]{1,30}", name) \
                and (len(segs) == 1 or segs[1].lower() in ("reels", "tagged", "avatar", "feed", "guides")):
            return {"platform": "Instagram", "handle": name, "avatar_url": f"https://www.instagram.com/{name}/avatar/"}

    elif _on(host, "x.com", "twitter.com"):
        name = segs[0]
        if name.lower() not in X_RESERVED and re.fullmatch(r"[A-Za-z0-9_]{1,15}", name) \
                and (len(segs) == 1 or segs[1].lower() in ("with_replies", "media", "highlights", "likes", "photo", "articles")):
            return {"platform": "X", "handle": name, "avatar_url": f"https://x.com/{name}/photo"}

    elif _on(host, "tiktok.com"):
        if segs[0].startswith("@") and re.fullmatch(r"@[A-Za-z0-9._]{1,40}", segs[0]) \
                and (len(segs) == 1 or segs[1].lower() in ("avatar", "liked")):
            name = segs[0][1:]
            return {"platform": "TikTok", "handle": name, "avatar_url": f"https://www.tiktok.com/@{name}/avatar"}

    elif _on(host, "youtube.com"):
        if segs[0].startswith("@") and re.fullmatch(r"@[\w.\-]{3,60}", segs[0]) and (len(segs) == 1 or segs[1].lower() in YT_TABS):
            return {"platform": "YouTube", "handle": segs[0][1:], "page_url": f"https://www.youtube.com/{segs[0]}"}
        if segs[0] in ("channel", "c", "user") and len(segs) >= 2 and re.fullmatch(r"[\w.\-]{1,80}", segs[1]) \
                and (len(segs) == 2 or segs[2].lower() in YT_TABS):
            return {"platform": "YouTube", "handle": segs[1], "page_url": f"https://www.youtube.com/{segs[0]}/{segs[1]}"}
    return None


# ---------------------------------------------------------------- looking

def _why_not(target, data, stderr):
    """Log gallery-dl's real answer for the server owner, and pick the most useful message for the visitor."""
    errors = [e[-1] for e in data if isinstance(e, list) and e and e[0] == -1]
    print(f"Profile picture lookup, {target['platform']} @{target['handle']}: gallery-dl said {errors or 'nothing'} | "
          f"{stderr.strip()[-400:]}", file=sys.stderr)
    text = (json.dumps(errors) + stderr).lower()
    if errors or any(w in text for w in ("login", "cookie", "401", "403", "429", "rate")):
        return (f"{target['platform']} would not show that profile right now. This usually means the login the site "
                "uses has expired or the site is being slowed down. Please try again later.")
    return "We couldn’t find that profile picture. The account may not exist, or it only has the default picture."


def _gallery_listing(target, extra_args):
    """Ask gallery-dl for the picture's address, without downloading it (also tells us the account is reachable)."""
    cmd = [sys.executable, "-m", "gallery_dl", "-j", *extra_args, target["avatar_url"]]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TOOL_TIMEOUT)
        data = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise PictureError("That took too long. Please try again.")
    except ValueError:
        raise PictureError(_why_not(target, [], result.stderr))
    for entry in data:
        if isinstance(entry, list) and len(entry) >= 3 and entry[0] == 3 and isinstance(entry[1], str):
            meta = entry[-1] if isinstance(entry[-1], dict) else {}
            return {"preview": entry[1], "width": meta.get("width") or None, "height": meta.get("height") or None}
    raise PictureError(_why_not(target, data, result.stderr))


def best_avatar(thumbnails):
    """The channel picture among yt-dlp's thumbnails (the 'uncropped' one if there is one, then the biggest)."""
    avatars = [t for t in thumbnails or [] if "avatar" in str(t.get("id", "")) and t.get("url")]
    if not avatars:
        return None
    pool = [t for t in avatars if "uncropped" in str(t.get("id"))] or avatars
    return max(pool, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))


def full_size(url):
    """Google's image links end in a size such as =s900-c-k; =s0 asks for the original."""
    return re.sub(r"=s\d+[^/?&#=]*$", "=s0", url)


def _youtube(target):
    opts = {"quiet": True, "skip_download": True, "extract_flat": True, "playlistend": 1, "socket_timeout": 30,
            "allowed_extractors": ["default", "-generic"]}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target["page_url"], download=False) or {}
    except Exception as error:
        print("YouTube channel lookup failed:", error, file=sys.stderr)
        raise PictureError("We couldn’t find that channel. Check that the link opens in your browser.")
    pick = best_avatar(info.get("thumbnails"))
    if not pick:
        raise PictureError("We couldn’t find a picture for that channel.")
    return {"preview": pick["url"], "image_url": full_size(pick["url"]),
            "width": pick.get("width") or None, "height": pick.get("height") or None}


def find_picture(target, extra_args=()):
    """Look the picture up. Returns {preview, width, height}; raises PictureError with a reason we can show."""
    if target["platform"] == "YouTube":
        return _youtube(target)
    return _gallery_listing(target, list(extra_args))


# ---------------------------------------------------------------- saving

def _tidy(path, stem):
    """Give the file a clear name. JPG and PNG are kept byte for byte; WEBP and similar become PNG."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png"):
        dest = stem + (".jpg" if ext == ".jpeg" else ext)
        os.replace(path, dest)
        return dest
    if Image is not None and ext in (".webp", ".bmp", ".gif", ".avif", ".heic", ".heif"):
        try:
            with Image.open(path) as img:
                img.load()
                dest = stem + ".png"
                (img if img.mode in ("RGB", "RGBA", "L") else img.convert("RGBA")).save(dest, "PNG")
            os.remove(path)
            return dest
        except Exception:
            pass        # cannot convert: keep it as it came
    dest = stem + (ext or ".jpg")
    os.replace(path, dest)
    return dest


def _download(url, job_dir, stem):
    host = urlparse(url).hostname or ""
    if not url.startswith("https://") or not any(host == h or host.endswith("." + h) for h in YOUTUBE_IMAGE_HOSTS):
        raise PictureError("That picture address is not one we can fetch.")
    reply = requests.get(url, timeout=20, stream=True, headers={"User-Agent": "Mozilla/5.0"})
    reply.raise_for_status()
    kind = reply.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if not kind.startswith("image/"):
        raise PictureError("That address did not give a picture.")
    data = reply.raw.read(MAX_BYTES + 1, decode_content=True)
    if len(data) > MAX_BYTES:
        raise PictureError("That picture is too large.")
    ext = ".jpg" if "jpeg" in kind else ".png" if "png" in kind else ".webp" if "webp" in kind else (mimetypes.guess_extension(kind) or ".jpg")
    raw = os.path.join(job_dir, "picture-download" + ext)
    with open(raw, "wb") as f:
        f.write(data)
    return _tidy(raw, stem)


def save_picture(target, job_dir, extra_args=()):
    """Download the profile picture into job_dir as a single, clearly named image file and return its path."""
    stem = os.path.join(job_dir, f"{target['platform'].lower()}-{target['handle']}-profile")
    if target["platform"] == "YouTube":
        return _download(_youtube(target)["image_url"], job_dir, stem)
    cmd = [sys.executable, "-m", "gallery_dl", "-d", job_dir, "--range", "1-1", *extra_args, target["avatar_url"]]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TOOL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise PictureError("That took too long. Please try again.")
    files = [os.path.join(p, n) for p, _, names in os.walk(job_dir) for n in names]
    if not files:
        print(f"Profile picture download, {target['platform']} @{target['handle']}, saved nothing:",
              result.stderr.strip()[-400:], file=sys.stderr)
        raise PictureError("We couldn’t download that profile picture.")
    return _tidy(max(files, key=os.path.getsize), stem)