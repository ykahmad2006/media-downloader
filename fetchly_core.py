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

import yt_dlp
from yt_dlp.utils import DownloadError

HAS_FFMPEG = shutil.which("ffmpeg") is not None
HAS_GALLERY = importlib.util.find_spec("gallery_dl") is not None
# Login cookies for sites that want one (Instagram stories). Best way: export a cookies.txt from your browser and
# save it next to this file. It is found automatically and works even while the browser is open.
# On a server, set COOKIES_FILE to its path instead.
_LOCAL_COOKIES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
COOKIES_FILE = os.environ.get("COOKIES_FILE") or (_LOCAL_COOKIES if os.path.isfile(_LOCAL_COOKIES) else None)
COOKIES_BROWSER = None  # last resort: read cookies straight from "firefox"/"chrome"/"edge". Unreliable while that browser is open
MAX_MB = int(os.environ.get("MAX_MB", "1000"))             # biggest file we will save
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


def ydl_opts(cookies=None, extra=None):
    o = {"quiet": True, "noplaylist": True, "ignoreerrors": False,
         "socket_timeout": 30, "retries": 3}
    if not ALLOW_GENERIC:
        o["allowed_extractors"] = ["default", "-generic"]  # known sites only, not "fetch any URL"
    if cookies:
        o["cookiefile"] = cookies
    elif COOKIES_BROWSER:
        o["cookiesfrombrowser"] = (COOKIES_BROWSER,)
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
        video = ext in VIDEO_EXT
        w, h = meta.get("width"), meta.get("height")
        detail = " · ".join(x for x in [(ext or "file").upper(), f"{w}x{h}" if w and h else "", "best quality"] if x)
        choice = opt(f"item{n}", "item", f"{'Video' if video else 'Photo'} {n} of {len(items)}", detail, None)
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
    info, options, failure = {}, [], None
    with cookie_copy() as cookies:
        try:
            with yt_dlp.YoutubeDL(ydl_opts(cookies)) as ydl:
                info = ydl.extract_info(url, download=False) or {}
            options = build_options(info)
        except DownloadError as error:
            failure = error
            if not HAS_GALLERY:
                raise
        has_video = any(o["kind"] == "video" for o in options)
        # Instagram stories and carousels: list every photo and video on its own
        items = []
        if "instagram.com" in url and gallery_supports(url):
            try:
                items = gallery_items(url, cookies)
            except Exception as error:
                print("Could not list the items of that post:", error, file=sys.stderr)  # shows up in the server log
                failure = failure or error
    show_items = len(items) >= 2 or (len(items) == 1 and not has_video)
    if show_items:
        options += item_options(items)
    # Photos, slideshows, carousels and stories come from gallery-dl
    if gallery_supports(url) and ("instagram.com" in url or not has_video) and not (show_items and len(items) == 1):
        what = "story" if "/stories/" in url else "post"
        if not HAS_FFMPEG:
            label, detail = f"All photos and videos in this {what}", "Saved as one ZIP"
        elif not has_video and "instagram.com" not in url:
            label, detail = "Slideshow video (MP4)", "Your photos set to the original song"
        else:
            label, detail = f"Everything in this {what} at once", "One file: a ZIP, or an MP4 slideshow if all are photos"
        options.append(opt("gallery", "zip", label, detail, None))
    if not options:
        raise failure or DownloadError("No downloadable media found.")  # keeps the real reason for the log
    return {"title": info.get("title") or "Your link", "thumb": info.get("thumbnail"),
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
    cmd = [sys.executable, "-m", "gallery_dl", "-d", job_dir,
           "--range", span, "--filesize-max", f"{MAX_MB}M", *cookie_args(cookies)]
    say(None, "Getting that item" if only else "Collecting everything in the post")
    try:
        result = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=TOOL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise DownloadError("gallery-dl took too long and was stopped.")
    files = [os.path.join(p, n) for p, _, names in os.walk(job_dir) for n in names]
    if not files:
        raise DownloadError("gallery-dl saved nothing. " + result.stderr[-200:])
    if sum(os.path.getsize(f) for f in files) > MAX_MB * 1048576:
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


def make_gif(url, oid, job_dir, opts, say=no_progress):
    width, fps, secs, _ = GIF_PRESETS[oid]
    opts.update({"format": "bestvideo*[height<=720]/best[height<=720]/best",
                 "outtmpl": os.path.join(job_dir, "source.%(ext)s")})
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    source = first_file(job_dir)
    say(None, "Making your GIF")
    out = os.path.join(job_dir, "clip.gif")
    vf = f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse"
    run_tool(["ffmpeg", "-nostdin", "-y", "-t", str(secs), "-i", source, "-vf", vf, out])
    os.remove(source)
    return out


def fetch(url, oid, job_dir, say=no_progress, expected_mb=None):
    """Download one chosen option into job_dir and return the file path.
    say(percent, stage) is called as the job moves along; expected_mb makes the percent more exact."""
    with cookie_copy() as cookies:
        if oid == "gallery":
            return gallery(url, job_dir, cookies, say)
        if oid.startswith("item") and oid[4:].isdigit():
            return gallery(url, job_dir, cookies, say, only=int(oid[4:]))
        opts = ydl_opts(cookies, {
            "outtmpl": os.path.join(job_dir, "%(title).100B [%(id)s].%(ext)s"),
            "max_filesize": MAX_MB * 1048576,
            "format_sort": ["res", "vcodec:h264", "acodec:aac"],  # top resolution, then the most compatible codec
            **make_hooks(say, expected_mb * 1048576 if expected_mb else None),
        })
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
        elif oid in GIF_PRESETS:
            return make_gif(url, oid, job_dir, opts, say)
        else:
            raise DownloadError("Unknown option.")
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        return first_file(job_dir)