"""
Fetchly engine: analyse a link, list every download option, fetch the one chosen.
Used by app.py (website) and get.py (command line).

Setup: pip install -U flask yt-dlp gallery-dl
FFmpeg is recommended: it enables HD merging, MP3 and GIF options.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from contextlib import contextmanager
from urllib.parse import urljoin, urlparse

import yt_dlp
from yt_dlp.utils import DownloadError

try:
    import profilepic
    HAS_PIC = True
    PictureError = profilepic.PictureError
except ImportError:
    profilepic, HAS_PIC = None, False
    PictureError = type("PictureError", (Exception,), {})

try:
    import tweetoverlay
    HAS_OVERLAY = True
    OVERLAY_THEMES, OVERLAY_POSITIONS, MAX_UPLOAD = tuple(tweetoverlay.THEMES), tweetoverlay.POSITIONS, tweetoverlay.MAX_UPLOAD
except ImportError:
    tweetoverlay, HAS_OVERLAY = None, False
    OVERLAY_THEMES, OVERLAY_POSITIONS, MAX_UPLOAD = ("dark",), ("bottom",), 15 * 1024 * 1024

try:                       # tweet scrolls need: pip install pillow fonttools requests
    import tweetcard
    HAS_CARD = True
    CardError = tweetcard.CardError
except ImportError:
    tweetcard, HAS_CARD = None, False
    CardError = type("CardError", (Exception,), {})
class LinkError(Exception):
    """A plain-words reason a link could not be read, safe to show to the visitor."""


FRIENDLY = (CardError, PictureError, LinkError)   # errors whose message is safe to show to the visitor

HAS_FFMPEG = shutil.which("ffmpeg") is not None
HAS_GALLERY = importlib.util.find_spec("gallery_dl") is not None
# Login cookies for sites that want one (Instagram stories). Best way: export a cookies.txt from your browser and
# save it next to this file. It is found automatically and works even while the browser is open.
# On a server, set COOKIES_FILE to its path instead.
_LOCAL_COOKIES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
COOKIES_FILE = os.environ.get("COOKIES_FILE") or (_LOCAL_COOKIES if os.path.isfile(_LOCAL_COOKIES) else None)
COOKIES_BROWSER = None  # last resort: read cookies straight from "firefox"/"chrome"/"edge". Unreliable while that browser is open
MAX_MB = int(os.environ.get("MAX_MB", "1000"))             # biggest file we will save in MB; 0 = no limit (the command line uses 0)
# What "best quality" means: the tallest picture, then the smoothest frame rate, then HDR, then the stream with
# the MOST DATA (bitrate), then the codec. Putting bitrate before codec is what finds the sharper stream.
FORMAT_SORT = ["res", "fps", "hdr:12", "br", "size", "codec"]
GIF_BEST_WIDTH, GIF_BEST_FPS, GIF_BEST_SECS = 1080, 30, 10   # the most a "best quality" GIF takes from a clip
TOOL_TIMEOUT = int(os.environ.get("TOOL_TIMEOUT", "600"))  # seconds before ffmpeg or gallery-dl is stopped
MAX_ITEMS = 30                                             # most photos or videos taken from one post
ALLOW_GENERIC = os.environ.get("ALLOW_GENERIC") == "1"     # yt-dlp's "any website" mode, off by default

# name: (width px, frames per second, seconds, label)
GIF_PRESETS = {
    "gif_s": (320, 10, 6, "Small GIF"),
    "gif_m": (480, 12, 8, "Medium GIF"),
    "gif_l": (640, 15, 10, "Large GIF"),
}

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
AUDIO_EXT = (".mp3", ".m4a", ".aac", ".opus", ".wav")
VIDEO_EXT = ("mp4", "webm", "mov", "m4v")
SONG_EXT = ("mp3", "m4a", "aac", "opus", "wav", "ogg")


def opt(oid, kind, label, detail, size):
    return {"id": oid, "kind": kind, "label": label, "detail": detail, "size": size}


def mb(n):
    return round(n / 1048576, 1) if n else None


@contextmanager
def cookie_copy():
    """A private, throw-away copy of the cookies file for ONE job.
    yt-dlp rewrites the file it is given, so jobs running together must not share one."""
    if COOKIES_FILE and os.path.isfile(COOKIES_FILE):
        fd, copy = tempfile.mkstemp(prefix="fetchly_cookies_", suffix=".txt")
        os.close(fd)
        try:
            shutil.copyfile(COOKIES_FILE, copy)
            yield copy
        finally:
            try:
                os.remove(copy)
            except OSError:
                pass
    else:
        yield None


def browser_identity():
    """yt-dlp's 'act like Chrome' setting, when the optional curl-cffi library is installed. TikTok often refuses plain requests."""
    try:
        from yt_dlp.dependencies import curl_cffi
        if not curl_cffi:
            return None
        from yt_dlp.networking.impersonate import ImpersonateTarget
        return ImpersonateTarget.from_str("chrome")
    except Exception:
        return None


def ydl_opts(cookies=None, extra=None, url=None):
    o = {"quiet": True, "noplaylist": True, "ignoreerrors": False,
         "socket_timeout": 30, "retries": 3}
    if not ALLOW_GENERIC:
        o["allowed_extractors"] = ["default", "-generic"]  # known sites only, not "fetch any URL"
    if cookies:
        o["cookiefile"] = cookies
    elif COOKIES_BROWSER:
        o["cookiesfrombrowser"] = (COOKIES_BROWSER,)
    if url and site_of(url) == "tiktok" and browser_identity():
        o["impersonate"] = browser_identity()
    return {**o, **(extra or {})}


def build_options(info):
    """Turn yt-dlp's format list into simple choices with estimated sizes."""
    fmts = info.get("formats") or []
    dur = info.get("duration") or 0
    has = lambda f, key: f.get(key) not in (None, "none")

    def size(f):
        s = f.get("filesize") or f.get("filesize_approx")
        if not s and f.get("tbr") and dur:
            s = f["tbr"] * 125 * dur
        return s or 0

    audio = [f for f in fmts if has(f, "acodec") and not has(f, "vcodec")]
    best_audio = max(audio, key=lambda f: f.get("abr") or 0, default=None)
    audio_size = size(best_audio) if best_audio else 0

    heights = {}
    for f in fmts:
        if f.get("height") and has(f, "vcodec"):
            total = size(f) + (0 if has(f, "acodec") else audio_size)
            heights[f["height"]] = max(heights.get(f["height"], 0), total)

    options = [opt(f"v{h}", "video", f"{h}p video", "MP4, with sound", mb(heights[h]))
               for h in sorted(heights, reverse=True)]
    if best_audio:
        options.append(opt("a_orig", "audio", "Original audio", "Best quality, M4A or Opus", mb(audio_size)))
    if HAS_FFMPEG:
        if dur:
            options.append(opt("a_mp3", "audio", "MP3 audio", "Plays on any phone", mb(dur * 40000)))
        top = max((f for f in fmts if f.get("height") and has(f, "vcodec")),
                  key=lambda f: (f["height"], f.get("fps") or 0), default=None)
        if top:                                            # the best GIF this clip can give, from its own size and frame rate
            src_w = top.get("width") or int(top["height"] * 16 / 9)
            w = min(src_w, GIF_BEST_WIDTH)
            fps = min(round(top.get("fps") or 24), GIF_BEST_FPS)
            length = min(dur or GIF_BEST_SECS, GIF_BEST_SECS)
            est = w * int(top["height"] * w / src_w) * fps * length * 0.3
            options.append(opt("gif_best", "gif", "Best quality GIF",
                               f"{w}px wide, {fps} fps, first {int(length)}s, full colour palette", mb(est)))
        if heights:
            for oid, (w, fps, secs, name) in GIF_PRESETS.items():
                length = min(dur or secs, secs)
                est = w * (w * 9 // 16) * fps * length * 0.3
                options.append(opt(oid, "gif", name, f"{w}px wide, first {int(length)}s", mb(est)))
    return options


def gallery_supports(url):
    """Ask gallery-dl whether it knows this site, so we never offer a button that cannot work."""
    if not HAS_GALLERY:
        return False
    try:
        from gallery_dl import extractor
        return extractor.find(url) is not None
    except Exception:
        return True  # if the check itself breaks, keep the old behaviour


def site_of(url):
    """Which of the four sites a link belongs to: tiktok, instagram, x, youtube, or an empty string."""
    host = (urlparse(url).hostname or "").lower()
    for name, domains in (("tiktok", ("tiktok.com",)), ("instagram", ("instagram.com", "instagr.am")),
                          ("x", ("x.com", "twitter.com")), ("youtube", ("youtube.com", "youtu.be"))):
        if any(host == d or host.endswith("." + d) for d in domains):
            return name
    return ""


def tiktok_kind(url):
    """photo for a TikTok photo post, video for a video, unknown for anything else (short links, profiles)."""
    path = urlparse(url).path
    return "photo" if "/photo/" in path else "video" if "/video/" in path else "unknown"


def resolve_link(url):
    """TikTok short links (vm.tiktok.com, /t/...) are followed to the real post, one hop at a time and only through TikTok."""
    import requests
    host = (urlparse(url).hostname or "").lower()
    if not (host in ("vm.tiktok.com", "vt.tiktok.com") or (site_of(url) == "tiktok" and urlparse(url).path.startswith("/t/"))):
        return url
    current = url
    try:
        for _ in range(4):
            reply = requests.get(current, allow_redirects=False, timeout=8, stream=True, headers={"User-Agent": "Mozilla/5.0"})
            reply.close()
            where = reply.headers.get("Location")
            if not (300 <= reply.status_code < 400 and where):
                break
            nxt = urljoin(current, where)
            if site_of(nxt) != "tiktok":
                break
            current = nxt
    except Exception:
        return url
    parts = urlparse(current)
    return f"{parts.scheme}://{parts.netloc}{parts.path}" if "/video/" in parts.path or "/photo/" in parts.path else url


def explain(error, site):
    """Turn a technical failure into a plain sentence for the visitor. None means: nothing more useful to say."""
    text = str(error or "").lower()
    name = {"tiktok": "TikTok", "instagram": "Instagram", "x": "X", "youtube": "YouTube"}.get(site, "That site")
    if "not a bot" in text or "confirm you" in text:
        return ("YouTube is asking this server to prove it is not a robot, which it often does to servers. "
                "Try again later, or ask the site owner to add a cookies file.")
    if "private" in text and ("video" in text or "post" in text or "account" in text):
        return f"That {name} post is private, so it can’t be saved."
    if "age" in text and ("restrict" in text or "confirm your age" in text or "inappropriate" in text):
        return "That video is age-restricted, so only logged-in viewers can see it."
    if any(w in text for w in ("login", "log in", "sign in", "cookies")):
        return (f"{name} only shows that to logged-in viewers or to browsers it trusts. "
                "If it plays in your browser while you are logged out, try again in a minute. Otherwise the site owner can add cookies.")
    if any(w in text for w in ("blocked", "403", "429", "too many requests", "rate limit", "rate-limit", "ip address")):
        return f"{name} is turning this server away right now. Please try again in a little while."
    if any(w in text for w in ("unavailable", "removed", "not available", "does not exist", "could not be found", "404")):
        return f"That {name} post is unavailable. It may have been removed or made private."
    if "unsupported url" in text:
        return "That link isn’t one Fetchly can read. Paste the link of the post or video itself."
    if site == "tiktok":
        return ("TikTok would not show that post to this computer. Open the link in your browser while logged out: if it plays there, "
                "try again in a minute. If it keeps failing, the site owner can add TikTok cookies.")
    return None


def _is_video(meta):
    return (meta.get("type") == "video") or (meta.get("extension") or "").lower() in VIDEO_EXT


def _is_song(meta):
    return (meta.get("type") == "audio") or (meta.get("extension") or "").lower() in SONG_EXT


def cookie_args(cookies):
    """gallery-dl's way of being given the login cookies."""
    if cookies:
        return ["--cookies", cookies]
    if COOKIES_BROWSER:
        return ["--cookies-from-browser", COOKIES_BROWSER]
    return []


def gallery_items(url, cookies):
    """List the separate photos and videos of a post or story WITHOUT downloading them."""
    cmd = [sys.executable, "-m", "gallery_dl", "-j", "--range", f"1-{MAX_ITEMS}", *cookie_args(cookies), url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        data = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise DownloadError("Listing that post took too long.")
    except ValueError:
        raise DownloadError("Could not list that post. " + result.stderr[-200:])
    # gallery-dl's JSON: [2, folder info] and [3, file url, file info] for every photo or video
    items = [e[-1] for e in data if isinstance(e, list) and len(e) >= 3 and e[0] == 3 and isinstance(e[-1], dict)]
    if not items:
        raise DownloadError("No photos or videos found. " + result.stderr[-200:])
    return items


def item_options(items):
    """One download choice per photo or video, each in the best quality the site gives."""
    options = []
    for n, meta in enumerate(items, 1):
        ext = (meta.get("extension") or "").lower()
        video, song = ext in VIDEO_EXT, ext in SONG_EXT
        w, h = meta.get("width"), meta.get("height")
        detail = " · ".join(x for x in [(ext or "file").upper(), f"{w}x{h}" if w and h else "", "best quality"] if x)
        choice = opt(f"item{n}", "item", "The song from the post" if song else f"{'Video' if video else 'Photo'} {n} of {len(items)}", detail, None)
        cover = meta.get("display_url") or ""          # the still picture of a photo, or the cover frame of a video
        if cover.startswith("https://"):
            choice["thumb"] = cover
        when = re.match(r"(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)", str(meta.get("date") or ""))
        if when:                                        # gallery-dl gives UTC; the page shows it in the visitor's own time
            choice["when"] = f"{when.group(1)}T{when.group(2)}Z"
        options.append(choice)
    return options


def analyze(url):
    """Look at a link without downloading. Returns title, thumbnail and options."""
    if HAS_PIC and profilepic.avatar_target(url):          # a profile or channel link: offer its picture
        target = profilepic.avatar_target(url)
        with cookie_copy() as cookies:
            found = profilepic.find_picture(target, cookie_args(cookies))
        size = f"{found['width']}x{found['height']}" if found.get("width") and found.get("height") else ""
        detail = " · ".join(x for x in ["Best quality, one image file", size] if x)
        return {"title": f"Profile picture of @{target['handle']}", "thumb": found.get("preview"), "duration": 0,
                "by": target["platform"], "options": [opt("avatar", "avatar", "Profile picture", detail, None)]}
    url = resolve_link(url)
    site = site_of(url)
    info, options, failure, items_error = {}, [], None, None
    with cookie_copy() as cookies:
        if not (site == "tiktok" and tiktok_kind(url) == "photo"):   # a TikTok photo post is not a video: go to the photo reader
            try:
                with yt_dlp.YoutubeDL(ydl_opts(cookies, url=url)) as ydl:
                    info = ydl.extract_info(url, download=False) or {}
                options = build_options(info)
            except DownloadError as error:
                failure = error
                if not HAS_GALLERY:
                    raise LinkError(explain(error, site)) if explain(error, site) else error
        has_video = any(o["kind"] == "video" for o in options)
        # A tweet that is only words: offer to draw it as a scroll
        tweet = None
        if HAS_CARD and not has_video and tweetcard.tweet_id(url):
            try:
                found = tweetcard.tweet_info(url, cookie_args(cookies))
                tweet = None if found["media"] else found      # tweets with photos keep the normal flow
            except Exception as error:
                print("Could not read that tweet:", error, file=sys.stderr)
        # Photos, carousels, stories, TikTok posts: ask gallery-dl what is in the post, item by item
        items = []
        if gallery_supports(url) and not tweet and (site == "instagram" or not has_video):
            try:
                items = gallery_items(url, cookies)
            except Exception as error:
                items_error = error
                print("Could not list the items of that post:", error, file=sys.stderr)  # shows up in the server log
                failure = failure or error
    videos = [(n, m) for n, m in enumerate(items, 1) if _is_video(m)]
    tiktok_video = site == "tiktok" and not has_video and bool(videos)      # TikTok's own best file, when the usual reader is refused
    show_items = len(items) >= 2 or (len(items) == 1 and not has_video)
    if has_video and (info.get("thumbnail") or info.get("thumbnails")):
        options.append(opt("cover", "cover", "Cover picture", "The video's cover image, as one file", None))
    if tiktok_video:
        n, meta = videos[0]
        w, h, seconds = meta.get("width"), meta.get("height"), meta.get("duration")
        detail = " · ".join(x for x in ["MP4", f"{w}x{h}" if w and h else "", f"{int(seconds)}s" if seconds else "", "best quality TikTok gives out"] if x)
        options.append(opt(f"item{n}", "video", "Video", detail, None))
        if HAS_FFMPEG:
            options.append(opt(f"fbmp3{n}", "audio", "MP3 audio", "The sound of the video", None))
            options.append(opt(f"fbgif{n}", "gif", "Best quality GIF",
                               f"The clip's own size and speed, up to {GIF_BEST_WIDTH}px, first {GIF_BEST_SECS}s", None))
        author = meta.get("author")
        author = author.get("uniqueId") if isinstance(author, dict) else author
        info = {"title": (str(meta.get("desc") or meta.get("title") or "TikTok video"))[:90], "uploader": f"@{author}" if author else "TikTok"}
    elif show_items:
        options += item_options(items)
    if tweet:
        options.append(opt("tweet", "image", "Ancient scroll (PNG)",
                           "The tweet on parchment, with the author's face and ~username", None))
    if HAS_OVERLAY and tweetcard.tweet_id(url) and (options or tweet):      # any tweet can be drawn across a photo
        options.append(opt("overlay", "overlay", "Photo with the tweet across it",
                           "Upload your photo, or use the tweet's own picture. The tweet is drawn like it looks on X.", None))
    # Everything at once: a ZIP, or a slideshow video for a TikTok photo post. Never offered blind for TikTok.
    if (gallery_supports(url) and (site == "instagram" or not has_video) and not (show_items and len(items) == 1)
            and not tweet and not tiktok_video and not (site == "tiktok" and not items)):
        what = "story" if "/stories/" in url else "post"
        if not HAS_FFMPEG:
            label, detail = f"All photos and videos in this {what}", "Saved as one ZIP"
        elif site == "tiktok":
            label, detail = "Slideshow video (MP4)", "Your photos set to the original song"
        else:
            label, detail = f"Everything in this {what} at once", "One file: a ZIP, or an MP4 slideshow if all are photos"
        options.append(opt("gallery", "zip", label, detail, None))
    if not options:
        reason = explain(" ".join(str(e) for e in (failure, items_error) if e), site)
        if reason:
            raise LinkError(reason)
        raise failure or DownloadError("No downloadable media found.")  # keeps the real reason for the log
    if tweet:
        words = re.sub(r"https?://\S+", "", tweet["text"]).strip()
        info = {"title": (words[:90] + ("…" if len(words) > 90 else "")) or "A tweet", "thumbnail": tweet["avatar"],
                "uploader": "~" + tweet["handle"]}
    return {"title": info.get("title") or "Your link", "thumb": info.get("thumbnail"), "url": url,
            "duration": int(info.get("duration") or 0), "by": info.get("uploader") or "", "options": options}


def first_file(folder):
    files = [os.path.join(folder, n) for n in os.listdir(folder) if not n.endswith((".part", ".ytdl"))]
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        raise DownloadError("Nothing was saved. The file may be bigger than the limit.")
    return max(files, key=os.path.getsize)


def run_tool(cmd):
    """Run ffmpeg or gallery-dl with a time limit so one bad job cannot hang a thread forever."""
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=TOOL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise DownloadError("That took too long and was stopped.")


def no_progress(percent=None, stage=None):
    """Default progress listener: does nothing (the command line does not need one)."""


def make_hooks(say, expected_bytes=None):
    """yt-dlp hooks that report to say(percent, stage). With an expected size the percent covers
    video and audio together, otherwise it follows the file being downloaded."""
    seen = {}

    def on_progress(d):
        key = d.get("filename") or ""
        done = d.get("downloaded_bytes") or 0
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if d.get("status") == "finished":
            done = total or done
        seen[key] = done
        if expected_bytes:
            say(min(97, sum(seen.values()) / expected_bytes * 100), "Downloading")
        elif total:
            say(min(97, done / total * 100), "Downloading")
        else:
            say(None, "Downloading")

    def on_post(d):
        if d.get("status") == "started":
            say(98, "Finishing up")

    return {"progress_hooks": [on_progress], "postprocessor_hooks": [on_post]}


def slideshow(url, images, song, job_dir, cookies, say=no_progress):
    """Photos plus the post's song become one MP4 (1080x1920 for TikTok, 1080x1350 otherwise)."""
    tmp = job_dir + "_tmp"
    os.makedirs(tmp, exist_ok=True)
    try:
        duration = 0
        if not song:
            try:  # slideshow posts carry their song as the audio track
                song_opts = ydl_opts(cookies, {"format": "bestaudio/best",
                                               "outtmpl": os.path.join(tmp, "song.%(ext)s")})
                with yt_dlp.YoutubeDL(song_opts) as ydl:
                    duration = (ydl.extract_info(url, download=True) or {}).get("duration") or 0
                song = first_file(tmp)
            except Exception:
                song = None
        per = min(5, max(2, duration / len(images))) if duration else 3  # seconds per photo
        quote = lambda p: p.replace("\\", "/").replace("'", "'\\''")
        lines = ["file '%s'\nduration %.2f" % (quote(path), per) for path in images]
        lines.append("file '%s'" % quote(images[-1]))
        list_path = os.path.join(tmp, "list.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        say(None, "Building your slideshow")
        w, h = (1080, 1920) if "tiktok.com" in url else (1080, 1350)
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p")
        out = os.path.join(job_dir, "slideshow.mp4")
        cmd = ["ffmpeg", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", list_path]
        if song:
            cmd += ["-i", song]
        cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "18"]
        if song:
            cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
        cmd += ["-movflags", "+faststart", out]
        run_tool(cmd)
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def gallery(url, job_dir, cookies, say=no_progress, only=None):
    """gallery-dl: every photo/video of a post (All photos become an MP4 slideshow, otherwise a ZIP),
    or just item number `only` as a single file."""
    span = f"{only}-{only}" if only else f"1-{MAX_ITEMS}"
    cmd = [sys.executable, "-m", "gallery_dl", "-d", job_dir, "--range", span,
           *(["--filesize-max", f"{MAX_MB}M"] if MAX_MB else []), *cookie_args(cookies)]
    say(None, "Getting that item" if only else "Collecting everything in the post")
    try:
        result = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=TOOL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise DownloadError("gallery-dl took too long and was stopped.")
    files = [os.path.join(p, n) for p, _, names in os.walk(job_dir) for n in names]
    if not files:
        raise DownloadError("gallery-dl saved nothing. " + result.stderr[-200:])
    if MAX_MB and sum(os.path.getsize(f) for f in files) > MAX_MB * 1048576:
        raise DownloadError("That post is bigger than the size limit.")
    if only or len(files) == 1:
        return max(files, key=os.path.getsize)
    images = sorted(f for f in files if f.lower().endswith(IMAGE_EXT))
    songs = [f for f in files if f.lower().endswith(AUDIO_EXT)]
    if HAS_FFMPEG and images and len(images) + len(songs) == len(files):
        try:
            return slideshow(url, images, songs[0] if songs else None, job_dir, cookies, say)
        except Exception:
            traceback.print_exc()  # fall back to the ZIP below
    say(None, "Packing a ZIP")
    return shutil.make_archive(job_dir + "_zip", "zip", job_dir)


def probe_video(path):
    """(width, height, frames per second) of a video file, or zeros if it cannot be read."""
    if not shutil.which("ffprobe"):
        return 0, 0, 0
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                             "stream=width,height,r_frame_rate", "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        w, h, rate = result.stdout.strip().split(",")[:3]
        top, _, bottom = rate.partition("/")
        return int(w), int(h), float(top) / float(bottom or 1)
    except (ValueError, ZeroDivisionError):
        return 0, 0, 0


def make_gif(url, oid, job_dir, opts, say=no_progress, source=None):
    """A GIF from the link's video, or from a video file we already have (source)."""
    best = oid == "gif_best"
    if best:
        secs, cap = GIF_BEST_SECS, 1080                    # width and frame rate come from the clip itself, below
    else:
        width, fps, secs, _ = GIF_PRESETS[oid]
        cap = 720
    if source is None:
        opts.update({"format": f"bestvideo*[height<={cap}]/best[height<={cap}]/best",
                     "outtmpl": os.path.join(job_dir, "source.%(ext)s")})
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        source = first_file(job_dir)
    say(None, "Making your GIF")
    out = os.path.join(job_dir, "clip.gif")
    if best:                                               # keep the clip's own size and smoothness, up to the caps
        src_w, _, src_fps = probe_video(source)
        width, fps = min(src_w or GIF_BEST_WIDTH, GIF_BEST_WIDTH), min(round(src_fps or 24), GIF_BEST_FPS)
        palette = "palettegen=stats_mode=diff:max_colors=256[p];[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle"
    else:
        palette = "palettegen[p];[b][p]paletteuse"
    vf = f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];[a]{palette}"
    run_tool(["ffmpeg", "-nostdin", "-y", "-t", str(secs), "-i", source, "-vf", vf, out])
    os.remove(source)
    if MAX_MB and os.path.getsize(out) > MAX_MB * 1048576:
        raise DownloadError("That GIF is bigger than the size limit. Try a smaller GIF.")
    return out


def video_cover(url, cookies):
    """The cover picture of a tweet's video (the biggest thumbnail yt-dlp lists), or None."""
    try:
        with yt_dlp.YoutubeDL(ydl_opts(cookies, {"skip_download": True})) as ydl:
            found = ydl.extract_info(url, download=False) or {}
    except Exception:
        return None
    thumbs = [t for t in found.get("thumbnails") or [] if t.get("url")]
    if thumbs:
        return max(thumbs, key=lambda t: ((t.get("width") or 0) * (t.get("height") or 0), t.get("preference") or 0))["url"]
    return found.get("thumbnail")


def overlay(url, photo, theme, position, job_dir, say=no_progress):
    """Draw the tweet at url across a photo (bytes), or across the tweet's own first photo when photo is None.
    theme: dark, dim or light. position: top, middle or bottom. Returns the path of the finished JPEG."""
    if not HAS_OVERLAY:
        raise DownloadError("Tweet over a photo needs:  pip install pillow fonttools requests")
    with cookie_copy() as cookies:
        say(None, "Reading the tweet")
        info = tweetcard.tweet_info(url, cookie_args(cookies))
        if photo is None and not info.get("photos") and not info.get("cover"):
            say(None, "Getting the video's cover picture")          # a video tweet has no photo, but it has a cover
            info["cover"] = video_cover(url, cookies)
    if photo is None:
        say(None, "Getting the tweet's own picture")
        photo = tweetoverlay.fetch_own_photo(info)
    say(None, "Drawing the card")
    return tweetoverlay.render_overlay(info, photo, os.path.join(job_dir, f"tweet-{info['id']}-on-photo.jpg"), theme, position)


def fetch(url, oid, job_dir, say=no_progress, expected_mb=None):
    """Download one chosen option into job_dir and return the file path.
    say(percent, stage) is called as the job moves along; expected_mb makes the percent more exact."""
    with cookie_copy() as cookies:
        if oid == "gallery":
            return gallery(url, job_dir, cookies, say)
        if oid == "avatar":
            if not HAS_PIC or not profilepic.avatar_target(url):
                raise DownloadError("That is not a profile link.")
            say(None, "Getting the picture")
            return profilepic.save_picture(profilepic.avatar_target(url), job_dir, cookie_args(cookies))
        if oid == "tweet":
            if not HAS_CARD:
                raise DownloadError("Tweet scrolls need:  pip install pillow fonttools requests")
            say(None, "Writing your scroll")
            found = tweetcard.tweet_info(url, cookie_args(cookies))
            return tweetcard.render_card(found, os.path.join(job_dir, f"tweet-{found['id']}.png"))
        derived = re.fullmatch(r"(fbmp3|fbgif)(\d+)", oid)
        if derived:                                        # TikTok's own best file, then the sound or a GIF made from it
            video = gallery(url, job_dir, cookies, say, only=int(derived.group(2)))
            if derived.group(1) == "fbgif":
                return make_gif(url, "gif_best", job_dir, {}, say, source=video)
            say(None, "Taking out the sound")
            out = os.path.join(job_dir, "sound.mp3")
            run_tool(["ffmpeg", "-nostdin", "-y", "-i", video, "-vn", "-codec:a", "libmp3lame", "-q:a", "0", out])
            os.remove(video)
            return out
        if oid == "cover":                                 # the video's cover picture, as one image file
            say(None, "Getting the cover picture")
            with yt_dlp.YoutubeDL(ydl_opts(cookies, {"skip_download": True, "writethumbnail": True,
                                                    "outtmpl": {"default": os.path.join(job_dir, "cover"),
                                                                "thumbnail": os.path.join(job_dir, "cover.%(ext)s")}}, url=url)) as ydl:
                ydl.download([url])
            picture = first_file(job_dir)
            return profilepic._tidy(picture, os.path.join(job_dir, "cover-picture")) if HAS_PIC else picture
        if oid.startswith("item") and oid[4:].isdigit():
            return gallery(url, job_dir, cookies, say, only=int(oid[4:]))
        opts = ydl_opts(cookies, {
            "outtmpl": os.path.join(job_dir, "%(title).100B [%(id)s].%(ext)s"),
            "format_sort": FORMAT_SORT,                       # best quality first (see the top of this file)
            **({"max_filesize": MAX_MB * 1048576} if MAX_MB else {}),
            **make_hooks(say, expected_mb * 1048576 if expected_mb else None),
        }, url=url)
        if oid.startswith("v") and oid[1:].isdigit():
            h = int(oid[1:])
            opts["format"] = (f"bestvideo*[height<={h}]+bestaudio/best[height<={h}]" if HAS_FFMPEG
                              else f"best[height<={h}]")
            opts["merge_output_format"] = "mp4"
        elif oid == "a_orig":
            opts["format"] = "bestaudio/best"
        elif oid == "a_mp3":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}]
        elif oid in GIF_PRESETS or oid == "gif_best":
            return make_gif(url, oid, job_dir, opts, say)
        else:
            raise DownloadError("Unknown option.")
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        return first_file(job_dir)
